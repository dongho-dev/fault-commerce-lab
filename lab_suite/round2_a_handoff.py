import json
import time


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def wait(path, stage, run_id=None, stop=None, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        if value.get("stage") == stage and (run_id is None or value.get("run_id") == run_id):
            if value.get("error"):
                raise RuntimeError(value["error"])
            return value
        if stop is not None:
            if stop.wait(0.05):
                raise RuntimeError("Lifecycle stopped")
        else:
            time.sleep(0.05)
    raise TimeoutError(f"Lifecycle checkpoint {stage} was not reached")


def perform_handoff(fence, synchronize, promote, checkpoint, activate):
    fence()
    synchronize()
    promote()
    checkpoint()
    activate()


def control(compose, evidence, stop, run):
    request = evidence / "handoff-request.json"
    response = evidence / "handoff-response.json"
    operations = []
    run_id = None

    def command(arguments, **kwargs):
        return run([*compose, *arguments], **kwargs)

    def sql(service, statement):
        value = command(
            ["exec", "-T", service, "psql", "-U", "lab", "-d", "lab", "-v",
             "ON_ERROR_STOP=1", "-At", "-c", statement], capture=True, timeout=20,
        )
        return value.decode().strip() if isinstance(value, bytes) else str(value).strip()

    try:
        signal = wait(request, "prepare", stop=stop)
        run_id = signal["run_id"]
        command(["exec", "-T", "db", "sh", "-c",
                 "printf '\nhost replication lab all scram-sha-256\n' >> "
                 '"$PGDATA/pg_hba.conf"; kill -HUP 1'],
                log=evidence / "handoff-controller.log", timeout=20)
        command(["up", "-d", "--no-deps", "--wait", "--wait-timeout", "35", "standby"],
                log=evidence / "handoff-controller.log", timeout=45)
        command(["up", "-d", "--no-deps", "--wait", "--wait-timeout", "35", "app-b"],
                log=evidence / "handoff-controller.log", timeout=45)
        if sql("standby", "SELECT pg_is_in_recovery()") != "t":
            raise RuntimeError("The replacement database is not a physical standby")
        original_system = sql("db", "SELECT system_identifier FROM pg_control_system()")
        if sql("standby", "SELECT system_identifier FROM pg_control_system()") != original_system:
            raise RuntimeError("Standby does not share the source cluster identity")
        write(response, {"run_id": run_id, "stage": "prepared"})
        wait(request, "handoff", run_id, stop)

        def fence():
            sql("db", "ALTER SYSTEM SET default_transaction_read_only = on")
            sql("db", "SELECT pg_reload_conf()")
            deadline = time.monotonic() + 10
            while sql("db", "SHOW default_transaction_read_only") != "on":
                if time.monotonic() >= deadline or stop.wait(0.05):
                    raise RuntimeError("Old writer fencing was not acknowledged")
            operations.append({"operation": "fenced_old_writer"})

        def synchronize():
            target = sql("db", "SELECT pg_current_wal_lsn()")
            deadline = time.monotonic() + 15
            while sql("standby", f"SELECT pg_last_wal_replay_lsn() >= '{target}'::pg_lsn") != "t":
                if time.monotonic() >= deadline or stop.wait(0.05):
                    raise RuntimeError("Standby did not reach the captured write boundary")
            operations.append({"operation": "replayed", "lsn": target})

        def promote():
            if sql("standby", "SELECT pg_promote(true, 10)") != "t":
                raise RuntimeError("Standby promotion failed")
            operations.append({"operation": "promoted", "timeline": sql(
                "standby", "SELECT timeline_id FROM pg_control_checkpoint()")})

        def checkpoint():
            write(response, {"run_id": run_id, "stage": "boundary", "operations": operations})
            wait(request, "boundary-observed", run_id, stop, timeout=25)

        def activate():
            write(evidence / "active-backend.json", {"backend": "app-b"})
            operations.append({"operation": "activated_replacement"})

        perform_handoff(fence, synchronize, promote, checkpoint, activate)
        write(response, {"run_id": run_id, "stage": "active", "operations": operations})
        return {"passed": True, "operations": operations, "cluster_identifier": original_system}
    except Exception as exc:
        write(response, {"run_id": run_id, "stage": "error", "error": str(exc)})
        return {"passed": False, "operations": operations, "error": str(exc)}
