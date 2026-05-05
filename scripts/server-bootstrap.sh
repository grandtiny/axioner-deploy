#!/usr/bin/env bash
# axioner-deploy server bootstrap
#
# One-shot server-side configuration:
#   1. Ensure 1G swap is configured and persistent (/etc/fstab)
#   2. Ensure PubkeyAuthentication=yes in sshd (and reload)
#
# Idempotent: re-running will not duplicate work or break state.
# Run as root.

set -euo pipefail

SWAP_FILE="/swapfile"
SWAP_SIZE_MB=1024
SSHD_CONFIG="/etc/ssh/sshd_config"

log()  { printf '\033[0;36m[+]\033[0m %s\n' "$*"; }
ok()   { printf '    \033[0;32mOK:\033[0m %s\n' "$*"; }
skip() { printf '    \033[0;33mSKIP:\033[0m %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: must run as root" >&2
    exit 1
fi

echo "=== axioner-deploy server bootstrap ==="
echo

# --- 1. swap ---
log "Configure ${SWAP_SIZE_MB}M swap"
# Idempotency: if our swap file is already active, skip.
# (Don't compare `free -m` totals — it rounds down by a few MB.)
if swapon --show=NAME --noheadings 2>/dev/null | grep -qx "$SWAP_FILE"; then
    skip "$SWAP_FILE already active"
else
    if [ -f "$SWAP_FILE" ]; then
        log "  found stale $SWAP_FILE, disabling and removing"
        swapoff "$SWAP_FILE" 2>/dev/null || true
        rm -f "$SWAP_FILE"
    fi
    log "  creating ${SWAP_SIZE_MB}M swap file"
    fallocate -l "${SWAP_SIZE_MB}M" "$SWAP_FILE"
    chmod 600 "$SWAP_FILE"
    mkswap "$SWAP_FILE" >/dev/null
    swapon "$SWAP_FILE"

    if ! grep -qE "^${SWAP_FILE}[[:space:]]" /etc/fstab; then
        echo "${SWAP_FILE} none swap sw 0 0" >> /etc/fstab
    fi
    ok "swap enabled and persisted"
fi

# --- 2. PubkeyAuthentication ---
log "Ensure PubkeyAuthentication=yes in $SSHD_CONFIG"
# Check current effective value (last matching uncommented directive wins).
current=$(grep -E '^[[:space:]]*PubkeyAuthentication[[:space:]]+' "$SSHD_CONFIG" \
          | tail -n 1 \
          | awk '{print tolower($2)}' || true)

if [ "$current" = "yes" ]; then
    skip "PubkeyAuthentication already 'yes'"
else
    backup="${SSHD_CONFIG}.bak.$(date +%Y%m%d%H%M%S)"
    cp "$SSHD_CONFIG" "$backup"
    log "  backup saved: $backup"

    if grep -qE '^[[:space:]]*#?[[:space:]]*PubkeyAuthentication[[:space:]]+' "$SSHD_CONFIG"; then
        # Update existing line (commented or otherwise)
        sed -i -E 's/^[[:space:]]*#?[[:space:]]*PubkeyAuthentication[[:space:]]+.*/PubkeyAuthentication yes/' "$SSHD_CONFIG"
    else
        # Append at end of file
        printf '\nPubkeyAuthentication yes\n' >> "$SSHD_CONFIG"
    fi

    log "  validating sshd config syntax"
    sshd -t

    log "  reloading sshd"
    systemctl reload ssh 2>/dev/null || systemctl reload sshd
    ok "PubkeyAuthentication enabled"
fi

echo
echo "=== bootstrap complete ==="
echo
log "Final state:"
echo "  swap:"
swapon --show | sed 's/^/    /'
free -h | grep '^Swap:' | sed 's/^/    /'
echo "  sshd:"
echo "    PubkeyAuthentication: $(grep -E '^[[:space:]]*PubkeyAuthentication' "$SSHD_CONFIG" | tail -n 1)"
