"""Collect runtime state of the deploy target server.

Single SSH session runs a batch of commands; output is parsed back into
a ServerState dataclass that downstream code (conflict_detector,
preflight) consumes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .ssh_client import SSHClient

# Marker we use to delimit sections in the batched shell output. Picked
# to be unlikely to appear in any normal command output.
_MARK = "===AXIONER:SECTION:"


@dataclass
class ContainerInfo:
    name: str
    status: str
    image: str


@dataclass
class ServerState:
    mem_total_mb: int
    mem_avail_mb: int
    disk_total_gb: int
    disk_avail_gb: int
    swap_total_mb: int
    used_ports: list[int] = field(default_factory=list)
    project_dirs: list[str] = field(default_factory=list)
    nginx_sites: list[str] = field(default_factory=list)
    docker_containers: list[ContainerInfo] = field(default_factory=list)


# Single shell script that emits each section after a marker line.
# We use `set +e` because some commands legitimately exit non-zero
# (e.g. `ls /opt | grep -v containerd` if /opt is empty). We tolerate
# missing sections gracefully on the parse side.
_COLLECT_CMD = rf"""
set +e
echo "{_MARK}MEMSWAP"
free -m | awk '/^Mem:/ {{print "mem_total " $2; print "mem_avail " $7}}
                /^Swap:/ {{print "swap_total " $2}}'
echo "{_MARK}DISK"
df -BG --output=size,avail / | tail -n 1
echo "{_MARK}PORTS"
# Listening TCP ports (extract port number from the local-address column)
ss -tln 2>/dev/null | awk 'NR>1 {{n=split($4,a,":"); print a[n]}}' | sort -un
echo "{_MARK}OPT"
# Project dirs in /opt, excluding system entries
ls -1 /opt 2>/dev/null | grep -vE '^(containerd|lost\+found)$' || true
echo "{_MARK}NGINX"
ls -1 /etc/nginx/sites-enabled 2>/dev/null || true
echo "{_MARK}DOCKER"
docker ps -a --format '{{{{json .}}}}' 2>/dev/null || true
echo "{_MARK}END"
"""


def _split_sections(output: str) -> dict[str, list[str]]:
    """Split the batched output into sections keyed by marker name."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in output.splitlines():
        if line.startswith(_MARK):
            current = line[len(_MARK):]
            if current != "END":
                sections[current] = []
        elif current is not None and current != "END":
            sections[current].append(line)
    return sections


def _parse_memswap(lines: list[str]) -> tuple[int, int, int]:
    mem_total = mem_avail = swap_total = 0
    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        key, val = parts
        try:
            v = int(val)
        except ValueError:
            continue
        if key == "mem_total":
            mem_total = v
        elif key == "mem_avail":
            mem_avail = v
        elif key == "swap_total":
            swap_total = v
    return mem_total, mem_avail, swap_total


def _parse_disk(lines: list[str]) -> tuple[int, int]:
    """Parse `df -BG --output=size,avail`. Values look like '20G' '15G'."""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            size = int(re.sub(r"\D", "", parts[0]) or 0)
            avail = int(re.sub(r"\D", "", parts[1]) or 0)
            return size, avail
    return 0, 0


def _parse_ports(lines: list[str]) -> list[int]:
    out: list[int] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            p = int(line)
        except ValueError:
            continue
        if 0 < p < 65536 and p not in out:
            out.append(p)
    return sorted(out)


def _parse_listing(lines: list[str]) -> list[str]:
    return [line.strip() for line in lines if line.strip()]


def _parse_docker(lines: list[str]) -> list[ContainerInfo]:
    out: list[ContainerInfo] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(ContainerInfo(
            name=obj.get("Names", ""),
            status=obj.get("Status", ""),
            image=obj.get("Image", ""),
        ))
    return out


def collect(client: SSHClient) -> ServerState:
    """Run the collection script and return a populated ServerState."""
    rc, out, err = client.run(_COLLECT_CMD, timeout=30)
    if rc != 0:
        raise RuntimeError(
            f"server_state.collect: SSH command failed (rc={rc}): {err}"
        )

    sections = _split_sections(out)

    mem_total, mem_avail, swap_total = _parse_memswap(sections.get("MEMSWAP", []))
    disk_total, disk_avail = _parse_disk(sections.get("DISK", []))

    return ServerState(
        mem_total_mb=mem_total,
        mem_avail_mb=mem_avail,
        disk_total_gb=disk_total,
        disk_avail_gb=disk_avail,
        swap_total_mb=swap_total,
        used_ports=_parse_ports(sections.get("PORTS", [])),
        project_dirs=_parse_listing(sections.get("OPT", [])),
        nginx_sites=_parse_listing(sections.get("NGINX", [])),
        docker_containers=_parse_docker(sections.get("DOCKER", [])),
    )
