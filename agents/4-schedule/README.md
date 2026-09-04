# 4. 학회·연구 일정 관리 Agent

> **상태: 동작 중** — 플랫폼의 5개 Agent 중 첫 번째로 구현된 에이전트다.

학회, 논문, 연구과제, 세미나, 연차까지 연구자가 놓치면 안 되는 일정을 통합 관리한다.
지정한 학회만 감시해서 마감을 추적하고, 프로그램북에서 우리 랩 발표를 찾아내고,
캘린더로 보여주고 각자 폰에 알림이 가게 한다.

## 설계

| | 담당 |
|---|---|
| 사이트·PDF 읽고 일정 해석 | 에이전트(LLM) — 사이트 구조가 바뀌어도 대응 |
| 상태 저장 · 변경 감지 · 계산 · 캘린더 | `watch.py` — 결정적으로 동작 |

크롤러를 하드코딩하지 않은 이유: 학회 사이트는 구조가 제각각이고 매년 바뀐다.
반대로 D-day 계산이나 연차 잔여일처럼 틀리면 안 되는 건 LLM에 맡기지 않는다.

## 데이터

| 파일 | 무엇 | 누가 고치나 |
|---|---|---|
| `../../shared/members.yaml` | **연구실 명단 (공유 원본)** | 사람 |
| `targets.yaml` | 감시할 학회 목록 | 사람 |
| `personal.yaml` | 학기 정기 일정, 논문·과제 | 사람 |
| `leave.yaml` | **석사 연차** | 사람 |
| `state.json` | 수집된 학회 마감·발표 | 에이전트 |

## 물어보기

이 폴더에서 Claude Code를 열고 말로 물으면 된다. `CLAUDE.md` 가 알아서 명령을 돌린다.

> 이번 달 일정 알려줘 · 마감 임박한 거 뭐 있어 · 이승훈 남은 연차 며칠

**대시보드에도 채팅이 붙어 있다** — 랩 사람들은 링크만 열면 Claude에게 직접 물어볼 수 있다.

## 수집

```
/conf-check                    등록된 학회를 돌며 마감 수집 (매일 09:00 자동 실행 중)
/conf-program 프로그램북.pdf    프로그램북에서 우리 랩 발표 찾기
```

**기록 규칙**: 연구실 구성원이 **제1저자인 발표만** 기록한다. 공저자로만 들어간 건 넣지 않는다.
`upsert-sessions` 가 명단 밖 제1저자를 거부하므로 우회할 수 없다. 자세한 건 `CLAUDE.md`.

## 연차

`leave.yaml` 에 한 줄 추가하면 캘린더에 뜨고 잔여일이 자동 계산된다.

```yaml
leaves:
  - {name: 이승훈, date: 2026-09-18}
  - {name: 이나경, date: 2026-09-22, half: true, note: "오전 반차"}
```

`used_before` 는 날짜를 모르는 기존 사용분이다. 잔여 계산에는 들어가지만 캘린더에는 안 뜬다.

## 알림

`python watch.py ics` 가 만드는 파일을 캘린더에 넣으면 끝이다.

- `calendars/all.ics` — 랩 전체
- `calendars/이나경.ics` — 본인 발표 + 공통 마감 + 참석하는 정기 일정 + 본인 연차

[calendars.google.com](https://calendar.google.com) → 설정(⚙) → **가져오기/내보내기**.
일정마다 내용 기반 고정 ID를 붙여두어 **갱신된 파일을 다시 가져와도 중복이 생기지 않는다.**

| | 알림 |
|---|---|
| 마감 (초록·등록·최종본) | 7일 전, 1일 전 |
| 학회 발표 | 1일 전, 1시간 전 |
| 세미나·미팅 | 1시간 전 |
| 연차 | 1일 전 |

## 명령

```bash
python watch.py month [YYYY-MM]   # 월간 브리핑 (정기 일정은 접어서)
python watch.py leave             # 연차 현황
python watch.py report            # 전체 리포트 → LATEST.md
python watch.py ics               # 캘린더 생성
python watch.py sync              # 대시보드 데이터 주입
python watch.py publish           # 공유 DB(shared/lab.db)에 내보내기
python watch.py scan-pdf <PDF>    # 프로그램북에서 우리 사람 찾기
python watch.py list / show / prune-members / reset
```

`publish` 는 다른 에이전트가 이 일정을 읽을 수 있게 한다. 수집 후 빠뜨리지 말 것.

## 공유 계층과의 관계

- **읽기**: `shared/members.yaml` (명단)
- **쓰기**: `shared/lab.db` 의 `agent='4-schedule'` 행 — `deadline` / `talk` / `routine` / `leave`

회계 에이전트(1번)는 `category='leave'` 로 연차를, GitLab 에이전트(2번)는
`category='deadline'` 으로 마감을, 세미나 에이전트(3번)는 `kind='seminar'` 로 일정을 읽을 수 있다.

## 한계

- 로그인이 필요한 회원 전용 공지는 못 읽는다.
- 스캔 이미지 PDF는 텍스트가 없어 자동 스캔이 0건을 낸다. **이건 "없음"이 아니라 "못 읽음"이다** —
  도구가 경고하며, 이 경우 사람이(또는 Claude가 눈으로) 읽어야 한다.
- 등록 **제출·결제**는 하지 않는다. 로그인·결제·본인인증이 걸려 있고 잘못 제출하면 되돌릴 수 없다.

## 의존성

```bash
pip install pyyaml pdfplumber
```
