# GitLab 연구진행·보고 Agent

> **상태: 미착수** — 담당자가 정해지면 여기부터 채우세요.

## 역할

Issue·Commit·MR 분석, 연구원별 진행률, 지연 업무 탐지, 주간미팅 자료 생성

## 주요 기능

- 완료/진행/지연 구분, 주간 목표 달성률
- 전주 대비 진행률 비교
- 장기 미완료 업무 탐지
- 다음 주 업무 목록 정리

## 주요 기술

GitLab API · Python · LLM · KPI calculation · Dashboard

## 공유 계층과의 관계

`shared/members.yaml` 의 `name` 으로 GitLab 계정을 매칭한다 (계정명 필드는 필요하면 추가).
`shared/lab.db` 의 마감(`category='deadline'`)을 읽어 주간 계획에 반영할 수 있다.

규약 전문: [../../shared/README.md](../../shared/README.md)

## 시작할 때

1. 이 README 를 채운다 (담당자, 데이터 출처, 산출물).
2. `CLAUDE.md` 를 만들어 이 에이전트에서 Claude가 지킬 규칙을 적는다.
3. 다른 에이전트가 볼 결과가 있으면 `shared/lab.db` 에 **자기 `agent` 행만** 쓴다.

동작하는 예시는 [4번 에이전트](../4-schedule)를 참고할 것 —
LLM이 해석하고 파이썬이 계산하는 구조로 되어 있다.
