"""deploy.py - Phase 3 (execute) + Phase 4 (report) deployment CLI.

Reads the PreflightSnapshot produced by preflight.py and executes the
8-step deployment recipe over SSH. Stops on the first failure; does
NOT auto-rollback containers (only nginx config rolled back so a bad
site doesn't break the server). Pauses to ask the human when the
plan-vs-server conflict-pause protocol fires (e.g. .env still contains
placeholder values).

Example:
    # 1. preflight first
    python scripts/preflight.py --repo ... --subdomain ... --json snap.json

    # 2. then deploy
    python scripts/deploy.py --plan-file snap.json
"""

from __future__ import annotations

import argparse
import shlex
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# sys.path bootstrap (same as preflight.py — script may be run directly)
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.lib.conflict_detector import has_blocking
from scripts.lib.plan_io import (
    DeployPlan,
    PreflightSnapshot,
    SERVER_IP,
    read_snapshot,
)
from scripts.lib.ssh_client import SSHClient

# ANSI
_RESET = "\033[0m"; _RED = "\033[31m"; _GREEN = "\033[32m"
_YELLOW = "\033[33m"; _CYAN = "\033[36m"; _BOLD = "\033[1m"
def _c(color: str, text: str) -> str: return f"{color}{text}{_RESET}"


class DeployStepError(Exception):
    def __init__(self, step: str, exit_code: int, stdout: str, stderr: str) -> None:
        super().__init__(f"step '{step}' failed (rc={exit_code})")
        self.step = step; self.exit_code = exit_code
        self.stdout = stdout; self.stderr = stderr


@dataclass
class StepResult:
    name: str
    ok: bool
    note: str = ""


# --- helpers ---

def _q(s: str) -> str:
    """Shell-quote for safe interpolation into ssh commands."""
    return shlex.quote(s)


def _run(ssh: SSHClient, cmd: str, step: str, timeout: int = 120) -> str:
    """Run a remote command. Raise DeployStepError on non-zero exit.
    Returns stdout."""
    rc, out, err = ssh.run(cmd, timeout=timeout)
    if rc != 0:
        raise DeployStepError(step, rc, out, err)
    return out


def _fmt_step_header(idx: int, total: int, msg: str) -> str:
    return _c(_CYAN, f"[{idx}/{total}] {msg}")


# --- step implementations ---

def step_mkdir_target(ssh: SSHClient, plan: DeployPlan) -> str:
    # Use mkdir without -p so we fail loudly if it already exists
    # (preflight should have caught this, but defense in depth).
    rc, out, err = ssh.run(f"test -e {_q(plan.target_dir)} && echo EXISTS || echo OK")
    if "EXISTS" in out:
        raise DeployStepError("mkdir", 17,
                              f"{plan.target_dir} 已存在", "")
    _run(ssh, f"mkdir -p {_q(plan.target_dir)}", "mkdir")
    return f"created {plan.target_dir}"


def step_git_clone(ssh: SSHClient, plan: DeployPlan) -> str:
    # Clone into the target dir. Use --depth 1 unless we explicitly want
    # full history (deploy is one-shot; keep clone small for 2G server).
    cmd = f"git clone --depth 1 {_q(plan.repo_url)} {_q(plan.target_dir)}"
    out = _run(ssh, cmd, "git_clone", timeout=180)
    return f"cloned {plan.repo_url}"


def step_inspect_compose(ssh: SSHClient, plan: DeployPlan) -> tuple[str, dict]:
    """Inspect the project's own docker-compose.yml and extract:
      - service name (the first service if multiple)
      - host port (the published port the service expects)
      - container port (the target port inside the container)

    Returns (note, info_dict) where info_dict contains:
      'service', 'host_port', 'container_port', 'changed_port'

    The plan.port may be UPDATED in-place if it disagrees with what
    the project's compose declares — projects often hardcode their
    port in source, so we follow the project rather than fight it.
    """
    # Read the compose file via SSH (server has python3 + yaml).
    # We send the YAML path and let server-side python emit JSON we parse.
    compose_paths = ["docker-compose.yml", "docker-compose.yaml",
                     "compose.yml", "compose.yaml"]
    found_path = None
    for p in compose_paths:
        rc, out, _ = ssh.run(
            f"test -f {_q(plan.target_dir)}/{_q(p)} && echo FOUND || echo MISSING"
        )
        if "FOUND" in out:
            found_path = p
            break
    if not found_path:
        raise DeployStepError("inspect_compose", 1,
                              "no docker-compose.yml in repo", "")

    # Parse server-side. Output is a single JSON line we can json.loads.
    parse_script = (
        "import yaml, json, sys; "
        "d = yaml.safe_load(open(sys.argv[1])); "
        "svcs = d.get('services') or {}; "
        "name = next(iter(svcs), None); "
        "spec = svcs.get(name) or {} if name else {}; "
        "print(json.dumps({'service': name, 'ports': spec.get('ports') or []}))"
    )
    cmd = (f"cd {_q(plan.target_dir)} && "
           f"python3 -c {_q(parse_script)} {_q(found_path)}")
    rc, out, err = ssh.run(cmd, timeout=15)
    if rc != 0:
        raise DeployStepError("inspect_compose", rc, out, err)

    import json as _json
    try:
        info = _json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise DeployStepError("inspect_compose", 1,
                              f"could not parse compose output: {out!r}", err)

    service = info.get("service")
    if not service:
        raise DeployStepError("inspect_compose", 1,
                              "compose has no services", "")

    # Extract host & container port from the first port spec.
    # Port specs can be:
    #   "5000:5000"           -> host=5000, container=5000
    #   "127.0.0.1:5000:5000" -> host=5000, container=5000
    #   "5000"                -> host=auto, container=5000 (rare)
    #   {target: 5000, published: 5000, ...}  long form
    ports = info.get("ports") or []
    host_port = None
    container_port = None
    if ports:
        spec = ports[0]
        if isinstance(spec, str):
            parts = spec.split(":")
            if len(parts) == 1:
                container_port = int(parts[0])
            elif len(parts) == 2:
                host_port = int(parts[0]); container_port = int(parts[1])
            elif len(parts) == 3:
                host_port = int(parts[1]); container_port = int(parts[2])
        elif isinstance(spec, dict):
            t = spec.get("target") or spec.get("container_port")
            p = spec.get("published") or spec.get("host_port")
            if t: container_port = int(t)
            if p: host_port = int(p)

    if container_port is None:
        raise DeployStepError("inspect_compose", 1,
                              f"could not parse port from {ports!r}", "")

    # If host_port wasn't explicit, fall back to container_port (the
    # common case — project says "5000:5000").
    if host_port is None:
        host_port = container_port

    # If the project's host_port disagrees with the auto-picked plan.port,
    # follow the project. Tell the user.
    changed = host_port != plan.port
    old_port = plan.port
    plan.port = host_port

    info_out = {
        "service": service,
        "host_port": host_port,
        "container_port": container_port,
        "changed_port": changed,
        "old_port": old_port,
    }
    if changed:
        note = (f"service={service}, port {old_port} → {host_port} "
                f"(项目 compose 硬编码)")
    else:
        note = f"service={service}, port={host_port}"
    return note, info_out


def step_write_override(ssh: SSHClient, plan: DeployPlan,
                        compose_info: dict) -> str:
    """Write a docker-compose.override.yml that forces the host port
    to bind only to 127.0.0.1 (defense in depth: cloud firewall already
    limits ports, but the spec mandates localhost-only binding)."""
    service = compose_info["service"]
    host = compose_info["host_port"]
    cont = compose_info["container_port"]

    # Use compose v2's `!override` tag so our ports REPLACE the base
    # spec rather than appending. Available in compose v2.20+.
    override = (
        "services:\n"
        f"  {service}:\n"
        "    ports: !override\n"
        f"      - \"127.0.0.1:{host}:{cont}\"\n"
    )
    override_path = f"{plan.target_dir}/docker-compose.override.yml"
    ssh.write_file(override, override_path, mode=0o644)

    # Verify the merged config is what we expect.
    rc, out, err = ssh.run(
        f"cd {_q(plan.target_dir)} && docker compose config 2>&1 "
        f"| grep -E 'host_ip|published' | head -4"
    )
    if "127.0.0.1" not in out:
        raise DeployStepError("write_override", 1,
                              f"merged config missing 127.0.0.1 binding: {out}",
                              err)
    return f"override written, host_ip=127.0.0.1:{host}"


def step_prepare_env(ssh: SSHClient, plan: DeployPlan,
                     interactive: bool) -> str:
    """Ensure .env exists. If it doesn't, copy .env.example. Detect
    placeholder values and pause for human if interactive."""
    # Detect what's there
    rc, out, _ = ssh.run(
        f"cd {_q(plan.target_dir)} && "
        f"ls .env 2>/dev/null && echo HAS_ENV; "
        f"ls .env.example 2>/dev/null && echo HAS_EXAMPLE"
    )
    has_env = "HAS_ENV" in out
    has_example = "HAS_EXAMPLE" in out

    if not has_env:
        if not has_example:
            return ".env / .env.example 都不存在；继续（项目可能不需要）"
        # Copy example -> .env
        _run(ssh,
             f"cd {_q(plan.target_dir)} && cp .env.example .env",
             "copy_env")
        note = ".env 由 .env.example 复制而来"
    else:
        note = ".env 已存在"

    # Look for placeholder-ish values (heuristic, kept simple)
    rc, out, _ = ssh.run(
        f"grep -nE 'your_|_here|change.?me|<.+>' {_q(plan.target_dir)}/.env 2>/dev/null || true"
    )
    placeholders = [line for line in out.splitlines() if line.strip()]

    if placeholders and interactive:
        print()
        print(_c(_YELLOW, "  ⚠ .env 中检测到占位符："))
        for line in placeholders:
            print(f"    {line}")
        print(_c(_YELLOW, "  请在另一个终端 ssh axioner，编辑 "
                          f"{plan.target_dir}/.env，填入真实值。"))
        ans = input(_c(_BOLD, "  填好后按 Enter 继续，输入 'abort' 中止： "))
        if ans.strip().lower() == "abort":
            raise DeployStepError("prepare_env", 1, "user aborted", "")
        return note + "（已确认占位符已替换）"

    return note + (f"（检测到 {len(placeholders)} 处占位符，--yes 跳过）"
                   if placeholders else "")


def step_docker_up(ssh: SSHClient, plan: DeployPlan) -> str:
    cmd = (f"cd {_q(plan.target_dir)} && "
           f"docker compose build && docker compose up -d")
    _run(ssh, cmd, "docker_up", timeout=600)
    return "container started"


def step_write_nginx(ssh: SSHClient, plan: DeployPlan,
                     templates_dir: Path) -> str:
    template_path = templates_dir / "nginx" / "site.conf"
    if not template_path.is_file():
        raise DeployStepError("write_nginx", 1,
                              f"template missing: {template_path}", "")
    content = template_path.read_text(encoding="utf-8")
    content = (content
               .replace("{{SUBDOMAIN}}", plan.subdomain)
               .replace("{{PORT}}", str(plan.port)))

    sites_avail = f"/etc/nginx/sites-available/{plan.subdomain}"
    sites_enabl = f"/etc/nginx/sites-enabled/{plan.subdomain}"
    ssh.write_file(content, sites_avail, mode=0o644)
    _run(ssh,
         f"ln -sf {_q(sites_avail)} {_q(sites_enabl)}",
         "nginx_symlink")
    return f"wrote {sites_avail} + symlink"


def step_nginx_test_reload(ssh: SSHClient, plan: DeployPlan) -> str:
    rc, out, err = ssh.run("nginx -t 2>&1")
    if rc != 0:
        # rollback the new site file we just wrote
        ssh.run(f"rm -f /etc/nginx/sites-enabled/{_q(plan.subdomain)} "
                f"/etc/nginx/sites-available/{_q(plan.subdomain)}")
        raise DeployStepError("nginx_test", rc, out, err)
    _run(ssh, "systemctl reload nginx", "nginx_reload")
    return "nginx reloaded"


def step_certbot(ssh: SSHClient, plan: DeployPlan,
                 cert_email: str | None) -> str:
    # certbot writes 443 block + 80->301 redirect into our site config.
    if cert_email:
        email_args = f"-m {_q(cert_email)} --agree-tos --no-eff-email"
    else:
        email_args = "--register-unsafely-without-email --agree-tos"
    cmd = (f"certbot --nginx -d {_q(plan.subdomain)} "
           f"--non-interactive --redirect {email_args}")
    _run(ssh, cmd, "certbot", timeout=120)
    return "HTTPS certificate issued"


def step_health_check(ssh: SSHClient, plan: DeployPlan) -> str:
    # Give container a few seconds to settle
    time.sleep(3)
    cmd = (f"curl -sI -o /dev/null -w '%{{http_code}}' "
           f"--max-time 10 https://{_q(plan.subdomain)}/")
    rc, out, err = ssh.run(cmd, timeout=20)
    out = out.strip()
    if rc != 0:
        return _c(_YELLOW, f"⚠ curl 异常 (rc={rc})；服务可能仍在启动")
    if out.startswith("2") or out.startswith("3"):
        return f"HTTP {out}"
    if out in ("401", "403"):
        return f"HTTP {out}（受保护，正常）"
    return _c(_YELLOW, f"⚠ 非预期 HTTP {out}；建议检查容器日志")


# --- phase 4 report ---

def render_report(ssh: SSHClient, plan: DeployPlan,
                  results: list[StepResult]) -> str:
    lines = ["", _c(_BOLD, "═══ 部署完成 ═══"), ""]
    lines.append(f"  {_c(_GREEN, '✓')} https://{plan.subdomain}")

    # Container status
    rc, out, _ = ssh.run(
        f"docker ps --filter name={_q(plan.container_name)} "
        f"--format '{{{{.Status}}}} | {{{{.Image}}}}'"
    )
    if out.strip():
        lines.append(f"     容器: {plan.container_name} → {out.strip()}")
    else:
        lines.append(f"     容器: {_c(_YELLOW, '未找到 ' + plan.container_name)}")

    # Nginx
    lines.append(f"     Nginx: {plan.subdomain} → 127.0.0.1:{plan.port}")

    # Cert validity
    rc, out, _ = ssh.run(
        f"certbot certificates --cert-name {_q(plan.subdomain)} 2>/dev/null "
        f"| grep -E 'Expiry Date' | head -1"
    )
    if out.strip():
        lines.append(f"     证书: {out.strip()}")

    lines.append("")
    lines.append(_c(_BOLD, "  后续 TODO:"))
    lines.append(f"     [ ] 跑业务功能验证 https://{plan.subdomain}")
    lines.append(f"     [ ] 若用了占位符 .env，编辑 {plan.target_dir}/.env "
                 "并 docker compose up -d")
    return "\n".join(lines)


# --- main ---

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try: stream.reconfigure(encoding="utf-8")
            except Exception: pass

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan-file", required=True,
                        help="PreflightSnapshot JSON path (from preflight.py --json)")
    parser.add_argument("--yes", action="store_true",
                        help="skip the final yes/no prompt and any --env pause")
    parser.add_argument("--dry-run", action="store_true",
                        help="print steps without connecting / executing")
    parser.add_argument("--templates-dir", default=str(_PROJECT_ROOT / "templates"))
    parser.add_argument("--alias", default="axioner")
    parser.add_argument("--cert-email", default=None,
                        help="email for Let's Encrypt; default register-unsafely-without-email")
    args = parser.parse_args(argv)

    snap = read_snapshot(args.plan_file)
    plan = snap.plan

    # Reject if blocking conflicts present in snapshot
    if has_blocking(snap.conflicts):
        print(_c(_RED, "✗ 该 snapshot 含 block 冲突，不能部署："))
        for c in snap.conflicts:
            if c.severity == "block":
                print(f"    [{c.kind}] {c.message}")
        print()
        print("  解决冲突后重新跑 preflight，再 deploy。")
        return 2

    # Show plan summary
    print(_c(_BOLD, "═══ 拟执行部署 ═══"))
    print(f"  项目:    {plan.project_name}")
    print(f"  仓库:    {plan.repo_url}")
    print(f"  域名:    https://{plan.subdomain}")
    print(f"  端口:    {plan.port}")
    print(f"  目录:    {plan.target_dir}")
    print(f"  容器:    {plan.container_name}")
    print()

    if args.dry_run:
        print(_c(_YELLOW, "[dry-run] 10 个步骤：mkdir / git clone / inspect compose / "
                          "write override / prepare .env / docker compose / "
                          "write nginx / nginx -t reload / certbot / health check"))
        return 0

    if not args.yes and not plan.confirmed_by_user:
        ans = input(_c(_BOLD, "继续部署？[y/N]: ")).strip().lower()
        if ans not in ("y", "yes"):
            print("已取消")
            return 0

    # Execute. Some steps need to pass data forward (compose_info), so
    # keep this as an explicit sequence rather than a list of lambdas.
    print()
    print(_c(_BOLD, "═══ 执行中 ═══"))

    results: list[StepResult] = []
    compose_info: dict | None = None

    with SSHClient(args.alias) as ssh:
        sequence = [
            ("mkdir target dir",   lambda: step_mkdir_target(ssh, plan)),
            ("git clone",          lambda: step_git_clone(ssh, plan)),
            ("inspect compose",    lambda: step_inspect_compose(ssh, plan)),
            ("write compose override", lambda: step_write_override(ssh, plan, compose_info)),
            ("prepare .env",       lambda: step_prepare_env(ssh, plan, not args.yes)),
            ("docker compose up",  lambda: step_docker_up(ssh, plan)),
            ("write nginx config", lambda: step_write_nginx(ssh, plan, Path(args.templates_dir))),
            ("nginx -t + reload",  lambda: step_nginx_test_reload(ssh, plan)),
            ("certbot HTTPS",      lambda: step_certbot(ssh, plan, args.cert_email)),
            ("health check",       lambda: step_health_check(ssh, plan)),
        ]

        for idx, (name, fn) in enumerate(sequence, 1):
            print(_fmt_step_header(idx, len(sequence), name))
            try:
                if name == "inspect compose":
                    note, compose_info = fn()
                elif name == "write compose override":
                    if compose_info is None:
                        raise DeployStepError("write_override", 1, "compose_info missing", "")
                    note = fn()
                else:
                    note = fn()
                print(f"     {_c(_GREEN, 'OK')} {note}")
                results.append(StepResult(name, True, note))
            except DeployStepError as e:
                print(f"     {_c(_RED, 'FAIL')} (rc={e.exit_code})")
                if e.stdout.strip():
                    print(_c(_YELLOW, "     --- stdout ---"))
                    for line in e.stdout.splitlines()[-15:]:
                        print(f"     {line}")
                if e.stderr.strip():
                    print(_c(_YELLOW, "     --- stderr ---"))
                    for line in e.stderr.splitlines()[-15:]:
                        print(f"     {line}")
                results.append(StepResult(name, False, str(e)))
                print()
                print(_c(_RED, f"部署中止于步骤 [{idx}/{len(sequence)}] {name}"))
                print(_c(_YELLOW, f"已完成步骤可通过 ssh axioner 检查；"
                                  "未完成步骤未执行。"))
                return 1

        # Phase 4 - report
        print(render_report(ssh, plan, results))

    return 0


if __name__ == "__main__":
    sys.exit(main())
