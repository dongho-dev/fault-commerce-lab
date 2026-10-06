# Vercel 배포

Vercel은 `app/main.py`의 FastAPI 앱을 실행한다. 쇼핑몰과 API는 같은 배포에 포함된다.
기본 브랜치는 `master`다. Docker Compose의 로컬 DB는 Vercel에서 접근할 수 없으므로
별도의 외부 PostgreSQL을 연결해야 한다.

## 환경 변수

| 변수 | 설정 |
| --- | --- |
| `DATABASE_URL` | 전용 PostgreSQL의 `postgresql+psycopg://...` 연결 URL. Vercel 환경 변수로만 저장 |
| `LOG_FILE` | `vercel.json`에서 `/tmp/fault-commerce.jsonl`로 설정 |
| `DATABASE_POOL_SIZE` | `2` |
| `DATABASE_MAX_OVERFLOW` | `3` |

`DATABASE_URL`은 저장소나 로그에 기록하지 않는다. PostgreSQL 제공자가 요구하는 SSL
설정을 유지한다. 서버리스 인스턴스마다 풀이 생성되므로 작은 연결 풀을 사용한다.
기존 고정 서버의 용량 측정 결과를 Vercel의 처리 용량으로 간주하지 않는다.

## DB 준비

쇼핑몰 전용 DB에 연결한 관리 환경에서 한 번 실행한다.

```sh
alembic upgrade head
python -m scripts.seed_catalog
```

배포 함수의 요청마다 마이그레이션·초기화를 실행하지 않는다. 초기 상품 등록은 기존
상품 이름을 건너뛰며 주문 데이터와 재고를 초기화하지 않는다.

## 배포와 확인

로그인된 Vercel 프로젝트에 저장소를 연결하고 프로덕션 브랜치를 `master`로 설정한다.
DB 환경 변수를 준비한 뒤 기본 브랜치의 검증된 커밋으로 배포한다.

```sh
npx vercel deploy --prod
```

Vercel 배포 상태 `READY`와 실제 동작은 별도로 확인한다.

- `/health/ready`: 외부 DB 연결 정상
- `/products`: 실제 데모 상품 조회
- `/orders/quote`: 우편번호 `06236`의 앞자리 0 보존 및 배송비 확인
- 브라우저: 상품·장바구니·견적·주문완료 흐름 확인

실제 결제는 없으며, 배포된 앱에서도 상품 생성·주문 API는 인증 없이 접근 가능한
실습 기능이다. 실제 고객 정보나 결제 정보를 넣지 않는다.

참고: [Vercel FastAPI 공식 문서](https://vercel.com/docs/frameworks/backend/fastapi).
