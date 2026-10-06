# 종류별 문제·개선 기록 카탈로그

Fault Commerce Lab은 정상 쇼핑몰을 기준으로 문제를 재현하고, 조사·수정·검증 기록을 남기는 프로젝트다. 관심 있는 문제 유형에서 이슈와 개선 기록으로 이동할 수 있다. CS 실습의 고객 문의는 학습용 가상 상황이다.

확인 시각: **2026-10-06 21:35 KST**. 아래 상태는 해당 시점의 GitHub 기록이며 실시간으로 갱신되지 않는다.

**등록 이슈 15건 · 유형 5개 · 열린 이슈 10개 · CS 실습 준비 검증 10개**

개선 기록은 **PR 병합 3개, 검토 중 1개**다. 이슈의 열림·닫힘과 별도로 표시한다.

## 종류로 찾아보기

| 종류 | 이슈 수 | 다루는 문의 |
| --- | ---: | --- |
| [성능과 응답시간](#성능과-응답시간) | 4 | 기다리는 시간이나 이용 흐름의 지연에 관한 문의 |
| [접속과 화면 열림](#접속과-화면-열림) | 3 | 서비스에 들어가거나 화면을 여는 과정의 문의 |
| [상품 탐색과 화면 표시](#상품-탐색과-화면-표시) | 3 | 상품을 찾고 정보를 읽는 과정의 문의 |
| [주문 금액과 재고](#주문-금액과-재고) | 4 | 주문 결과, 안내 금액, 남은 수량에 관한 문의 |
| [접근성](#접근성) | 1 | 사용 방식에 따른 정보 구분과 조작에 관한 문의 |

[열린 이슈 전체](https://github.com/dongho-dev/fault-commerce-lab/issues?q=is%3Aissue+is%3Aopen) · [개선 PR 전체](https://github.com/dongho-dev/fault-commerce-lab/pulls?q=is%3Apr) · [CS 실습 실행 안내](../../lab_suite/README.md)

## 상태 읽는 법

- **실습 준비 확인:** 정상 → 의도한 증상 재현 → 정상 소스 원복 검증을 마쳤다는 뜻이다. 학습자의 수정·해결 완료와는 별도다.
- **이슈 상태:** GitHub의 현재 상태와 종료 사유다. 중복·진행 안 함으로 닫힌 이슈를 해결 성과로 집계하지 않는다.
- **개선 기록:** 해당 문제를 다루는 PR의 게시·병합 상태다. 달성한 결과, 미달 목표, 검증 범위와 기여 내용은 각 기록에서 확인한다. 병합만으로 모든 목표 달성을 뜻하지 않는다.
- **—:** 연결한 개선 PR이 없거나 CS 실습 준비 집계 대상이 아닌 항목이다. 문제 유형은 고객에게 드러난 현상으로만 분류했다.

## 성능과 응답시간

| 문제 | 이슈 상태 | CS 실습 준비 | 개선 기록 |
| --- | --- | --- | --- |
| [#1 쇼핑몰 이용 중 느려짐·멈춤 문의 증가](https://github.com/dongho-dev/fault-commerce-lab/issues/1) | 닫힘 · 완료 처리 | — | [PR #2](https://github.com/dongho-dev/fault-commerce-lab/pull/2) · 병합 · 기록상 일부 목표 미달 |
| [#4 주문 1건이 조회보다 느림](https://github.com/dongho-dev/fault-commerce-lab/issues/4) | 닫힘 · 진행 안 함 | — | — |
| [#6 장바구니 여러 상품 주문 시 줄마다 느리게 표시](https://github.com/dongho-dev/fault-commerce-lab/issues/6) | 닫힘 · 중복 | — | — |
| [#8 품절 안내 지연](https://github.com/dongho-dev/fault-commerce-lab/issues/8) | 닫힘 · 중복 | — | — |

[종류 선택으로 돌아가기](#종류로-찾아보기)

## 접속과 화면 열림

| 문제 | 이슈 상태 | CS 실습 준비 | 개선 기록 |
| --- | --- | --- | --- |
| [CS-04 · #18 한동안 정상 이용하던 쇼핑몰이 하루 중 몇 차례 함께 열리지 않습니다](https://github.com/dongho-dev/fault-commerce-lab/issues/18) | 열림 | 확인 | — |
| [CS-05 · #12 같은 쇼핑몰인데 접속 주소에 따라 상품 목록이 열리지 않습니다](https://github.com/dongho-dev/fault-commerce-lab/issues/12) | 닫힘 · 진행 안 함 | 확인 | — |
| [CS-09 · #16 새로 접속하면 상단 메뉴만 보이고 상품이 나오지 않습니다](https://github.com/dongho-dev/fault-commerce-lab/issues/16) | 열림 | 확인 | — |

[종류 선택으로 돌아가기](#종류로-찾아보기)

## 상품 탐색과 화면 표시

| 문제 | 이슈 상태 | CS 실습 준비 | 개선 기록 |
| --- | --- | --- | --- |
| [CS-01 · #9 낮은가격순인데 다음 페이지에서 더 저렴한 상품이 나옵니다](https://github.com/dongho-dev/fault-commerce-lab/issues/9) | 열림 | 확인 | [PR #19](https://github.com/dongho-dev/fault-commerce-lab/pull/19) · 병합 |
| [CS-07 · #14 검색어를 바꿨는데 잠시 뒤 이전 상품들이 다시 나옵니다](https://github.com/dongho-dev/fault-commerce-lab/issues/14) | 열림 | 확인 | — |
| [CS-08 · #15 일부 상품 설명에 낯선 안내 버튼이 나타납니다](https://github.com/dongho-dev/fault-commerce-lab/issues/15) | 열림 | 확인 | — |

[종류 선택으로 돌아가기](#종류로-찾아보기)

## 주문 금액과 재고

| 문제 | 이슈 상태 | CS 실습 준비 | 개선 기록 |
| --- | --- | --- | --- |
| [#5 결제 버튼 재클릭 시 중복 주문 여부](https://github.com/dongho-dev/fault-commerce-lab/issues/5) | 열림 | — | — |
| [CS-02 · #10 주문하지 못했다는 안내 뒤에 남은 수량이 줄어 보입니다](https://github.com/dongho-dev/fault-commerce-lab/issues/10) | 열림 | 확인 | [PR #20](https://github.com/dongho-dev/fault-commerce-lab/pull/20) · 병합 |
| [CS-03 · #11 주문하기 전과 주문완료 화면의 배송비가 다릅니다](https://github.com/dongho-dev/fault-commerce-lab/issues/11) | 열림 | 확인 | [PR #21](https://github.com/dongho-dev/fault-commerce-lab/pull/21) · 검토 중 |
| [CS-06 · #13 같은 상품의 재고가 새로고침할 때마다 이전 숫자로 돌아옵니다](https://github.com/dongho-dev/fault-commerce-lab/issues/13) | 열림 | 확인 | — |

[종류 선택으로 돌아가기](#종류로-찾아보기)

## 접근성

| 문제 | 이슈 상태 | CS 실습 준비 | 개선 기록 |
| --- | --- | --- | --- |
| [CS-10 · #17 소리로 들으면 어느 상품의 담기 버튼인지 구분할 수 없습니다](https://github.com/dongho-dev/fault-commerce-lab/issues/17) | 열림 | 확인 | — |

[종류 선택으로 돌아가기](#종류로-찾아보기)

## 자료와 갱신

[실습별 실행 브랜치 목록](issues.md) · [GitHub 상태 스냅숏](catalog.json)

<details>
<summary>상세 검증 자료 — 풀이 정보가 포함될 수 있음</summary>

[제작자용 검증 기록](verification.md) · [검증 범위와 한계](validation-notes.md) · [검증 환경 기록](runtime.json)

실습 준비 기록은 장애 주입과 원복 대조에 관한 증거다. 새 임시 DB에서의 원복 검증을 기존 장애 데이터의 복구 성공으로 해석하지 않는다. 상세 자료는 별도 확인이 필요할 때 펼쳐본다.

</details>

이슈를 추가하거나 진행 상태가 바뀌면 GitHub의 상태·종료 사유와 관련 PR을 확인하고, 이 문서와 catalog.json의 확인 시각을 함께 갱신한다. 현재 조회되는 이슈만 포함하며 삭제된 이슈를 추정해서 복원하지 않는다.
