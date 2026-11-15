#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 - <<'PY'
import json
import socket
import subprocess
import sys
import time
import uuid
import urllib.request

passed = 0
failed = 0

def run(*args, timeout=35):
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout).strip()
            or f"Command failed: {args[0]}"
        )
    return result.stdout.strip()

def check(label, func):
    global passed, failed
    try:
        detail = func()
        passed += 1
        print(f"[PASS] {label}", flush=True)
        if detail:
            print(f"       {detail}", flush=True)
        return True
    except Exception as exc:
        failed += 1
        print(f"[FAIL] {label}: {exc}", flush=True)
        return False

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def pg_role(node):
    result = run(
        "sudo", "docker", "exec", node,
        "psql", "-U", "riskengine",
        "-d", "riskengine", "-Atqc",
        "SELECT CASE WHEN pg_is_in_recovery() "
        "THEN 'REPLICA' ELSE 'PRIMARY' END;",
    )
    return result.strip()

def check_api():
    with urllib.request.urlopen(
        "http://127.0.0.1:8000/health",
        timeout=5,
    ) as response:
        data = json.load(response)
    require(data.get("status") == "ok", f"Health: {data}")
    return "GET /health -> ok"

def check_roles():
    global current_primary

    nodes = [
        "risk-engine-postgres",
        "risk-engine-postgres-standby",
    ]
    roles = {node: pg_role(node) for node in nodes}

    require(
        sorted(roles.values()) == ["PRIMARY", "REPLICA"],
        f"Unexpected roles: {roles}"
    )

    current_primary = next(
        node for node, role in roles.items()
        if role == "PRIMARY"
    )

    return f"Primary: {current_primary}"

def check_replication():
    require(current_primary is not None, "Primary not detected")

    result = run(
        "sudo", "docker", "exec", current_primary,
        "psql", "-U", "riskengine",
        "-d", "riskengine", "-Atqc",
        "SELECT COUNT(*) FROM pg_stat_replication "
        "WHERE state = 'streaming';",
    )

    require(int(result) >= 1, "No streaming replica")
    return "Streaming replication active"

def check_rabbit_cluster():
    output = run(
        "sudo", "docker", "exec",
        "risk-engine-rabbitmq-2",
        "rabbitmqctl", "cluster_status",
    )

    require("Running Nodes" in output, "Running Nodes missing")

    section = output.split("Running Nodes", 1)[1]
    section = section.split("Versions", 1)[0]

    nodes = [
        "rabbit@rabbitmq",
        "rabbit@rabbitmq-2",
        "rabbit@rabbitmq-3",
    ]

    running = [line.strip() for line in section.splitlines()]

    for node in nodes:
        require(node in running, f"Node not running: {node}")

    return "3/3 RabbitMQ nodes running"

def check_quorum(queue):
    output = run(
        "sudo", "docker", "exec",
        "risk-engine-rabbitmq-2",
        "rabbitmq-queues", "quorum_status",
        "--vhost", "risk_engine", queue,
    )

    require(
        output.count("voter") == 3,
        f"Expected 3 voters for {queue}"
    )
    require(
        output.lower().count("leader") == 1,
        f"Expected one leader for {queue}"
    )

    return "3 voters, 1 leader"

def check_tcp(container, host, port):
    code = """
import socket
import sys
with socket.create_connection(
    (sys.argv[1], int(sys.argv[2])),
    timeout=5
):
    pass
"""
    run(
        "sudo", "docker", "exec",
        container, "python3", "-c",
        code, host, str(port),
        timeout=15,
    )
    return f"{host}:{port} reachable"

def check_containers():
    names = [
        "risk-engine-api",
        "risk-engine-postgres",
        "risk-engine-postgres-standby",
        "risk-engine-etcd",
        "risk-engine-pg-haproxy",
        "risk-engine-redis",
        "risk-engine-rabbitmq",
        "risk-engine-rabbitmq-2",
        "risk-engine-rabbitmq-3",
        "risk-engine-rabbitmq-haproxy",
        "risk-engine-alert-postgres",
        "risk-engine-outbox-publisher",
        "risk-engine-alert-notifier",
        "risk-engine-alert-dispatcher",
        "risk-engine-notification-receiver",
        "risk-engine-backup-postgres-1",
        "risk-engine-backup-alert-postgres-1",
    ]

    for name in names:
        state = run(
            "sudo", "docker", "inspect",
            "-f", "{{.State.Status}}", name
        )
        require(state == "running", f"{name}: {state}")

    return f"{len(names)} running containers"

def end_to_end():
    tx_id = "deploy-verify-" + uuid.uuid4().hex[:16]

    payload = {
        "transaction_id": tx_id,
        "amount": 2700,
        "payer_key": "deploy-verify@example.test",
        "receiver_key": "receiver@example.test",
        "transaction_type": "PIX",
    }

    request = urllib.request.Request(
        "http://127.0.0.1:8000/transactions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)

    require(
        data.get("transaction_id") == tx_id,
        "API returned unexpected transaction"
    )
    require(
        "risk_assessment" in data,
        "Risk assessment missing"
    )

    print(f"       Transaction created: {tx_id}", flush=True)

    started = time.monotonic()

    while time.monotonic() - started < 45:
        result = run(
            "sudo", "docker", "exec",
            "risk-engine-alert-postgres",
            "psql", "-U", "alertsvc",
            "-d", "alertengine", "-Atqc",
            "SELECT COUNT(*) FROM processed_events "
            f"WHERE transaction_id = '{tx_id}';",
            timeout=15,
        )

        if result == "1":
            elapsed = time.monotonic() - started
            return f"Event processed in {elapsed:.2f} s"

        time.sleep(1)

    raise RuntimeError("Event not processed within 45 seconds")

print("==========================================", flush=True)
print("     RISK ENGINE DEPLOYMENT VERIFICATION", flush=True)
print("==========================================", flush=True)

current_primary = None

check("Docker containers", check_containers)
api_ok = check("FastAPI health", check_api)

roles_ok = check("PostgreSQL HA roles", check_roles)
check("PostgreSQL streaming replication", check_replication)

check("RabbitMQ cluster", check_rabbit_cluster)
check("Quorum Queue: risk.alerts",
      lambda: check_quorum("risk.alerts"))
check("Quorum Queue: risk.alerts.dead",
      lambda: check_quorum("risk.alerts.dead"))

check("API -> Redis",
      lambda: check_tcp("risk-engine-api", "redis", 6379))

check("API -> PostgreSQL HAProxy",
      lambda: check_tcp("risk-engine-api", "pg-haproxy", 5432))

check("Publisher -> RabbitMQ HAProxy",
      lambda: check_tcp(
          "risk-engine-outbox-publisher",
          "rabbitmq-haproxy", 5672
      ))

check("Notifier -> Alert PostgreSQL",
      lambda: check_tcp(
          "risk-engine-alert-notifier",
          "alert-postgres", 5432
      ))

print("\n=== END-TO-END TRANSACTION TEST ===", flush=True)

if api_ok and roles_ok:
    check("Transaction -> Outbox -> RabbitMQ -> Notifier",
          end_to_end)
else:
    print("[SKIP] End-to-end test: API or PostgreSQL unavailable")

print("\n==========================================")
print(f"PASSED: {passed}")
print(f"FAILED: {failed}")

if failed == 0:
    print("DEPLOYMENT VERIFICATION SUCCESS")
else:
    print("DEPLOYMENT VERIFICATION FAILED")

print("==========================================")

sys.exit(1 if failed else 0)
PY
