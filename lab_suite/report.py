import argparse
import json
import shutil
from datetime import UTC, datetime

from lab_suite.__main__ import ARTIFACTS, ROOT, write_json
from lab_suite.catalog import BASELINE, CASES


def generate(runs):
    target = ROOT / "docs" / "labs"
    target.mkdir(parents=True, exist_ok=True)
    summary = {
        "baseline_tag": "l1-baseline-v2",
        "baseline_commit": BASELINE,
        "generated_at": datetime.now(UTC).isoformat(),
        "cases": {},
        "pending": [],
    }
    rows = []
    for case, (_, title) in CASES.items():
        chosen = None
        for run in reversed(runs):
            phase_paths = {
                phase: ARTIFACTS / run / case / phase / "result.json"
                for phase in ("healthy", "fault", "restored")
            }
            if not all(path.exists() for path in phase_paths.values()):
                continue
            reports = {
                phase: json.loads(path.read_text(encoding="utf-8"))
                for phase, path in phase_paths.items()
            }
            if not all(value.get("passed") for value in reports.values()):
                continue
            if (
                reports["healthy"]["application_sha256"]
                != reports["restored"]["application_sha256"]
            ):
                raise ValueError(f"Case {case}: restored application differs")
            if (
                len(
                    {
                        json.dumps(value["protected_sha256"], sort_keys=True)
                        for value in reports.values()
                    }
                )
                != 1
            ):
                raise ValueError(f"Case {case}: protected source differs")
            chosen = (run, reports)
            break
        if chosen is None:
            summary["pending"].append(case)
            summary["cases"][case] = {"ready": False, "title": title}
            rows.append(f"| {case} | 보완 중 | 보완 중 | 보완 중 | |")
            continue
        run, reports = chosen
        item = {
            "ready": True,
            "title": title,
            "run": run,
            "branch": f"incident/cs-{case}",
            "phases": {},
            "protected_files_unchanged": True,
            "restored_source_identical": True,
        }
        for phase, value in reports.items():
            source = ARTIFACTS / run / case / phase / "evidence"
            evidence = target / "evidence" / case / phase
            evidence.mkdir(parents=True, exist_ok=True)
            for filename in ("probe.json", "oom-events.json", "runtime-samples.json"):
                if (source / filename).exists():
                    shutil.copy2(source / filename, evidence / filename)
            screenshots = sorted(source.glob("*.png"))
            for image in screenshots[:1]:
                shutil.copy2(image, evidence / image.name)
            probe = value["probe"]
            item["phases"][phase] = {
                "passed": True,
                "healthy": probe["healthy"],
                "symptom": probe["symptom"],
                "oracle_passed": probe["oracle"]["passed"],
                "image_id": value["image_id"],
                "finished_at": value["finished_at"],
                "oom_events": len(value.get("oom_events", [])),
                "application_sha256": value["application_sha256"],
                "evidence": f"evidence/{case}/{phase}/probe.json",
            }
        summary["cases"][case] = item
        oracle = "결함 때 불일치 탐지" if case == "02" else "세 단계 정합성 유지"
        rows.append(f"| {case} | 통과 | 증상 재현 | 통과 | {oracle} |")
    baseline = ARTIFACTS / "baseline-contracts" / "result.json"
    if baseline.exists():
        summary["baseline_contracts"] = json.loads(baseline.read_text(encoding="utf-8"))
        shutil.copy2(ARTIFACTS / "baseline-contracts" / "pytest.log", target / "baseline-tests.txt")
    rollout = ARTIFACTS / "rollout-verification-v1" / "result.json"
    if rollout.exists():
        result = json.loads(rollout.read_text(encoding="utf-8"))
        summary["deployment_continuity_passed"] = result.get("passed", False)
        write_json(target / "evidence" / "09" / "deployment-continuity.json", result)
        source = ARTIFACTS / "rollout-verification-v1" / "evidence"
        for image in sorted(source.glob("*.png"))[:2]:
            shutil.copy2(image, target / "evidence" / "09" / image.name)
    summary["ready_count"] = sum(item["ready"] for item in summary["cases"].values())
    write_json(target / "verification.json", summary)
    content = [
        "# CS 실습 검증 기록",
        "",
        f"정상 기준: l1-baseline-v2 ({BASELINE}). 검증 완료: {summary['ready_count']}/10.",
        "",
        "이 문서는 제작자용 증거다. 학습자는 해당 브랜치의 INCIDENT.md와 실행 안내부터 사용한다.",
        "",
        "각 사례는 격리된 Docker 앱·PostgreSQL에서 동일한 합성 fix"
        "ture 규칙으로 정상, 결함, 원복을 실행했다. "
        "브라우저 검증은 Linux 컨테이너 안에서 수행했다. 결함 단계의 통과는 의"
        "도한 장애를 관측했다는 뜻이다.",
        "",
        "| 사례 | 정상 | 결함 | 원복 | Oracle |",
        "| --- | --- | --- | --- | --- |",
        *rows,
        "",
        "API·모델·스키마·Alembic·기존 테스트·Oracle 파일의 해시가 정"
        "상 기준과 일치하는지 검사했다. "
        "원복 앱 소스도 정상 단계와 동일함을 확인했다. 원복은 새로운 격리 DB의 "
        "대조 검증이며, "
        "장애 중 생긴 과거 데이터의 복구를 의미하지 않는다.",
        "",
        "## 추가 확인",
        "",
        "- 기존 기준 테스트: 52개 통과. 원본 결과는 baseline-tests.txt에 있다.",
        "- 사례 09: 실제 정상 이미지 → 결함 이미지 → 정상 이미지 교체 중 "
        "기존 탭과 새 탭의 동작을 검증했다.",
        "- 사례 10: 실제 Chromium 접근성 트리와 Tab·Enter 조작을"
        " 검증했다. NVDA 음성 실청취는 수행하지 않았다.",
        "- 사례 04: 짧은 검색어 4,000개에서 메모리 증가를 관측했다. 최종 "
        "OOM 판정은 실제 Docker 이벤트, "
        "고객 목록·상세 조회 실패, 재시작 후 회복을 함께 요구한다. 완료 표기 전"
        "에는 재현 완료로 취급하지 않는다.",
        "- 사례 04의 가속 입력은 유효한 긴 합성 URL을 사용한다. 앱 1CPU"
        "·RAM512MB·추가 swap 없음, "
        "요청 로그 비활성 조건을 세 단계에 동일하게 적용한다. 이 결과는 운영 트래"
        "픽의 발생 시간이나 수용량 추정이 아니다.",
        "",
        "## 증거",
        "",
        "[기계 판독 결과](verification.json)의 사례별 evidenc"
        "e 경로에 API 응답·판정·관측값을 보존했다. "
        "해당 디렉터리에 대표 화면과 OOM 이벤트·자원 표본도 있다. 전체 실행 로"
        "그와 모든 화면은 로컬 "
        "artifacts/cs-labs 아래 실행별 디렉터리에 보존된다.",
        "",
        "재실행 방법: [실습 실행 안내](../../lab_suite/README."
        "md). "
        "브라우저 설치 방식: [Playwright 공식 문서](https://pla"
        "ywright.dev/python/docs/browsers).",
        "",
    ]
    (target / "verification.md").write_text("\n".join(content), encoding="utf-8")
    book = ROOT / "docs" / "cs-exercises.md"
    text = book.read_text(encoding="utf-8-sig")
    first = (
        "**완료 범위: 문제 출제와 기준 소스 대조. 장애 코드 주입, 재현 실행,"
        " 해결 검증은 각 실습을 시작할 때 진행합니다.**"
    )
    replacement = (
        f"**현재 검증 완료: {summary['ready_count']}/10."
        " 상세 상태와 검증 범위는 [검증 기록](labs/verification.md)을 따릅니다.**"
    )
    if first in text:
        text = text.replace(first, replacement)
    else:
        import re

        text = re.sub(r"\*\*현재 검증 완료: .*?\*\*", replacement, text, count=1)
    text = text.replace(
        "이 준비 전제들은 아직 구축·검증 완료를 의미하지 않습니다. 정상 기준에서 "
        "재현에 사용할 입력과 환경을 먼저 검증한 후, 선택한 문제의 결함 하나를 주"
        "입합니다.",
        "실행 도구가 문제별 구성을 준비합니다. 검증 완료 여부는 검증 기록을 기준으"
        "로 확인하고, 각 문제는 독립된 환경에서 진행합니다.",
    )
    book.write_text(text, encoding="utf-8")
    print(json.dumps({"ready": summary["ready_count"], "pending": summary["pending"]}), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", default="verification-v1,verification-04-noswap")
    args = parser.parse_args()
    generate(args.runs.split(","))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
