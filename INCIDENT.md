# INCIDENT / TRAFFIC-002

이 브랜치(`incident/traffic-002`)는 정상 기준 태그 `l1-baseline-v2`에서 만든 **장애 조사 연습용**
브랜치입니다. 운영에 배포하지 않습니다. 정상 시스템은 언제든 `l1-baseline-v2` 태그로 되돌릴 수
있습니다.

## 시작점

- GitHub 이슈의 고객 문의(VOC) 요약에서 시작합니다. 증상만 적혀 있고 원인은 적혀 있지 않습니다.
- 정상 시스템이 어느 정도의 트래픽을 감당하는지는 [docs/capacity-baseline.md](docs/capacity-baseline.md)에
  기록되어 있습니다. "평소와 무엇이 달라졌는지"를 이 표와 비교해 판단합니다.

## 실행

이 연습 환경은 정상 시스템(포트 8000)과 나란히 띄울 수 있도록 Compose 프로젝트 이름 `shop1-t2`와
포트 8001을 씁니다. 포트는 git에 올리지 않는 `compose.override.yaml`로 바꿉니다.

```yaml
# compose.override.yaml
services:
  app:
    ports: !override ["8001:8000"]
```

```bash
docker compose -p shop1-t2 up -d --build --wait
```

처음 띄울 때 데모 카탈로그가 자동으로 들어갑니다(`SEED_CATALOG=true`). 쇼핑몰은
`http://localhost:8001`, 관제실은 `http://localhost:8001/admin`입니다.

## 사용할 수 있는 도구

| 도구 | 사용법 |
| --- | --- |
| 부하 테스트 | `docker compose -p shop1-t2 --profile tools run --rm --build loadgen --stages 5,10,20 --stage-seconds 30 --label <label>` → `artifacts/load-<label>.json` (행동별 결과도 들어 있습니다) |
| 메트릭 | `http://localhost:8001/metrics` (경로·상태 코드별 요청 수와 응답 시간 히스토그램) |
| 요청 로그 | `logs/app.jsonl` (JSON Lines, 요청 ID 포함) |
| 상세 로그 | `.env`에 `OBSERVABILITY_LEVEL=detailed`를 넣고 `docker compose -p shop1-t2 up -d`로 앱을 다시 띄우면 단계별 로그가 추가됩니다 |
| 동시 주문 점검 | `docker compose -p shop1-t2 exec -T app python scripts/concurrency_probe.py --base-url http://localhost:8000 --stock 10 --requests 40 --quantity 1` |
| 스모크·Oracle | `docker compose -p shop1-t2 exec -T app python scripts/smoke.py --base-url http://localhost:8000`, `... python oracle/check.py` |
| DB 스냅숏 | `docker compose -p shop1-t2 exec -T app python tools/db_snapshot.py` |

컨테이너 안에서 실행하는 명령의 `--base-url`은 컨테이너 내부 포트인 8000을 그대로 씁니다.

## 진행 순서 (권장)

1. 이슈의 증상을 브라우저와 부하 테스트로 재현하고 기준표와 수치를 비교합니다.
2. 느려지는 요청 종류와 조건(혼자일 때와 여럿일 때)을 좁힙니다.
3. 해당 경로에서 시간이 어디에 쓰이는지 확인하고 가설을 세웁니다. 가설은 "왜 사람이 몰릴 때만
   심해지는지"까지 설명할 수 있어야 합니다.
4. 수정한 뒤 같은 부하 테스트로 기준표 수준까지 회복되는지, 테스트·Oracle이 그대로 통과하는지
   확인합니다.

## 마지막 수단

`git diff l1-baseline-v2`는 정답을 바로 보여 주므로, 위 과정을 충분히 해 본 뒤에만 사용합니다.

## 정리

```bash
docker compose -p shop1-t2 down      # 데이터는 유지
docker compose -p shop1-t2 down -v   # 연습용 DB까지 삭제
```

## 규칙

`oracle/`, Oracle 관련 테스트, API 계약, DB 불변조건 정의는 수정하지 않습니다.
테스트를 약화하거나 DB를 직접 조작해 정상 결과를 연출하지 않습니다.
