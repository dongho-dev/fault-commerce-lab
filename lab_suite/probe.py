import argparse
import json
import os
import traceback
from datetime import UTC, datetime
from pathlib import Path

from lab_suite.catalog import normalize, provider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--expect", choices=("healthy", "fault"), required=True)
    parser.add_argument("--output", default="/evidence/probe.json")
    args = parser.parse_args()
    case = normalize(args.case)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    urls = {
        "app": os.environ.get("LAB_BASE_URL", "http://app:8000"),
        "direct": "http://app:8000",
        "secondary": "http://app-b:8000",
        "proxy": "http://proxy:8080",
    }
    report = {"case": case, "expected": args.expect, "started_at": datetime.now(UTC).isoformat()}
    try:
        report.update(provider(case).probe(case, urls, path.parent))
        from oracle.check import collect_report

        oracle = collect_report()
        report["oracle"] = oracle
        wanted_healthy = args.expect == "healthy"
        wanted_oracle = not (case == "02" and not wanted_healthy)
        report["passed"] = (
            report.get("healthy") is wanted_healthy
            and report.get("symptom") is not wanted_healthy
            and oracle["passed"] is wanted_oracle
        )
    except Exception as exc:
        report.update(passed=False, error=str(exc), traceback=traceback.format_exc())
    report["finished_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "case": case,
                "passed": report["passed"],
                "healthy": report.get("healthy"),
                "symptom": report.get("symptom"),
                "error": report.get("error"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
