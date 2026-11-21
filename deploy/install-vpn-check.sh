#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
test "$(id -u)" -eq 0
test -x /usr/local/bin/sjtuvpn
systemctl cat sjtuvpn.service >/dev/null
install -d -m 755 /usr/local/lib/quantlens
install -m 644 ensure_sjtuvpn.py /usr/local/lib/quantlens/ensure_sjtuvpn.py
install -m 644 quantlens-vpn-check.service quantlens-vpn-check.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now quantlens-vpn-check.timer
systemctl start quantlens-vpn-check.service
