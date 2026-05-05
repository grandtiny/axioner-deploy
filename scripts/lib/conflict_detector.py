"""Detect deployment conflicts between a desired DeployPlan and the
current ServerState. Six conflict categories handled at preflight time;
three more (env, private_repo, certbot) are surfaced by deploy.py at
runtime when they fail.
"""

from __future__ import annotations

import ipaddress

from .plan_io import Conflict, DeployPlan, SERVER_IP
from .server_state import ServerState


# Thresholds (centralized so they're easy to tune)
MEM_WARN_MB = 200          # warn if available memory < this
DISK_BLOCK_GB = 1          # block if available disk < this


# Cloudflare published IPv4 ranges (https://www.cloudflare.com/ips-v4/).
# Used to recognize when a subdomain is behind Cloudflare's proxy
# (orange-cloud) — the resolved IP won't match our origin, but
# certbot/HTTP still tunnel through if Cloudflare's DNS points at us.
# We treat these as `warn` instead of `block`.
_CLOUDFLARE_V4_RANGES = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
    "103.31.4.0/22",   "141.101.64.0/18", "108.162.192.0/18",
    "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
    "198.41.128.0/17", "162.158.0.0/15",  "104.16.0.0/13",
    "104.24.0.0/14",   "172.64.0.0/13",   "131.0.72.0/22",
)
_CLOUDFLARE_NETWORKS = tuple(
    ipaddress.ip_network(r) for r in _CLOUDFLARE_V4_RANGES
)


def _is_cloudflare_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _CLOUDFLARE_NETWORKS)


def detect(
    plan: DeployPlan,
    state: ServerState,
    dns_resolves_to: str | None = None,
    expected_server_ip: str = SERVER_IP,
) -> list[Conflict]:
    """Return a list of conflicts that apply to the given plan/state.

    Empty list = good to proceed. Any conflict with severity='block' must
    be resolved (or explicitly overridden by the user) before deploy.
    """
    conflicts: list[Conflict] = []

    # --- DNS ---
    # Three buckets:
    #   (a) resolves to expected origin IP   -> no conflict
    #   (b) resolves to Cloudflare proxy IP  -> warn (proxied; certbot still works)
    #   (c) unresolved or wrong elsewhere    -> block
    if dns_resolves_to is None:
        conflicts.append(Conflict(
            kind="dns", severity="block",
            message=f"{plan.subdomain} 未解析到任何 IP",
            suggested_action=(
                f"在 DNS 后台为 {plan.subdomain} 添加 A 记录指向 {expected_server_ip}"
            ),
        ))
    elif dns_resolves_to == expected_server_ip:
        pass  # ok
    elif _is_cloudflare_ip(dns_resolves_to):
        conflicts.append(Conflict(
            kind="dns", severity="warn",
            message=(
                f"{plan.subdomain} 解析到 Cloudflare 代理 IP {dns_resolves_to}"
                f"（非源站 {expected_server_ip}）"
            ),
            suggested_action=(
                "确认 Cloudflare 后台 A 记录指向 38.12.23.241。"
                "若 certbot 失败，可临时关闭橙云代理（变灰云）后重试，"
                "拿到证书后再开回。"
            ),
        ))
    else:
        conflicts.append(Conflict(
            kind="dns", severity="block",
            message=f"{plan.subdomain} 解析到 {dns_resolves_to}，应为 {expected_server_ip}",
            suggested_action=(
                f"在 DNS 后台修改 A 记录指向 {expected_server_ip}"
            ),
        ))

    # --- Port ---
    if plan.port in state.used_ports:
        conflicts.append(Conflict(
            kind="port", severity="block",
            message=f"端口 {plan.port} 已被占用",
            suggested_action=(
                f"换一个空闲端口（当前已用：{state.used_ports}）"
            ),
        ))

    # --- Project dir ---
    if plan.project_name in state.project_dirs:
        conflicts.append(Conflict(
            kind="dir", severity="block",
            message=f"目录 /opt/{plan.project_name} 已存在",
            suggested_action="若是更新，用 git pull + docker compose up -d；若是新建，换项目名",
        ))

    # --- Nginx site ---
    if plan.subdomain in state.nginx_sites:
        conflicts.append(Conflict(
            kind="nginx", severity="block",
            message=f"Nginx site config 已存在: /etc/nginx/sites-enabled/{plan.subdomain}",
            suggested_action="若要覆盖，先 rm 该 site 文件并 reload nginx",
        ))

    # --- Memory ---
    if state.mem_avail_mb < MEM_WARN_MB:
        conflicts.append(Conflict(
            kind="memory", severity="warn",
            message=f"可用内存只剩 {state.mem_avail_mb} MB（< {MEM_WARN_MB} MB）",
            suggested_action="检查 docker stats 看哪个容器占内存大；考虑停掉再部署",
        ))

    # --- Disk ---
    if state.disk_avail_gb < DISK_BLOCK_GB:
        conflicts.append(Conflict(
            kind="disk", severity="block",
            message=f"可用磁盘只剩 {state.disk_avail_gb} GB（< {DISK_BLOCK_GB} GB）",
            suggested_action="docker system prune；清理 /var/log；删旧镜像",
        ))

    return conflicts


def has_blocking(conflicts: list[Conflict]) -> bool:
    """Convenience: any block-severity conflict in the list?"""
    return any(c.severity == "block" for c in conflicts)
