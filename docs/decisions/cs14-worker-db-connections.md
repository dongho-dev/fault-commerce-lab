# CS-14 작업자 DB 연결 분리

- 관련 이슈: [#27](https://github.com/dongho-dev/fault-commerce-lab/issues/27)
- 장애 기준: `incident/cs-14`, `336bdcf37cbec44a0955a9494d43182c987042ae`
- 검증일: 2026-10-07

## 원인 근거

서버는 부모 프로세스에서 DB 연결 8개를 준비한 뒤 작업자 2개를 fork한다. 작업자의 `initialize_worker()`가 비어 있어 부모의 열린 DB 연결을 그대로 재사용했다.

수정 전 계측에서 작업자 PID 16과 17이 동일한 PostgreSQL 접속 PID 91과 `socket:[966242]`를 사용했다. 상품 SELECT의 결과 상태가 ROLLBACK으로 반환됐고, 재고 4개 열을 요청한 SQL에서 9개 열이 반환되는 현상도 기록됐다. 잘못된 결과는 조회 예외와 서버 응답 검증 실패로 이어졌다. 계측 없이도 동시 요청 36건 중 5건의 HTTP 500이 재현됐다. 개별 패킷을 캡처한 결과는 아니며, 식별자와 드라이버 호출·결과를 대조한 기록이다.

## 선택한 수정

`initialize_worker(engine)`에서 `engine.dispose(close=False)`를 호출한다. 각 자식 프로세스가 첫 요청 전에 새 연결 풀을 사용하도록 하고, 부모 프로세스가 사용하는 기존 연결은 닫지 않는다. 이후 필요한 실제 연결은 작업자 자신의 풀에서 생성된다.

[SQLAlchemy의 fork와 연결 풀 지침](https://docs.sqlalchemy.org/en/20/core/pooling.html#using-connection-pools-with-multiprocessing-or-os-fork)에 따른 초기화 방식이다. 작업자 두 개를 유지한 상태에서 프로세스별 연결 수명 관리를 바로잡는다.

변경 범위는 작업자 초기화 한 곳, 실제 PostgreSQL과 fork를 사용하는 회귀 테스트, 이 결정 기록이다. Oracle, 기존 판정 기준, API 계약, DB 불변조건과 정상 기준 태그는 그대로 유지한다.

## 검증

| 검사 | 결과 |
| --- | --- |
| 신규 회귀 테스트, 수정 전 | 부모 접속 PID 415를 자식 PID 13·14가 함께 사용하여 예상대로 실패 |
| 같은 회귀 테스트, 수정 후 | 통과: 자식 둘의 접속이 서로 다르고 부모 접속과도 다름. 부모의 기존 접속으로 실제 쿼리 성공 |
| CS-14 기존 healthy 판정 | 480/480건 HTTP 200 및 fixture 전체 데이터 일치 |
| 작업자 교체 | 처음 실행과 두 차례 재시작 각각 160/160건 정상. 매 세대 두 작업자의 응답 관측 |
| Linux 컨테이너 브라우저 | 상품 상세명 일치, 화면 표시 성공, pageerror 없음 |
| 변경 Python 파일 Ruff 검사 | 통과 |

회귀 테스트는 `tests/integration/test_prefork_connections.py`다. PostgreSQL 환경에서 `python -m pytest tests/integration/test_prefork_connections.py -q`로 실행한다. fork 기반 실행 환경을 검증하므로 Linux에서 수행한다.

기존 판정은 `python -m lab_suite.operator check --expect healthy --fresh`로 실행했다. 결과 파일은 `artifacts/cs-labs/learner-14/20261007T041222.163617Z-check/result.json`, 화면 기록은 같은 폴더의 `case14-product.png`다. 두 차례 실제 앱 재시작도 controller 기록으로 통과했다. 검증 이미지 ID는 `sha256:304209422bacae10547b69313fc738fecb16add389ab6eaba777fa5848471da2`다.

범위는 CS-14 실습의 연결 분리, 카탈로그 동시 조회와 상품 상세 표시다. 전체 저장소 테스트 및 운영 배포 검증은 수행하지 않았다. 테스트용 DB는 실습 도구의 임시 DB이며 이전 장애 데이터 복구의 증거가 아니다.
