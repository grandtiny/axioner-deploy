"""Internal helper for local-bootstrap.ps1.

Pushes a local SSH public key to a remote server's ~/.ssh/authorized_keys
using password authentication (one-shot bootstrap).

This is a separate file because PowerShell + embedded Python heredocs
have nightmarish escaping rules. Keeping the SSH/paramiko logic in pure
Python is far cleaner.

Usage (called from local-bootstrap.ps1):
    python scripts/_install_pubkey.py \
        --host 38.12.23.241 \
        --port 22 \
        --user root \
        --password <bootstrap-password> \
        --pubkey-file C:/Users/Axioner/.ssh/axioner_ed25519.pub
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import paramiko


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--user", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--pubkey-file", required=True)
    args = parser.parse_args()

    pub_path = Path(args.pubkey_file)
    if not pub_path.is_file():
        print(f"ERROR: public key not found: {pub_path}", file=sys.stderr)
        return 2

    pub_key = pub_path.read_text(encoding="utf-8").strip()
    if not pub_key:
        print("ERROR: public key file is empty", file=sys.stderr)
        return 2

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            args.host,
            port=args.port,
            username=args.user,
            password=args.password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
    except paramiko.AuthenticationException:
        print("ERROR: password authentication failed", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"ERROR: connection failed: {e}", file=sys.stderr)
        return 4

    # Use a heredoc-style command to avoid shell-quoting issues with the
    # public key (which contains spaces, '=', '+', '/').  We feed the key
    # via stdin to a small awk-style append-if-missing routine.
    remote_cmd = (
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
        "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
        "key=$(cat); "
        'if grep -qxF "$key" ~/.ssh/authorized_keys; then '
        '    echo "ALREADY_PRESENT"; '
        "else "
        '    echo "$key" >> ~/.ssh/authorized_keys && echo "INSTALLED"; '
        "fi"
    )

    stdin, stdout, stderr = client.exec_command(remote_cmd, timeout=15)
    stdin.write(pub_key + "\n")
    stdin.channel.shutdown_write()
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    rc = stdout.channel.recv_exit_status()
    client.close()

    if rc != 0:
        print(f"ERROR: remote command failed (rc={rc}): {err}", file=sys.stderr)
        return 5

    print(out)  # ALREADY_PRESENT or INSTALLED
    return 0


if __name__ == "__main__":
    sys.exit(main())
