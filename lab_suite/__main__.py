import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from lab_suite.catalog import BASELINE, CASES, normalize, provider
from lab_suite.controller import finish_controller, start_controller

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "cs-labs"
DB_URL = "postgresql+psycopg://lab:lab@db:5432/lab"


def command(args, *, log=None, check=True, capture=False, timeout=None, env=None):
    kwargs = {"cwd": ROOT, "check": check, "timeout": timeout, "env": env}
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result = subprocess.run(args, **kwargs)
        return result.stdout
    if log:
        with Path(log).open("a", encoding="utf-8") as output:
            return subprocess.run(args, stdout=output, stderr=subprocess.STDOUT, **kwargs)
    return subprocess.run(args, **kwargs)


def git(*args):
    return ["git", "-c", f"safe.directory={ROOT.as_posix()}", "-C", str(ROOT), *args]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def hashes(source, prefixes):
    result = {}
    for prefix in prefixes:
        base = source / prefix
        paths = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in paths:
            if path.is_file() and "__pycache__" not in path.parts:
                result[path.relative_to(source).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    return result


def export_source(case, phase, destination):
    destination.mkdir(parents=True, exist_ok=False)
    archive = command(git("archive", "--format=tar", BASELINE), capture=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        bundle.extractall(destination, filter="data")
    shutil.copytree(
        ROOT / "lab_suite", destination / "lab_suite", ignore=shutil.ignore_patterns("__pycache__")
    )
    protected = hashes(
        destination, ["oracle", "tests", "alembic", "app/api", "app/schemas", "app/models"]
    )
    changes = []
    documents = ["docs/cs-exercises.md", "docs/cs-advanced-exercises.md"]
    evidence_documents = ROOT / "docs" / "labs"
    if evidence_documents.exists():
        documents.extend(
            path.relative_to(ROOT).as_posix()
            for path in sorted(evidence_documents.rglob("*"))
            if path.is_file()
        )
    for document in documents:
        original = ROOT / document
        if original.exists():
            target = destination / document
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            changes.append(document)
    if phase == "fault":
        for name, before, after in provider(case).replacements(case):
            path = (destination / name).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise ValueError("Unsafe replacement path")
            text = path.read_text(encoding="utf-8-sig")
            if text.count(before) != 1:
                raise ValueError(f"Case {case}: expected one replacement anchor in {name}")
            path.write_text(text.replace(before, after, 1), encoding="utf-8", newline="\n")
            changes.append(name)
    if (
        hashes(destination, ["oracle", "tests", "alembic", "app/api", "app/schemas", "app/models"])
        != protected
    ):
        raise ValueError("Protected baseline files changed")
    write_json(
        destination / "lab_suite" / "active_case.json",
        {"case": case, "baseline": BASELINE, "initial_state": phase},
    )
    return {
        "modified_files": sorted(set(changes)),
        "protected_sha256": protected,
        "application_sha256": hashes(destination, application_paths(case)),
    }


def application_paths(case):
    paths = ["app", ".dockerignore", "lab_suite/network_proxy.py"]
    if case == "13":
        paths.append("lab_suite/advanced_proxy.py")
    if case == "14":
        paths.append("lab_suite/advanced_server.py")
    return paths


def compose_definition(case, source, evidence, image):
    port = 18100 + int(case)
    env = {
        "DATABASE_URL": DB_URL,
        "SEED_CATALOG": "false",
        "LOG_FILE": "/dev/null" if case == "04" else "/tmp/app.jsonl",
        "LAB_CASE": case,
        "LAB_MIGRATE": "1",
        "DATABASE_POOL_SIZE": "20",
        "DATABASE_MAX_OVERFLOW": "10",
    }
    app = {
        "image": image,
        "build": {"context": source.as_posix(), "dockerfile": "lab_suite/Dockerfile.app"},
        "cpus": 1.0,
        "mem_limit": "512m",
        "restart": "on-failure:1",
        "environment": env,
        "depends_on": {"db": {"condition": "service_healthy"}},
        "ports": [f"127.0.0.1:{port if case not in ('05', '06') else port + 1000}:8000"],
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=2)",
            ],
            "interval": "2s",
            "timeout": "3s",
            "retries": 50,
        },
    }
    if case == "04":
        app["logging"] = {"driver": "none"}
        app["memswap_limit"] = "512m"
    services = {
        "db": {
            "image": "postgres:16",
            "environment": {
                "POSTGRES_DB": "lab",
                "POSTGRES_USER": "lab",
                "POSTGRES_PASSWORD": "lab",
            },
            "mem_limit": "320m",
            "cpus": 1.0,
            "tmpfs": ["/var/lib/postgresql/data:size=256m"],
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U lab -d lab"],
                "interval": "2s",
                "timeout": "3s",
                "retries": 40,
            },
        },
        "app": app,
    }
    if case in {"06", "12"}:
        secondary = dict(app)
        secondary.pop("build")
        secondary["environment"] = {**env, "LAB_MIGRATE": "0"}
        secondary["depends_on"] = {"app": {"condition": "service_healthy"}}
        secondary["ports"] = [f"127.0.0.1:{port + 2000}:8000"]
        services["app-b"] = secondary
    if case in ("05", "06"):
        services["proxy"] = {
            "image": image,
            "command": ["python", "-m", "lab_suite.network_proxy"],
            "cpus": 0.5,
            "mem_limit": "128m",
            "environment": {
                "UPSTREAM": "http://app:8000",
                "UPSTREAMS": "http://app:8000,http://app-b:8000"
                if case == "06"
                else "http://app:8000",
            },
            "ports": [f"127.0.0.1:{port}:8080"],
            "depends_on": {
                key: {"condition": "service_healthy"}
                for key in (["app", "app-b"] if case == "06" else ["app"])
            },
        }
    probe_env = {
        "DATABASE_URL": DB_URL,
        "LAB_BASE_URL": "http://proxy:8080" if case in ("05", "06") else "http://app:8000",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in (
        "LAB04_REQUESTS",
        "LAB04_CONCURRENCY",
        "LAB04_STAGE_TIMEOUT_SECONDS",
        "LAB04_REQUEST_TIMEOUT_SECONDS",
        "LAB04_PRESSURE_PATH",
        "LAB04_PADDING_BYTES",
        "LAB04_CUSTOMER_INTERVAL_SECONDS",
        "LAB04_RECOVERY_TIMEOUT_SECONDS",
    ):
        if name in os.environ:
            probe_env[name] = os.environ[name]
    services["probe"] = {
        "image": "fcl-cs-probe:v2",
        "profiles": ["probe"],
        "cpus": 1.0,
        "mem_limit": "768m",
        "shm_size": "128m",
        "working_dir": "/workspace",
        "environment": probe_env,
        "volumes": [
            {
                "type": "bind",
                "source": source.as_posix(),
                "target": "/workspace",
                "read_only": True,
            },
            {"type": "bind", "source": evidence.as_posix(), "target": "/evidence"},
        ],
    }
    definition = {"services": services}
    configure = getattr(provider(case), "configure_compose", None)
    if configure is not None:
        definition = configure(case, definition, source, evidence)
        if not isinstance(definition, dict) or "services" not in definition:
            raise ValueError(f"Case {case} returned an invalid Compose definition")
    return definition


def memory_bytes(value: str) -> int:
    token = value.split(" / ", 1)[0].strip()
    match = re.fullmatch(r"([0-9.]+)([A-Za-z]+)", token)
    if match is None:
        raise ValueError(f"Unexpected Docker memory value: {value}")
    units = {
        "B": 1,
        "kB": 1000,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
        "TiB": 1024**4,
    }
    return int(float(match[1]) * units[match[2]])


def sampler(container, evidence, stop):
    samples = []
    while not stop.is_set():
        try:
            snapshot = json.loads(
                command(["docker", "inspect", container], capture=True, timeout=10)
            )[0]
            state = snapshot["State"]
            memory = (
                command(
                    ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
                    capture=True,
                    timeout=10,
                )
                .decode()
                .strip()
            )
            sample = {
                "timestamp": datetime.now(UTC).isoformat(),
                "restart_count": snapshot.get("RestartCount", 0),
                "oom_killed": state.get("OOMKilled", False),
                "running": state.get("Running", False),
                "memory_usage": memory_bytes(memory),
                "memory_display": memory,
            }
            samples.append(sample)
            write_json(evidence / "runtime.json", sample)
            write_json(evidence / "runtime-samples.json", samples)
        except Exception as exc:
            write_json(evidence / "sampler-error.json", {"error": str(exc)})
        stop.wait(3)


def run_phase(case, phase, run_dir, keep=False):
    work = run_dir / case / phase
    source = work / "source"
    evidence = work / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    manifest = export_source(case, phase, source)
    image = f"fcl-cs-{case}:{'fault' if phase == 'fault' else 'healthy'}"
    project = f"fcl-cs-{case}-{phase}"
    config = work / "compose.json"
    write_json(config, compose_definition(case, source, evidence, image))
    compose = ["docker", "compose", "-p", project, "-f", str(config)]
    started = str(time.time())
    result = {
        "case": case,
        "phase": phase,
        "project": project,
        "image": image,
        "baseline": BASELINE,
        "source": str(source),
        "evidence": str(evidence),
        **manifest,
        "passed": False,
    }
    stop = threading.Event()
    monitor = None
    controller = None
    container = ""
    try:
        print(f"START case={case} phase={phase}", flush=True)
        command([*compose, "build", "app"], log=evidence / "build.log")
        command(
            [*compose, "up", "-d", "--wait", "--wait-timeout", "160"], log=evidence / "startup.log"
        )
        container = command([*compose, "ps", "-q", "app"], capture=True).decode().strip()
        monitor = threading.Thread(target=sampler, args=(container, evidence, stop), daemon=True)
        monitor.start()
        sample_deadline = time.monotonic() + 15
        while not (evidence / "runtime.json").exists() and time.monotonic() < sample_deadline:
            time.sleep(0.1)
        if not (evidence / "runtime.json").exists():
            raise RuntimeError("Runtime sampler did not provide an initial observation")
        expected = "fault" if phase == "fault" else "healthy"
        controller = start_controller(provider(case), case, compose, evidence, command)
        command(
            [*compose, "run", "--rm", "--no-deps", "probe", "--case", case, "--expect", expected],
            log=evidence / "probe.log",
            check=False,
            timeout=4000 if case == "04" else 300,
        )
        lifecycle = finish_controller(controller)
        if lifecycle is not None:
            result["controller"] = lifecycle
        report_path = evidence / "probe.json"
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            result["probe"] = report
            result["passed"] = report.get("passed", False)
        else:
            result["error"] = "Probe did not produce evidence"
        if lifecycle is not None and not lifecycle.get("passed"):
            result["passed"] = False
            result["controller_error"] = lifecycle.get("error", "Lifecycle validation failed")
        if container:
            events = command(
                [
                    "docker",
                    "events",
                    "--since",
                    started,
                    "--until",
                    str(time.time()),
                    "--filter",
                    f"container={container}",
                    "--filter",
                    "event=oom",
                    "--format",
                    "{{json .}}",
                ],
                capture=True,
                timeout=15,
            ).decode()
            oom_events = [json.loads(line) for line in events.splitlines() if line.strip()]
            result["oom_events"] = oom_events
            write_json(evidence / "oom-events.json", oom_events)
            if case == "04" and phase == "fault":
                result["passed"] = result["passed"] and bool(oom_events)
        result["image_id"] = (
            command(["docker", "image", "inspect", image, "--format", "{{.Id}}"], capture=True)
            .decode()
            .strip()
        )
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        lifecycle = finish_controller(controller)
        if lifecycle is not None:
            result["controller"] = lifecycle
            if not lifecycle.get("passed"):
                result["passed"] = False
        stop.set()
        if monitor:
            monitor.join(timeout=15)
        command([*compose, "logs", "--no-color"], log=evidence / "containers.log", check=False)
        if not keep:
            command(
                [*compose[:2], "--profile", "*", *compose[2:], "down", "--remove-orphans"],
                log=evidence / "cleanup.log",
                check=False,
            )
        result["finished_at"] = datetime.now(UTC).isoformat()
        write_json(work / "result.json", result)
        print(
            f"RESULT case={case} phase={phase} passed={result['passed']} evidence={evidence}",
            flush=True,
        )
    return result


def verify(cases, phases, run_dir):
    reports = []
    for case in cases:
        for phase in phases:
            report = run_phase(case, phase, run_dir)
            reports.append(report)
            write_json(run_dir / "results.json", reports)
            if not report["passed"]:
                print(f"STOP failed case={case} phase={phase}", flush=True)
                return 1
        phases_by_name = {r["phase"]: r for r in reports if r["case"] == case}
        if "healthy" in phases_by_name and "restored" in phases_by_name:
            if (
                phases_by_name["healthy"]["application_sha256"]
                != phases_by_name["restored"]["application_sha256"]
            ):
                write_json(
                    run_dir / case / "restore-error.json",
                    {"error": "Restored sources differ from healthy sources"},
                )
                return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="Isolated real-fault commerce exercises")
    subs = parser.add_subparsers(dest="command", required=True)
    check = subs.add_parser("verify")
    check.add_argument("--cases", default="all")
    check.add_argument("--phases", default="healthy,fault,restored")
    check.add_argument("--run", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    start = subs.add_parser("start")
    start.add_argument("case")
    start.add_argument("--state", choices=("healthy", "fault"), default="fault")
    stop = subs.add_parser("stop")
    stop.add_argument("case")
    subs.add_parser("list")
    args = parser.parse_args()
    if args.command == "list":
        for case, (_, title) in CASES.items():
            print(f"{case} {title}")
        return 0
    if args.command == "verify":
        cases = (
            list(CASES) if args.cases == "all" else [normalize(v) for v in args.cases.split(",")]
        )
        phases = args.phases.split(",")
        if set(phases) - {"healthy", "fault", "restored"}:
            raise ValueError("Unknown phase")
        return verify(cases, phases, ARTIFACTS / args.run)
    if args.command == "start":
        case = normalize(args.case)
        run_dir = ARTIFACTS / ("session-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
        result = run_phase(case, args.state, run_dir, keep=True)
        write_json(
            ARTIFACTS / f"active-{case}.json",
            {
                "config": str(run_dir / case / args.state / "compose.json"),
                "project": result["project"],
                "source": result["source"],
            },
        )
        print(f"URL http://localhost:{18100 + int(case)} SOURCE {result['source']}", flush=True)
        return 0 if result["passed"] else 1
    case = normalize(args.case)
    record = json.loads((ARTIFACTS / f"active-{case}.json").read_text(encoding="utf-8"))
    command(
        [
            "docker",
            "compose",
            "--profile",
            "*",
            "-p",
            record["project"],
            "-f",
            record["config"],
            "down",
            "--remove-orphans",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
