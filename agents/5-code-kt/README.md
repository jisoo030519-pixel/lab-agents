# 연구 코드·인수인계 Knowledge Agent

> **상태: 미착수** — 담당자가 정해지면 여기부터 채우세요.

## 역할

코드 자동 분석, 함수·입출력 추출, 실행법 생성, 자연어 코드 검색, Dependency 분석

## 주요 기능

- 코드 목적·주요 함수·입출력 자동 분석
- 연구분야 자동 태깅
- 선후행 코드 관계 분석 (공부 순서 추천)
- 졸업자 코드 인수인계

## 주요 기술

LLM Code Understanding · AST Parsing · GitLab API · RAG · Vector DB · Knowledge Graph

## 공유 계층과의 관계

`shared/members.yaml` 로 코드 소유자를 관리한다.
명단에서 빠진 사람의 코드가 고아가 되지 않도록, 졸업 처리 시 이 에이전트도 함께 갱신할 것.

규약 전문: [../../shared/README.md](../../shared/README.md)

## 시작할 때

1. 이 README 를 채운다 (담당자, 데이터 출처, 산출물).
2. `CLAUDE.md` 를 만들어 이 에이전트에서 Claude가 지킬 규칙을 적는다.
3. 다른 에이전트가 볼 결과가 있으면 `shared/lab.db` 에 **자기 `agent` 행만** 쓴다.

동작하는 예시는 [4번 에이전트](../4-schedule)를 참고할 것 —
LLM이 해석하고 파이썬이 계산하는 구조로 되어 있다.
