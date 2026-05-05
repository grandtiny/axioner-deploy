"""preflight.py - Phase 1 deployment preflight CLI.

Reads target deployment intent from CLI args, gathers server state +
DNS resolution, builds a DeployPlan, runs conflict detection, prints a
human-readable report, and optionally writes a JSON snapshot for
deploy.py to consume.

Example:
    python scripts/preflight.py \\
        --repo https://github.com/foo/bar \\
        --subdomain bar.axioner.top \\
        --json /tmp/bar-snapshot.json
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
from dataclasses import asdict
from pathlib import Path

# Allow running this file directly (`python scripts/preflight.py ...`).
# When invoked that way, sys.path[0] is the script's own directory; we
# need the *project* root so `from scripts.lib...` resolves.
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.lib.conflict_detector import detect, has_blocking
from scripts.lib.plan_io import (
    DeployPlan,
    PreflightSnapshot,
    SERVER_IP,
    write_snapshot,
)
from scripts.lib.server_state import ServerState, collect
from scripts.lib.ssh_client import SSHClient, get_default_client

# ANSI colors (work on Windows 10+ terminals; fall back to no color via env)
_RESET = "\033[0m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_BOLD = "\033[1m"


def _c(color: str, text: str) -> str:
    return f"{color}{text}{_RESET}"


# --- helpers ---

def _infer_project_name(repo_url: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    name = repo_url.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def _resolve_dns(hostname: str) -> str | None:
    try:
        return socket.gethostbyname(hostname)
    except socket.gaierror:
        return None


def _pick_port(state: ServerState, requested: int | None,
               start: int = 3102, ceiling: int = 3999) -> tuple[int, bool]:
    """Return (port, was_auto_picked).

    If requested is given, use it as-is (caller decides what to do
    when it conflicts). If not, scan from `start` upward."""
    if requested is not None:
        return requested, False
    for p in range(start, ceiling + 1):
        if p not in state.used_ports:
            return p, True
    raise RuntimeError(f"no free port in {start}..{ceiling}")


def _validate_subdomain(subdomain: str) -> None:
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", subdomain):
        raise SystemExit(
            f"invalid subdomain: {subdomain!r} (expected like 'name.axioner.top')"
        )


# --- report rendering ---

def _render_report(plan: DeployPlan, state: ServerState,
                   dns_to: str | None, conflicts: list, port_auto: bool) -> str:
    lines: list[str] = []
    lines.append(_c(_BOLD, "═══ 部署预检报告 ═══"))
    lines.append("")
    lines.append(f"  拟部署:    {_c(_CYAN, plan.project_name)}")
    lines.append(f"  仓库:      {plan.repo_url}")

    dns_marker = (
        _c(_GREEN, f"✓ 解析到 {dns_to}") if dns_to == SERVER_IP else
        _c(_RED, f"✗ 解析到 {dns_to}（应为 {SERVER_IP}）") if dns_to else
        _c(_RED, "✗ 未解析")
    )
    lines.append(f"  域名:      {plan.subdomain}    DNS: {dns_marker}")

    port_note = "（自动选）" if port_auto else "（你指定）"
    port_status = (
        _c(_RED, "占用") if plan.port in state.used_ports else _c(_GREEN, "空闲")
    )
    lines.append(f"  端口:      {plan.port} {port_note}  [{port_status}]")
    lines.append(f"  目录:      {plan.target_dir}")
    lines.append("")

    mem_pct = state.mem_avail_mb * 100 // max(state.mem_total_mb, 1)
    disk_pct = state.disk_avail_gb * 100 // max(state.disk_total_gb, 1)
    lines.append(f"  内存余量:  {state.mem_avail_mb}/{state.mem_total_mb} MB ({mem_pct}%)")
    lines.append(f"  磁盘余量:  {state.disk_avail_gb}/{state.disk_total_gb} GB ({disk_pct}%)")
    lines.append(f"  Swap:      {state.swap_total_mb} MB")
    lines.append("")
    lines.append(f"  已用端口:  {state.used_ports}")
    lines.append(f"  已部署:    {state.project_dirs or '(无)'}")
    lines.append("")

    lines.append(_c(_BOLD, "── 冲突清单 ──"))
    if not conflicts:
        lines.append(f"  {_c(_GREEN, '✓ 无冲突')}")
    else:
        for c in conflicts:
            sev = (
                _c(_RED, "✗ block") if c.severity == "block" else _c(_YELLOW, "⚠ warn")
            )
            lines.append(f"  {sev}  [{c.kind}] {c.message}")
            lines.append(f"           → {c.suggested_action}")
    lines.append("")

    if has_blocking(conflicts):
        lines.append(_c(_RED, "BLOCKED：解决上述 block 冲突后再跑 deploy.py"))
    else:
        lines.append(_c(_GREEN, "OK：可以执行 deploy.py"))
    return "\n".join(lines)


# --- main ---

def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 on stdout/stderr so checkmarks and CJK render on Windows
    # GBK consoles. Safe to call repeatedly.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True,
                        help="git repo URL, e.g. https://github.com/foo/bar.git")
    parser.add_argument("--subdomain", required=True,
                        help="full subdomain, e.g. bar.axioner.top")
    parser.add_argument("--port", type=int, default=None,
                        help="explicit port; auto-pick from 3102+ if omitted")
    parser.add_argument("--project-name", default=None,
                        help="override project name (default: derive from repo URL)")
    parser.add_argument("--json", default=None,
                        help="path to write PreflightSnapshot JSON (for deploy.py)")
    parser.add_argument("--alias", default="axioner",
                        help="SSH config alias (default: axioner)")
    args = parser.parse_args(argv)

    _validate_subdomain(args.subdomain)
    project = _infer_project_name(args.repo, args.project_name)

    print(_c(_CYAN, f"[*] connecting to {args.alias}..."), file=sys.stderr)
    with SSHClient(args.alias) as ssh:
        print(_c(_CYAN, "[*] collecting server state..."), file=sys.stderr)
        state = collect(ssh)

    print(_c(_CYAN, f"[*] resolving DNS for {args.subdomain}..."), file=sys.stderr)
    dns_to = _resolve_dns(args.subdomain)

    port, port_auto = _pick_port(state, args.port)

    plan = DeployPlan(
        project_name=project,
        repo_url=args.repo,
        subdomain=args.subdomain,
        port=port,
        target_dir=f"/opt/{project}",
        container_name=f"{project}-app-1",
    )

    conflicts = detect(plan, state, dns_resolves_to=dns_to)

    print(_render_report(plan, state, dns_to, conflicts, port_auto))

    if args.json:
        snap = PreflightSnapshot(
            plan=plan,
            server_state=asdict(state),
            dns_resolves_to=dns_to,
            conflicts=conflicts,
        )
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        write_snapshot(snap, args.json)
        print(file=sys.stderr)
        print(_c(_CYAN, f"[*] snapshot written: {args.json}"), file=sys.stderr)

    # Exit code: 0 = ok or warn-only, 2 = blocking conflicts
    return 2 if has_blocking(conflicts) else 0


if __name__ == "__main__":
    sys.exit(main())
