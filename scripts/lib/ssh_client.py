"""SSH client wrapping paramiko, reading connection info from ~/.ssh/config.

Usage:
    from scripts.lib.ssh_client import SSHClient

    with SSHClient("axioner") as client:
        rc, out, err = client.run("uname -a")
        if rc == 0:
            print(out)
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

import paramiko


class SSHConfigError(Exception):
    """Raised when ~/.ssh/config does not contain the requested alias."""


class SSHCommandError(Exception):
    """Raised when a remote command exits non-zero (only when check=True)."""

    def __init__(self, cmd: str, exit_code: int, stdout: str, stderr: str) -> None:
        super().__init__(
            f"remote command failed (rc={exit_code}): {cmd}\n"
            f"--- stderr ---\n{stderr}"
        )
        self.cmd = cmd
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class _Resolved:
    hostname: str
    port: int
    user: str
    identity_file: str


def _resolve_alias(alias: str) -> _Resolved:
    """Resolve an alias from ~/.ssh/config to concrete connection params."""
    config_path = Path.home() / ".ssh" / "config"
    if not config_path.is_file():
        raise SSHConfigError(
            f"no SSH config at {config_path}; run scripts/local-bootstrap.ps1 first"
        )

    cfg = paramiko.SSHConfig()
    with config_path.open(encoding="utf-8") as f:
        cfg.parse(f)

    entry = cfg.lookup(alias)
    # paramiko returns 'hostname' = alias if no Host match; detect that.
    if entry.get("hostname") == alias and "user" not in entry and "identityfile" not in entry:
        raise SSHConfigError(
            f"alias '{alias}' not found in {config_path}"
        )

    identity_files = entry.get("identityfile") or []
    if isinstance(identity_files, str):
        identity_files = [identity_files]
    if not identity_files:
        raise SSHConfigError(
            f"alias '{alias}' has no IdentityFile; cannot use key auth"
        )
    # Expand ~ in identity file path
    identity_file = os.path.expanduser(identity_files[0])

    return _Resolved(
        hostname=entry.get("hostname", alias),
        port=int(entry.get("port", 22)),
        user=entry.get("user") or os.environ.get("USERNAME", "root"),
        identity_file=identity_file,
    )


class SSHClient:
    """Context-managed SSH client backed by paramiko."""

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self._resolved = _resolve_alias(alias)
        self._client: paramiko.SSHClient | None = None

    # --- context manager ---

    def __enter__(self) -> "SSHClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- lifecycle ---

    def connect(self) -> None:
        if self._client is not None:
            return
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            self._resolved.hostname,
            port=self._resolved.port,
            username=self._resolved.user,
            key_filename=self._resolved.identity_file,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    # --- operations ---

    def run(
        self,
        cmd: str,
        timeout: int = 60,
        check: bool = False,
    ) -> tuple[int, str, str]:
        """Execute a command remotely.

        Returns (exit_code, stdout, stderr). If check=True, raises
        SSHCommandError on non-zero exit.
        """
        if self._client is None:
            self.connect()
        assert self._client is not None
        _, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        if check and rc != 0:
            raise SSHCommandError(cmd, rc, out, err)
        return rc, out, err

    def put_file(self, local_path: str, remote_path: str) -> None:
        """Upload a local file via SFTP."""
        if self._client is None:
            self.connect()
        assert self._client is not None
        with self._client.open_sftp() as sftp:
            sftp.put(local_path, remote_path)

    def write_file(self, content: str, remote_path: str, mode: int | None = None) -> None:
        """Write a string to a remote path via SFTP."""
        if self._client is None:
            self.connect()
        assert self._client is not None
        with self._client.open_sftp() as sftp:
            with sftp.open(remote_path, "w") as f:
                f.write(content)
            if mode is not None:
                sftp.chmod(remote_path, mode)


def get_default_client() -> SSHClient:
    """Return an SSHClient for the default alias 'axioner'."""
    return SSHClient("axioner")


def quote(arg: str) -> str:
    """Shell-quote an argument for safe interpolation into a remote command."""
    return shlex.quote(arg)
