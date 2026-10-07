# CS 장애 실습 실행

[CS 심화 과정 문제](../docs/cs-advanced-exercises.md) · [심화 실습 준비 검증](../docs/labs/advanced-verification.md)

[종류별 문제·개선 기록 카탈로그](../docs/labs/catalog.md) · [GitHub 실습 이슈 목록](../docs/labs/issues.md)

문제 상황과 과제는 [실습 문제](../docs/cs-exercises.md), 실제 검증 기록과 미검증 범위는 [검증 기록](../docs/labs/verification.md)을 따른다.

| 사례 | 관찰할 문제 |
| --- | --- |
| 01 | 가격순 목록을 여러 페이지 넘겼을 때의 순서 |
| 02 | 주문이 실패한 뒤의 주문 내역과 재고 |
| 03 | 주문 전후 달라지는 배송비 |
| 04 | 반복 사용 중 느려지거나 중단되는 서비스 |
| 05 | 끝까지 도착하지 않는 상품 응답 |
| 06 | 요청할 때마다 달라 보이는 재고 |
| 07 | 빠르게 검색할 때 바뀌는 검색 결과 |
| 08 | 상품 설명을 표시한 뒤 생기는 화면 변화 |
| 09 | 배포 뒤 열리지 않는 쇼핑몰 기능 |
| 10 | 키보드와 보조 기술로 이용하는 상품 화면 |
| 11 | 구매 가능한 상품의 주문 완료 문제 |
| 12 | 한정 수량 상품의 주문 접수 문제 |
| 13 | 다시 방문할 때의 상품 화면 문제 |
| 14 | 상품을 반복해서 열 때의 이용 문제 |
| 15 | 전에 이용하던 목록에서 새 상품을 찾는 문제 |

## 준비

Python 3.12와 Linux 컨테이너를 실행하는 Docker가 필요하다. 저장소 루트에서 공통 이미지를 한 번 준비한다. probe의 Chromium은 Linux 컨테이너 안에서만 실행된다.

```powershell
docker build -f lab_suite/Dockerfile.base -t fcl-cs-base:v2 .
docker build -f lab_suite/Dockerfile.probe -t fcl-cs-probe:v2 .
```

정상 기준은 `l1-baseline-v2`다. 각 `incident/cs-NN` 실습 브랜치는 이 기준에서 시작하며, 기준 태그를 이동하거나 덮어쓰지 않는다. Oracle, 관련 테스트, API 계약과 DB 불변조건은 보호한다. 기준 용량은 [용량 기준](../docs/capacity-baseline.md)을 따른다.

## 학습자: 현재 체크아웃 실행과 검증

해당 incident 브랜치를 체크아웃한 뒤 다음 명령을 실행한다. `active_case.json`에서 사례 번호를 읽으므로 보통 `--case`는 생략한다. 이 파일이 없는 공통 도구 브랜치에서는 `--case 01`처럼 번호를 명시한다.

```powershell
python -m lab_suite.operator up
python -m lab_suite.operator check --expect fault --fresh
```

`up`은 **현재 체크아웃의 앱 코드**를 빌드하고 준비 상태까지 기다린다. `check`도 현재 코드를 다시 빌드한 뒤 실제 API·DB·브라우저 판정을 수행한다. 어느 명령도 기준 코드를 복사하거나 장애를 다시 주입하지 않는다. 학습자가 코드를 수정한 뒤의 검증과 종료는 다음과 같다.

```powershell
python -m lab_suite.operator check --expect healthy --fresh
python -m lab_suite.operator down
```

`check`의 종료 코드 0은 지정한 기대 상태를 통과했다는 뜻이다. `--expect fault`의 성공은 장애 재현, `--expect healthy`의 성공은 해당 정상 판정 통과를 뜻한다. 결과 JSON, 화면 캡처와 로그는 `artifacts/cs-labs/learner-NN/`의 실행별 폴더에 남는다. 사례 04의 장애 판정은 실제 Docker OOM 이벤트까지 요구하며, 반복 부하 때문에 다른 사례보다 오래 걸릴 수 있다.

- 접속 포트는 사례 번호 NN에 맞는 `http://localhost:181NN`이다. 실행 도구가 사례별 구성을 자동으로 준비한다. 내부 진단용 호스트 포트로 19105, 19106, 20106, 20112, 19113도 사용한다.
- 프로젝트 이름은 `fcl-exercise-NN`이다. 같은 사례의 teacher 검증과 포트를 공유하므로 동시에 실행할 수 없다. 충돌 시 명령은 소유 환경을 설명하고 중단하며 다른 프로젝트를 종료하지 않는다. 다른 체크아웃이 같은 프로젝트를 사용 중이면 그 체크아웃에서 먼저 종료한다.
- 기본 `up`/`check`는 DB를 보존한다. probe가 fixture를 추가하므로 독립적인 재검증에는 위 예시처럼 `--fresh`를 사용한다. `--fresh`는 **이 실습 프로젝트만** 종료한 뒤 새 DB로 시작한다.
- DB는 tmpfs에 있으며 `down`, `--fresh`, DB 컨테이너 재생성 시 실습 데이터가 사라진다. 필요한 증거를 먼저 남긴다. 특히 사례 02에서 새 DB의 정상 판정은 과거에 불일치가 생긴 데이터의 복구 성공을 뜻하지 않는다. 기존 데이터 복구는 별도로 검증해야 한다.
- 사례 10 자동 검증 범위는 접근성 트리와 키보드 동작이다. 실제 NVDA 음성 출력은 별도로 확인해야 하며 이 도구에서는 실행하지 않는다. 사례 09의 배포 전부터 열린 탭 동작 검증 여부는 검증 기록에서 확인한다.

## 제작자: 기준·장애·원복 검증

이 절의 제작 명령은 공통 도구 브랜치 `codex/cs-incident-labs`에서만 사용한다. 심화 incident 브랜치는 출제용 주입 정의를 제외한 학습자 배포본이므로 위의 `lab_suite.operator`로 실행하고 검증한다.

```powershell
python -m lab_suite verify --cases 01,02,03
```

teacher `verify`는 `l1-baseline-v2`를 별도 폴더로 내보내고, 기준·장애 주입·원복 단계의 소스와 격리 환경을 만들어 검사한다. `fcl-cs-NN-phase` 프로젝트를 사용하며, 학습자가 현재 체크아웃에서 수정한 코드의 통과 여부를 검사하는 명령이 아니다. 현재 코드의 수정 결과는 반드시 `lab_suite.operator check`로 확인한다. 전체 실행 기록 없이 단계 수나 완료 상태를 추정하지 않는다.


## 사례 04의 메모리 재현 조건

사례 04는 앱 CPU 1개, RAM 512MB와 **추가 swap 없음**을 명시한다. Docker의 기본 memory+swap 한도가 RAM의 두 배가 될 수 있어, 이 실습에서는 총 한도를 고정한다. 정상·장애·원복 모두 같은 설정을 사용한다.

기본 검증은 고유한 유효 URL에 48,000바이트 합성 진단 문자열을 붙여 메트릭이 보유하는 메모리의 증가를 가속한다. 같은 URL 대조군에도 같은 길이를 사용하며, 초기 소량 메트릭 관측은 짧은 입력을 쓴다. 부하 클라이언트는 연결을 재사용하지 않으며, 정상·장애·원복에 같은 연결 조건을 적용한다. 요청 로그는 이 사례에서 비활성화해 디스크 누적을 별도 장애로 만들지 않는다. 이 입력은 운영 트래픽의 재생이나 수용량 측정이 아니다. 실제 상품 목록·상세는 별도 클라이언트로 계속 관측하고 Docker OOM·재시작·조회 실패·회복을 함께 확인한다.

환경변수 LAB04_PADDING_BYTES=0, LAB04_PRESSURE_PATH=/products, LAB04_REQUESTS로 일반적인 짧은 검색 입력을 선택할 수 있다. 짧은 입력에서의 메모리 증가 추세와 가속 조건의 OOM 재현은 검증 기록에서 구분한다.
