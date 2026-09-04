# 연구실 회계·급여 Agent

> **상태: 미착수** — 담당자가 정해지면 여기부터 채우세요.

## 역할

월급 정산, 과제별 인건비, 참여율 관리, 이상값·누락 탐지, 월별 정산표 생성

## 주요 기능

- 참여율 합계 검증 (100% 초과·미달)
- 전월 대비 급여·참여율 변동 확인
- 누락된 연구원·지급 항목 탐지
- Excel 정산표 자동 생성

## 주요 기술

Python · Excel/CSV · Database · LLM · Rule-based validation

## 공유 계층과의 관계

`shared/members.yaml` 로 급여 대상을 정한다.
`shared/lab.db` 의 `category='leave'` 행에서 연차 사용 내역을 읽을 수 있다 (4번이 채운다).

규약 전문: [../../shared/README.md](../../shared/README.md)

## 시작할 때

1. 이 README 를 채운다 (담당자, 데이터 출처, 산출물).
2. `CLAUDE.md` 를 만들어 이 에이전트에서 Claude가 지킬 규칙을 적는다.
3. 다른 에이전트가 볼 결과가 있으면 `shared/lab.db` 에 **자기 `agent` 행만** 쓴다.

동작하는 예시는 [4번 에이전트](../4-schedule)를 참고할 것 —
LLM이 해석하고 파이썬이 계산하는 구조로 되어 있다.
