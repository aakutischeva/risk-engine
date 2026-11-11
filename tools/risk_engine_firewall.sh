#!/usr/bin/env bash
# Scoped IPv4 cross-network policy for Risk Engine Docker bridge networks.
# Does not modify INPUT, OUTPUT, UFW, SSH, or Tailscale rules.
set -euo pipefail

CHAIN="RISK_ENGINE_SEG"
ACTION="${1:-}"

usage() {
  echo "Usage: sudo bash tools/risk_engine_firewall.sh {apply|show|remove}" >&2
  exit 2
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ "$ACTION" == "apply" || "$ACTION" == "show" || "$ACTION" == "remove" ]] || usage
[[ "$EUID" -eq 0 ]] || fail "Run as root with sudo."
command -v iptables >/dev/null || fail "iptables is unavailable."
iptables -w 5 -S DOCKER-USER >/dev/null || fail "Docker's DOCKER-USER chain is unavailable."

show_rules() {
  if iptables -w 5 -S "$CHAIN" >/dev/null 2>&1; then
    iptables -w 5 -S "$CHAIN"
  else
    echo "No Risk Engine firewall rules installed."
  fi
}

if [[ "$ACTION" == "show" ]]; then
  show_rules
  exit 0
fi

if [[ "$ACTION" == "remove" ]]; then
  # Remove only this project's jump and chain; preserve all other firewall rules.
  while iptables -w 5 -C DOCKER-USER -j "$CHAIN" 2>/dev/null; do
    iptables -w 5 -D DOCKER-USER -j "$CHAIN"
  done
  if iptables -w 5 -S "$CHAIN" >/dev/null 2>&1; then
    iptables -w 5 -F "$CHAIN"
    iptables -w 5 -X "$CHAIN"
  fi
  echo "Risk Engine firewall rules removed."
  exit 0
fi

command -v docker >/dev/null || fail "docker is unavailable."
command -v python3 >/dev/null || fail "python3 is unavailable."
command -v ip >/dev/null || fail "ip is unavailable."

# Resolve the actual Compose project name rather than assuming a fixed prefix.
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PROJECT="$(docker compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
[[ -n "$PROJECT" ]] || fail "Could not resolve Docker Compose project name."

validate_bridge() {
  local network="$1" expected="$2" actual ipv6
  actual="$(docker network inspect "${PROJECT}_${network}" --format '{{ index .Options "com.docker.network.bridge.name" }}')" || fail "Network ${PROJECT}_${network} missing; start Compose first."
  [[ "$actual" == "$expected" ]] || fail "Unexpected bridge for $network: $actual (expected $expected)."
  ip link show dev "$expected" >/dev/null || fail "Bridge interface $expected not found."
  ipv6="$(docker network inspect "${PROJECT}_${network}" --format '{{.EnableIPv6}}')"
  [[ "$ipv6" == "false" ]] || fail "IPv6 is enabled on $network; add corresponding IPv6 rules first."
}

validate_bridge tx_db br-re-txdb
validate_bridge tx_cache br-re-cache
validate_bridge events br-re-events
validate_bridge alert_db br-re-alertdb
validate_bridge webhook br-re-hook

# The chain is dedicated to this project. DOCKER-USER is untouched except for
# one jump; established Docker rules and any unrelated firewall rules remain.
if ! iptables -w 5 -S "$CHAIN" >/dev/null 2>&1; then
  iptables -w 5 -N "$CHAIN"
fi
iptables -w 5 -F "$CHAIN"

# Explicitly deny lateral movement from the API/transaction networks to the
# alert database and event bus; workers with both networks use local links.
for source in br-re-txdb br-re-cache; do
  iptables -w 5 -A "$CHAIN" -i "$source" -o br-re-alertdb -j DROP
  iptables -w 5 -A "$CHAIN" -i "$source" -o br-re-events -j DROP
done

# Demo webhook receiver should not initiate requests toward core services.
for destination in br-re-txdb br-re-cache br-re-events; do
  iptables -w 5 -A "$CHAIN" -i br-re-hook -o "$destination" -j DROP
done

# Alert database network must not initiate connections to the core data plane.
iptables -w 5 -A "$CHAIN" -i br-re-alertdb -o br-re-txdb -j DROP
iptables -w 5 -A "$CHAIN" -i br-re-alertdb -o br-re-cache -j DROP

# RETURN delegates every unrelated flow back to Docker's normal policy.
iptables -w 5 -A "$CHAIN" -j RETURN
if ! iptables -w 5 -C DOCKER-USER -j "$CHAIN" 2>/dev/null; then
  iptables -w 5 -I DOCKER-USER 1 -j "$CHAIN"
fi

echo "Risk Engine IPv4 cross-network DROP rules installed in $CHAIN."
echo "Permitted service flows use their explicitly shared Compose networks."
echo "The rules are runtime-only; reapply after host reboot."
