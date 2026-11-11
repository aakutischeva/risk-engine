# Task 4 — Docker network segmentation and scoped firewall

## Policy

| Docker network | Members | Necessary traffic |
| --- | --- | --- |
| `tx_db` | api, postgres, migrate, outbox-publisher | API / publisher to main PostgreSQL |
| `tx_cache` | api, redis | API to Redis |
| `events` | outbox-publisher, rabbitmq, alert-notifier | AMQP publisher and consumer |
| `alert_db` | alert-notifier, alert-postgres, alert-migrate, alert-dispatcher | Alert database operations |
| `webhook` | alert-dispatcher, notification-receiver | Signed HTTP webhook |

There is no general-purpose `default` network for these services. The API
is not joined to `events` or `alert_db`; the webhook receiver is only joined
to `webhook`. Main and alert databases retain separate named volumes.

The API and RabbitMQ management UI keep their existing **127.0.0.1-only**
host port bindings. SSH and Tailscale operate on the host and are untouched.

Docker creates isolation rules for separate user-defined bridge networks.
`risk_engine_firewall.sh` adds explicit IPv4 cross-bridge `DROP` rules in its
own `RISK_ENGINE_SEG` chain, called from `DOCKER-USER` before Docker's normal
forwarding rules. Intended allowed traffic stays **within shared bridges**;
the script does not install blanket ACCEPT rules that would bypass Docker's
own security policy.

## Safe rollout

1. Make a VirtualBox snapshot or independent offline backup **first**. Do not
   change networking if the previous snapshot operation is still failing and
   no other backup exists.
2. In `~/risk-engine`, inspect the patch dry run and `docker compose config`.
3. Run `sudo docker compose up -d`. Compose may recreate affected containers;
   **do not use `down -v`**, which would remove stored data volumes.
4. Check `sudo docker compose ps -a`. Both migration jobs should be `Exited (0)`;
   main API, postgres, redis, rabbitmq, alert-postgres and receiver healthy.
5. Only then run `sudo bash tools/risk_engine_firewall.sh apply`.
6. Inspect with `sudo bash tools/risk_engine_firewall.sh show`.

Do not enable UFW as part of this task. Docker published-port filtering can
bypass UFW; this policy uses Docker's `DOCKER-USER` iptables chain directly.

## Allowed connections: test after deployment

```bash
sudo docker compose exec -T api python -c 'import socket; socket.create_connection(("postgres",5432),3).close(); print("ALLOW api -> postgres")'
sudo docker compose exec -T api python -c 'import socket; socket.create_connection(("redis",6379),3).close(); print("ALLOW api -> redis")'
sudo docker compose exec -T outbox-publisher python -c 'import socket; socket.create_connection(("rabbitmq",5672),3).close(); print("ALLOW publisher -> rabbitmq")'
sudo docker compose exec -T alert-notifier python -c 'import socket; socket.create_connection(("alert-postgres",5432),3).close(); print("ALLOW notifier -> alert DB")'
sudo docker compose exec -T alert-dispatcher python -c 'import socket; socket.create_connection(("notification-receiver",8080),3).close(); print("ALLOW dispatcher -> receiver")'
```

## Denied connections: verify network isolation

These test DNS isolation (each prints `DENIED`):

```bash
sudo docker compose exec -T api python -c 'import socket; exec("try:\n socket.getaddrinfo(\"alert-postgres\",5432)\n raise SystemExit(\"UNEXPECTED: resolved alert DB\")\nexcept socket.gaierror:\n print(\"DENIED api -> alert DB\")")'
sudo docker compose exec -T notification-receiver python -c 'import socket; exec("try:\n socket.getaddrinfo(\"postgres\",5432)\n raise SystemExit(\"UNEXPECTED: resolved main DB\")\nexcept socket.gaierror:\n print(\"DENIED receiver -> main DB\")")'
```

To verify **packet filtering even by direct IP address**, first complete the
allowed-connection tests above. Then try to reach the alert database from the
API (expect `DENIED` after a two-second timeout):

```bash
ALERT_IP="$(sudo docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' risk-engine-alert-postgres)"
if sudo docker compose exec -T api python -c "import socket; socket.create_connection(('$ALERT_IP',5432),2)"; then
  echo 'UNEXPECTED: API reached alert database'
else
  echo 'DENIED: API cannot reach alert database'
fi
sudo iptables -w 5 -nvL RISK_ENGINE_SEG
```

Check that a `DROP` rule's packet counter increased. If the direct-IP check
fails for any unrelated reason, the counter makes the cause visible.

You can also confirm no data-path regression:

```bash
curl -fsS http://127.0.0.1:8000/health
sudo docker compose exec rabbitmq rabbitmqctl list_queues -p risk_engine name messages_ready consumers
```

## Reversal and persistence

To remove only the additional firewall rules:

```bash
sudo bash tools/risk_engine_firewall.sh remove
```

The script does **not** change UFW or OS `INPUT` rules. It is idempotent and
reversible. Rules are **not permanent** across OS reboots; re-run `apply`
after Docker networks come up. For production, install a systemd unit after
testing; this coursework stage deliberately avoids changing host boot
configuration and preserving SSH/Tailscale access is the priority.

To revert Compose network segmentation, restore the backed-up Compose file
and run `sudo docker compose up -d` again. Do not remove database volumes.
