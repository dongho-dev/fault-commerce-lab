import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from lab_suite.__main__ import ARTIFACTS, ROOT, command, compose_definition, write_json


def wait_status(evidence: Path, wanted: str, process: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 130
    while time.monotonic() < deadline:
        try:
            status = json.loads((evidence / "status.json").read_text(encoding="utf-8"))
            if status.get("phase") == "finished" and not status.get("passed"):
                raise RuntimeError(str(status))
            if status.get("phase") == wanted:
                if not status.get("passed"):
                    raise RuntimeError(str(status))
                print(f"ROLLOUT {wanted}", flush=True)
                return status
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            pass
        if process.poll() is not None:
            raise RuntimeError(f"Continuity probe exited before {wanted}")
        time.sleep(0.2)
    raise TimeoutError(f"Continuity probe did not reach {wanted}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args()
    work = ARTIFACTS / ("rollout-" + args.run)
    evidence = work / "evidence"
    evidence.mkdir(parents=True, exist_ok=False)
    project = "fcl-cs-09-rollout"
    definition = compose_definition("09", ROOT, evidence, "fcl-cs-09:healthy")
    definition["services"]["app"].pop("build")
    definition["services"]["probe"]["entrypoint"] = [
        "python",
        "-m",
        "lab_suite.cases.deployment_continuity",
    ]
    definition["services"]["probe"]["command"] = [
        "--base-url",
        "http://app:8000",
        "--evidence",
        "/evidence",
    ]
    config = work / "compose.json"
    write_json(config, definition)
    compose = ["docker", "compose", "-p", project, "-f", str(config)]
    process = None
    run_log = (evidence / "probe.log").open("w", encoding="utf-8")
    report = {"passed": False, "phases": []}
    try:
        command(
            [*compose, "up", "-d", "--wait", "--wait-timeout", "160"],
            log=evidence / "deployment.log",
        )
        process = subprocess.Popen(
            [*compose, "run", "--rm", "--no-deps", "probe"],
            cwd=ROOT,
            stdout=run_log,
            stderr=subprocess.STDOUT,
        )
        report["phases"].append(wait_status(evidence, "healthy_ready", process))
        for state, expected in (("fault", "fault_checked"), ("healthy", "finished")):
            definition["services"]["app"]["image"] = f"fcl-cs-09:{state}"
            write_json(config, definition)
            command(
                [*compose, "up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "app"],
                log=evidence / "deployment.log",
            )
            write_json(
                evidence / "signal.json", {"phase": "fault" if state == "fault" else "restored"}
            )
            report["phases"].append(wait_status(evidence, expected, process))
        process.wait(timeout=20)
        detail = json.loads((evidence / "continuity.json").read_text(encoding="utf-8"))
        report["passed"] = detail.get("passed") is True and process.returncode == 0
        report["detail"] = detail
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        command([*compose, "logs", "--no-color"], log=evidence / "containers.log", check=False)
        command([*compose, "down", "--remove-orphans"], log=evidence / "cleanup.log", check=False)
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        run_log.close()
        write_json(work / "result.json", report)
    print(
        json.dumps(
            {"passed": report["passed"], "evidence": str(evidence), "error": report.get("error")}
        ),
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
