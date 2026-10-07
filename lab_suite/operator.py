import argparse
import json
import socket
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from lab_suite.__main__ import (
    ARTIFACTS,
    ROOT,
    application_paths,
    command,
    compose_definition,
    hashes,
    sampler,
    write_json,
)
from lab_suite.catalog import BASELINE, normalize, provider
from lab_suite.controller import finish_controller, start_controller

ROOT_LABEL = "io.fault-commerce.exercise.root"
CASE_LABEL = "io.fault-commerce.exercise.case"


def selected_case(value, *, allow_other=False):
    active = ROOT / "lab_suite" / "active_case.json"
    recorded = None
    if active.exists():
        if not active.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("active_case.json must stay inside the current checkout")
        recorded = normalize(json.loads(active.read_text(encoding="utf-8-sig"))["case"])
    if value is None:
        if recorded is None:
            raise ValueError("This checkout has no active_case.json; supply --case NN")
        return recorded
    case = normalize(value)
    if recorded is not None and recorded != case and not allow_other:
        raise ValueError(f"This checkout is case {recorded}; use its case or switch branches")
    return case


def project_name(case):
    return f"fcl-exercise-{normalize(case)}"


def workspace(case):
    root = ROOT.resolve()
    artifacts = ARTIFACTS.resolve()
    work = (artifacts / f"learner-{normalize(case)}").resolve()
    if not artifacts.is_relative_to(root) or not work.is_relative_to(artifacts):
        raise ValueError("Learner artifacts must stay inside the current checkout")
    return work


def safe_path(work, path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(work.resolve()):
        raise ValueError(f"Unsafe learner record path: {path}")
    return resolved


def validate_record(case):
    work = workspace(case)
    path = safe_path(work, work / "active.json")
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        record.get("case") != case
        or record.get("project") != project_name(case)
        or Path(record.get("source", "")).resolve() != ROOT.resolve()
        or safe_path(work, record.get("config", "")) != work / "compose.json"
    ):
        raise ValueError("Learner active.json does not belong to this checkout and case")
    safe_path(work, record.get("evidence", ""))
    return record


def owned_labels(case):
    return {ROOT_LABEL: ROOT.resolve().as_posix(), CASE_LABEL: case}


def inspect_json(args):
    return json.loads(command(args, capture=True, timeout=30))


def assert_owned(case):
    project = project_name(case)
    expected = owned_labels(case)
    for kind, listing, inspection in (
        ("container", ["docker", "ps", "-a", "-q"], ["docker", "inspect"]),
        ("network", ["docker", "network", "ls", "-q"], ["docker", "network", "inspect"]),
    ):
        identifiers = (
            command(
                [*listing, "--filter", f"label=com.docker.compose.project={project}"],
                capture=True,
                timeout=30,
            )
            .decode()
            .split()
        )
        if not identifiers:
            continue
        for resource in inspect_json([*inspection, *identifiers]):
            labels = (
                resource.get("Config", {}).get("Labels", {})
                if kind == "container"
                else resource.get("Labels", {})
            ) or {}
            if any(labels.get(key) != value for key, value in expected.items()):
                raise ValueError(
                    f"{project} has a {kind} owned by another checkout. "
                    "Stop it from its owning checkout; no resource was removed."
                )


def assert_ports_available(case):
    ports = [18100 + int(case)]
    if case in ("05", "06", "13"):
        ports.append(19100 + int(case))
    if case in ("06", "12"):
        ports.append(20100 + int(case))
    for port in ports:
        identifiers = (
            command(
                ["docker", "ps", "-q", "--filter", f"publish={port}"],
                capture=True,
                timeout=30,
            )
            .decode()
            .split()
        )
        own_binding = False
        for resource in inspect_json(["docker", "inspect", *identifiers]) if identifiers else []:
            bindings = resource.get("NetworkSettings", {}).get("Ports", {}) or {}
            if not any(
                binding.get("HostPort") == str(port)
                for values in bindings.values()
                for binding in (values or [])
            ):
                continue
            labels = resource.get("Config", {}).get("Labels", {}) or {}
            if labels.get("com.docker.compose.project") != project_name(case):
                owner = labels.get("com.docker.compose.project", resource.get("Name", "unknown"))
                raise ValueError(
                    f"Port {port} is in use by {owner}. Teacher verification and learner "
                    "exercises share ports; stop the owner yourself. No project was stopped."
                )
            own_binding = True
        if not own_binding:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    listener.bind(("127.0.0.1", port))
                except OSError as exc:
                    raise ValueError(
                        f"Port {port} is unavailable; release it before starting this exercise. "
                        "No existing process or project was stopped."
                    ) from exc


def prepare(case, operation):
    work = workspace(case)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    evidence = safe_path(work, work / f"{stamp}-{operation}")
    evidence.mkdir(parents=True, exist_ok=False)
    config = safe_path(work, work / "compose.json")
    image = f"fcl-exercise-{case}:current"
    definition = compose_definition(case, ROOT.resolve(), evidence, image)
    for service in definition["services"].values():
        service["labels"] = owned_labels(case)
    definition["networks"] = {"default": {"labels": owned_labels(case)}}
    write_json(config, definition)
    record = {
        "case": case,
        "baseline": BASELINE,
        "project": project_name(case),
        "source": str(ROOT.resolve()),
        "config": str(config),
        "evidence": str(evidence),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    write_json(safe_path(work, work / "active.json"), record)
    compose = ["docker", "compose", "-p", project_name(case), "-f", str(config)]
    return compose, evidence, image


def start_current(compose, evidence):
    command([*compose, "build", "app"], log=evidence / "build.log")
    command(
        [*compose, "up", "-d", "--wait", "--wait-timeout", "160"],
        log=evidence / "startup.log",
    )


def stop_current(compose, evidence):
    command(
        [*compose[:2], "--profile", "*", *compose[2:], "down", "--remove-orphans"],
        log=evidence / "cleanup.log",
    )


def probe_report(path, case, expected, returncode):
    if not path.exists():
        return {"passed": False, "error": "Probe did not produce evidence"}
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Probe evidence must be a JSON object")
    valid = report.get("case") == case and report.get("expected") == expected
    return {
        "probe": report,
        "probe_exit_code": returncode,
        "passed": valid and returncode == 0 and report.get("passed") is True,
    }


def check_current(case, expected, compose, evidence, image):
    result = {
        "case": case,
        "expected": expected,
        "project": project_name(case),
        "source": str(ROOT.resolve()),
        "evidence": str(evidence),
        "passed": False,
        "application_sha256": hashes(ROOT, application_paths(case)),
    }
    stop = threading.Event()
    monitor = None
    controller = None
    started = str(time.time())
    try:
        container = command([*compose, "ps", "-q", "app"], capture=True).decode().strip()
        if not container:
            raise RuntimeError("Exercise app container is not running")
        if case == "04":
            monitor = threading.Thread(
                target=sampler, args=(container, evidence, stop), daemon=True
            )
            monitor.start()
            deadline = time.monotonic() + 25
            while not (evidence / "runtime.json").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Case 04 runtime sampler did not produce its initial sample")
                stop.wait(0.1)
        controller = start_controller(provider(case), case, compose, evidence, command)
        completed = command(
            [*compose, "run", "--rm", "--no-deps", "probe", "--case", case, "--expect", expected],
            log=evidence / "probe.log",
            check=False,
            timeout=4000 if case == "04" else 300,
        )
        result.update(probe_report(evidence / "probe.json", case, expected, completed.returncode))
        lifecycle = finish_controller(controller)
        if lifecycle is not None:
            result["controller"] = lifecycle
            if not lifecycle.get("passed"):
                result["passed"] = False
                result["controller_error"] = lifecycle.get("error", "Lifecycle validation failed")
        if case == "04":
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
            if expected == "fault":
                result["passed"] = result["passed"] and bool(oom_events)
                if not oom_events:
                    result["error"] = "Case 04 fault requires an actual Docker OOM event"
        result["image_id"] = (
            command(["docker", "image", "inspect", image, "--format", "{{.Id}}"], capture=True)
            .decode()
            .strip()
        )
    except Exception as exc:
        result.update(passed=False, error=str(exc))
    finally:
        lifecycle = finish_controller(controller)
        if lifecycle is not None:
            result["controller"] = lifecycle
            if not lifecycle.get("passed"):
                result["passed"] = False
        stop.set()
        if monitor:
            monitor.join(timeout=15)
        try:
            command([*compose, "logs", "--no-color"], log=evidence / "containers.log", check=False)
        except Exception as exc:
            result["log_error"] = str(exc)
        result["finished_at"] = datetime.now(UTC).isoformat()
        write_json(evidence / "result.json", result)
    print(f"RESULT case={case} expected={expected} passed={result['passed']} evidence={evidence}")
    if "error" in result:
        print(result["error"], file=sys.stderr)
    return 0 if result["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run and check the current exercise checkout")
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("up", "check", "down"):
        sub = subs.add_parser(name)
        sub.add_argument("--case", help="Case 01..21; defaults to lab_suite/active_case.json")
        if name == "check":
            sub.add_argument("--expect", required=True, choices=("healthy", "fault"))
            sub.add_argument("--fresh", action="store_true", help="Recreate this exercise's DB")
    args = parser.parse_args(argv)
    evidence = None
    try:
        case = selected_case(args.case, allow_other=args.command == "down")
        validate_record(case)
        assert_owned(case)
        if args.command != "down":
            assert_ports_available(case)
        compose, evidence, image = prepare(case, args.command)
        if args.command == "down":
            stop_current(compose, evidence)
            print(f"STOPPED {project_name(case)}; its temporary exercise data has been removed")
            return 0
        if args.command == "check" and args.fresh:
            stop_current(compose, evidence)
        start_current(compose, evidence)
        if args.command == "up":
            print(f"RUNNING http://localhost:{18100 + int(case)} source={ROOT} evidence={evidence}")
            return 0
        return check_current(case, args.expect, compose, evidence, image)
    except (Exception, KeyboardInterrupt) as exc:
        message = str(exc) or "Interrupted; use the down command to stop this exercise"
        if evidence is not None:
            write_json(evidence / "operator-error.json", {"error": message})
        print(f"ERROR {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
