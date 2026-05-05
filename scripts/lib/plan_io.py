"""Data structures for deployment plans and serialization helpers.

A DeployPlan is what preflight produces and deploy consumes. JSON is
the on-disk format so it's easy to hand-edit and inspect.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal


# Server we deploy to. Centralized here so tests can monkey-patch and
# downstream scripts can import.
SERVER_IP = "38.12.23.241"


@dataclass
class DeployPlan:
    project_name: str
    repo_url: str
    subdomain: str          # full subdomain, e.g. "bar.axioner.top"
    port: int
    target_dir: str         # e.g. "/opt/bar"
    container_name: str     # e.g. "bar-app-1"
    confirmed_by_user: bool = False


ConflictKind = Literal[
    "dns", "port", "dir", "memory", "disk", "nginx",
    "env", "private_repo", "certbot",
]
ConflictSeverity = Literal["block", "warn"]


@dataclass
class Conflict:
    kind: ConflictKind
    severity: ConflictSeverity
    message: str
    suggested_action: str


@dataclass
class PreflightSnapshot:
    """Bundles plan + state + dns + conflicts so deploy.py doesn't
    need to re-collect (avoids redundant SSH and ensures deploy uses
    the same plan the user approved)."""

    plan: DeployPlan
    server_state: dict       # ServerState as dict (avoids dual-import)
    dns_resolves_to: str | None
    conflicts: list[Conflict] = field(default_factory=list)


# --- DeployPlan IO ---

def plan_to_json(plan: DeployPlan) -> str:
    return json.dumps(asdict(plan), ensure_ascii=False, indent=2)


def plan_from_json(s: str) -> DeployPlan:
    return DeployPlan(**json.loads(s))


def write_plan(plan: DeployPlan, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(plan_to_json(plan))


def read_plan(path: str) -> DeployPlan:
    with open(path, encoding="utf-8") as f:
        return plan_from_json(f.read())


# --- Snapshot IO (used by deploy.py to read preflight output) ---

def snapshot_to_json(snap: PreflightSnapshot) -> str:
    return json.dumps({
        "plan": asdict(snap.plan),
        "server_state": snap.server_state,
        "dns_resolves_to": snap.dns_resolves_to,
        "conflicts": [asdict(c) for c in snap.conflicts],
    }, ensure_ascii=False, indent=2)


def snapshot_from_json(s: str) -> PreflightSnapshot:
    obj = json.loads(s)
    return PreflightSnapshot(
        plan=DeployPlan(**obj["plan"]),
        server_state=obj["server_state"],
        dns_resolves_to=obj.get("dns_resolves_to"),
        conflicts=[Conflict(**c) for c in obj.get("conflicts", [])],
    )


def write_snapshot(snap: PreflightSnapshot, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(snapshot_to_json(snap))


def read_snapshot(path: str) -> PreflightSnapshot:
    with open(path, encoding="utf-8") as f:
        return snapshot_from_json(f.read())
