#!/usr/bin/env bash
# LightSpeed lease host installer. Run ONCE on the VM, as root, from the unpacked
# bundle directory (contains stack/ and lease/):
#     sudo ./lease/install.sh [--slots 50] [--active 40] [--host lightspeed.breachpoint.live] [--no-build]
# Idempotent: safe to re-run (it will not wipe an existing lease state).
set -euo pipefail

SLOTS=50; ACTIVE=40; HOST="lightspeed.breachpoint.live"; BUILD=1; BASE_PORT=4001; HOURS=4
while [ $# -gt 0 ]; do case "$1" in
  --slots) SLOTS="$2"; shift 2;; --active) ACTIVE="$2"; shift 2;; --host) HOST="$2"; shift 2;;
  --base-port) BASE_PORT="$2"; shift 2;; --hours) HOURS="$2"; shift 2;; --no-build) BUILD=0; shift;;
  *) echo "unknown arg $1" >&2; exit 2;; esac; done

HERE="$(cd "$(dirname "$0")" && pwd)"; BUNDLE="$(dirname "$HERE")"
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker not installed" >&2; exit 1; }

echo "== 1/7 egress guard (block metadata API + VPC from containers; keep DNS + internet)"
cat >/etc/systemd/system/docker-egress-guard.service <<'UNIT'
[Unit]
Description=Block container access to GCP metadata API and VPC (DNS to the GCE resolver stays allowed)
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c "iptables -C DOCKER-USER -d 169.254.0.0/16 -j DROP 2>/dev/null || iptables -I DOCKER-USER -d 169.254.0.0/16 -j DROP; iptables -C DOCKER-USER -d 10.0.0.0/8 -j DROP 2>/dev/null || iptables -I DOCKER-USER -d 10.0.0.0/8 -j DROP; for p in udp tcp; do iptables -C DOCKER-USER -d 169.254.169.254 -p $p --dport 53 -j ACCEPT 2>/dev/null || iptables -I DOCKER-USER -d 169.254.169.254 -p $p --dport 53 -j ACCEPT; done"
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable --now docker-egress-guard

echo "== 2/7 docker address pools (each compose stack needs a network; default pools run out after ~30)"
# 172.20.0.0/14 as /24s = 1024 networks. Must stay OUT of 10.0.0.0/8 (the egress guard drops it,
# which would also break traffic between containers on one bridge).
python3 - <<'PY'
import json, os
p = "/etc/docker/daemon.json"
d = json.load(open(p)) if os.path.exists(p) and os.path.getsize(p) else {}
want = [{"base": "172.20.0.0/14", "size": 24}]
if d.get("default-address-pools") != want:
    d["default-address-pools"] = want
    json.dump(d, open(p, "w"), indent=2)
    print("daemon.json updated; restarting docker")
    os.system("systemctl restart docker")
else:
    print("daemon.json already set")
PY
sleep 3; systemctl is-active docker >/dev/null && systemctl restart docker-egress-guard

echo "== 3/7 stack + lsctl"
mkdir -p /opt/lightspeed /var/lib/lightspeed/slots
rsync -a --delete "$BUNDLE/stack/" /opt/lightspeed/stack/
cp "$HERE/docker-compose.images.yml" "$HERE/docker-compose.datastores.yml" /opt/lightspeed/stack/
install -m 755 "$HERE/lsctl" /usr/local/bin/lsctl
chmod 700 /var/lib/lightspeed /var/lib/lightspeed/slots

echo "== 4/7 reaper timer (releases expired leases every minute)"
cat >/etc/systemd/system/lightspeed-reaper.service <<'UNIT'
[Unit]
Description=LightSpeed lease reaper
[Service]
Type=oneshot
ExecStart=/usr/local/bin/lsctl reap --quiet
UNIT
cat >/etc/systemd/system/lightspeed-reaper.timer <<'UNIT'
[Unit]
Description=Run the LightSpeed lease reaper every minute
[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=5s
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload && systemctl enable --now lightspeed-reaper.timer

echo "== 5/7 decorative landing page (static; nginx serves it, no per-slot web port)"
mkdir -p /var/www/lightspeed
cp /opt/lightspeed/stack/services/frontdoor/src/public/index.html /var/www/lightspeed/index.html
chmod -R a+rX /var/www/lightspeed

echo "== 6/7 build images ONCE (shared by every slot)"
if [ "$BUILD" = 1 ]; then
  cd /opt/lightspeed/stack
  docker compose -f docker-compose.yml -f docker-compose.images.yml build ledger gateway-a frontdoor webhook-consumer webhook-broker
else
  echo "(skipped: --no-build)"
fi

echo "== 7/7 lease state"
if [ -f /var/lib/lightspeed/state.json ]; then
  echo "state.json exists - keeping it (existing leases untouched)"
else
  lsctl init --slots "$SLOTS" --active "$ACTIVE" --base-port "$BASE_PORT" --hours "$HOURS" --host "$HOST"
fi
echo; echo "done. Next: lsctl status | lsctl lease \"Team Name\""
