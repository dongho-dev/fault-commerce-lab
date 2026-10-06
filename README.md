# Fault Commerce Lab

실제 PostgreSQL 재고와 주문을 사용하는 쇼핑몰 실습 프로젝트입니다.
상품 탐색부터 서버 견적, 주문 접수, 재고 검증까지 한 흐름으로 확인하고 장애를 재현·분석합니다.
실제 결제와 회원 인증은 구현하지 않습니다.

![쇼핑몰 화면](docs/storefront-preview.jpg)

**Python 3.12 · FastAPI · PostgreSQL 16 · SQLAlchemy · Docker Compose**

## 빠른 시작

Docker Desktop에서 Linux 컨테이너를 실행할 수 있는 상태로 준비합니다.

```sh
docker compose up -d --build --wait
```

DB 준비, 스키마 마이그레이션, 데모 상품 24개 등록 후 쇼핑몰이 시작됩니다.

| 화면 | 주소 |
| --- | --- |
| 쇼핑몰 | [localhost:8000](http://localhost:8000) |
| 운영 관제실 | [localhost:8000/admin](http://localhost:8000/admin) |
| API 문서 | [localhost:8000/docs](http://localhost:8000/docs) |
| DB 연결 상태 | [localhost:8000/health/ready](http://localhost:8000/health/ready) |

종료할 때는 `docker compose down`을 사용합니다. DB 볼륨은 보존됩니다.
설정을 바꾸려면 [.env.example](.env.example)을 참고해 Git에서 제외되는 `.env`를 만듭니다.

## 구현된 흐름

- 검색·카테고리·정렬·페이지 이동으로 상품을 찾습니다.
- 장바구니에서 상품과 수량을 고르고 우편번호를 입력합니다.
- 서버가 DB 단가와 배송 규칙으로 견적을 계산합니다. 화면은 서버 반환 금액을 표시합니다.
- 견적이 준비된 상품을 주문하면 재고 차감과 주문 저장이 하나의 트랜잭션으로 처리됩니다.
- 완료 화면과 브라우저 주문내역에서 서버가 접수한 금액을 확인합니다.

상품마다 별도 주문을 생성합니다. 장바구니와 주문내역 화면은 현재 브라우저의 저장소를
사용하며, 서버의 전체 주문 조회 화면이나 회원별 주문내역은 아닙니다.

## 주문 금액의 기준

`POST /orders/quote`와 주문 생성은 **같은 서버 계산 함수**를 사용합니다.
브라우저에는 배송비 계산식을 두지 않습니다. 견적 요청은 재고를 차감하거나 예약하지 않습니다.

| 항목 | 계산 규칙 |
| --- | --- |
| 상품금액 | DB 상품 단가 × 수량 |
| 기본 배송비 | 상품금액 20만 원 미만 3,000원, 이상 0원 |
| 지역 추가비 | 우편번호 앞 두 자리 60~99이면 2,500원 |
| 포장 추가비 | 수량 3개째부터 개당 700원 |
| 총액 | 상품금액 + 배송비 |

우편번호의 앞자리 `0`을 보존합니다. `06236`의 지역 접두어는 `06`입니다.
20만 원 이상이어도 지역·포장 추가비는 남습니다. 이 규칙은 실습용 정책입니다.

## API

| 메서드 | 경로 | 기능 |
| --- | --- | --- |
| `POST` | `/products` | 상품과 초기 재고 생성 |
| `GET` | `/products` | 검색·필터·정렬·페이지 조회 |
| `GET` | `/products/{product_id}` | 상품과 현재 재고 조회 |
| `POST` | `/orders/quote` | 서버 단가·상품금액·배송비·총액 견적 |
| `POST` | `/orders` | 주문 접수와 재고 차감 |

견적과 주문의 입력은 `product_id`, `quantity`, `postal_code`입니다.
운영 상태는 `/health/live`, `/health/ready`, `/metrics`에서 확인합니다.

## 검증과 실습 기준

변경한 동작과 인접 경로부터 검증합니다. 배송비·견적의 집중 검증 예시:

```sh
docker compose --profile test run --rm test sh -c "alembic upgrade head && pytest -q tests/unit/test_shipping.py tests/integration/test_order_quotes.py"
```

통합 테스트는 별도 PostgreSQL을 사용합니다. 독립적인 `oracle/`은 재고·주문 금액의
불변조건을 검사합니다. 정상 기준은 **`l1-baseline-v2`**이며, 태그를 이동하거나 덮어쓰지 않습니다.
장애 실습 브랜치는 이 태그에서 시작합니다. `master`는 현재 개선 사항을 반영하는 기본 브랜치입니다.

화면의 중복 클릭 차단과 서버 요청의 멱등성은 구분합니다.
남은 [멱등성 이슈 #5](https://github.com/dongho-dev/fault-commerce-lab/issues/5)를 추적합니다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [개발·운영 참고](docs/development.md) | 환경 변수, 테스트, 로그, 부하 도구, DB 검증 |
| [Vercel 배포](docs/vercel-deployment.md) | 외부 PostgreSQL 연결과 배포·확인 절차 |
| [배송비 수정 기록](docs/incidents/cs-03-resolution.md) | 원인, 검증 결과, 과거 주문 확인 기준 |
| [용량 기준](docs/capacity-baseline.md) | 고정 서버 크기, SLO, 측정 결과 |
| [사진 출처](app/frontend/assets/PHOTO_CREDITS.md) | 상품·배너 이미지 출처 |
