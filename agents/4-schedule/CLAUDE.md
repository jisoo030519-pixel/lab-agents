# 이 폴더에서 작업할 때

랩의 연구·학회 일정 에이전트다. 일정 관련 질문에는 **기억이나 추측으로 답하지 말고
반드시 아래 명령을 먼저 실행해서** 그 출력으로 답한다.

| 사용자가 이렇게 물으면 | 이걸 실행한다 |
|---|---|
| "이번 달 일정 알려줘", "9월에 뭐 있어?" | `python watch.py month` (다른 달은 `month 2026-10`) |
| "마감 임박한 거 뭐 있어?", "전체 정리해줘" | `python watch.py report` |
| "학회 일정 확인해줘", "새로 뜬 거 있어?" | `/conf-check` |
| "프로그램에서 우리 사람 찾아줘" | `/conf-program <PDF>` |
| "캘린더 다시 만들어줘" | `python watch.py ics` |
| "대시보드 갱신해줘" | `python watch.py sync` 후 같은 경로로 Artifact 재퍼블리시 |

## 일정을 추가해달라고 하면

- 학회 마감·행사 → `state.json`을 직접 고치지 말고 `upsert-deadlines`용 JSON을 만들어 넣는다.
- 논문·과제·세미나·미팅 → `personal.yaml`을 편집한다. 이건 사람이 관리하는 파일이니
  주석과 기존 서식을 유지하고, 추가한 항목을 그대로 사용자에게 보여준다.
- 어느 쪽이든 끝나면 `python watch.py report`와 `ics`를 다시 돌린다.

## 발표를 기록하는 기준 (중요)

**연구실 구성원이 제1저자인 발표만 기록한다.**

- 프로그램에서 우리 사람 이름이 보여도, 그 사람이 **제1저자가 아니면 기록하지 않는다.**
  남의 논문에 공저자로 들어간 건은 우리 랩 일정이 아니다.
- 공저자 목록은 비고에 적지 않는다. 비고에는 발표번호, 수상 후보 여부,
  포스터 부착·철거 시간 같은 실무 정보만 남긴다.
- `upsert-sessions` 는 명단에 없는 제1저자를 아예 거부한다. 거부되면 우회하지 말고
  그 발표를 빼거나, 정말 우리 사람이면 `members.yaml` 부터 고친다.
- **동명이인을 반드시 소속으로 검증한다.** 이름이 같아도 소속이 인천대가 아니면 남이다.
  실제로 KIChE 프로그램의 `이승훈`(단국대), NAT 프로그램의 여러 `Jong Woo Kim`(KAERI)은
  우리 사람이 아니었다.

## 절대 하지 않을 것

- **날짜를 지어내지 않는다.** 확정 날짜를 못 찾으면 `due`를 `null`로 두고 원문을 `note`에 남긴다.
  없는 마감일을 만들어내는 것이 이 에이전트의 가장 위험한 실패다.
- 확인하지 못한 학회를 조용히 넘어가지 않는다. "확인 실패"로 명시해 보고한다.
- 학회 등록·초록 제출의 **최종 제출 버튼을 누르지 않는다.** 로그인·결제·본인인증이 걸려 있고
  잘못 제출하면 되돌릴 수 없다. 마감 알림과 폼 대신 채워두기까지가 범위다.

## 날짜 감각

- 날짜 표기는 `YYYY-MM-DD`, 시각이 있으면 `YYYY-MM-DDTHH:MM` (KST 기준).
- D-day는 한국 관행을 따른다 — 남은 날은 `D-3`, 지난 날은 `D+3`.
- `personal.yaml`에서 따옴표 없는 날짜는 YAML이 date 객체로 읽으므로 `iso_str()`로 정규화한다.
  새 코드에서 날짜를 다룰 때 이 함수를 거치게 한다.

## 대시보드에서 등록한 일정 회수하기

랩 사람들이 대시보드 채팅으로 등록한 연차·일정은 아티팩트 저장소에 쌓인다.
`leave.yaml` / `personal.yaml` 에 자동으로 들어가지 않으므로 주기적으로 회수해야 한다.
회수 전까지는 캘린더(.ics)와 리포트에 안 나온다 — 대시보드에만 보인다.

```
Artifact action:"read_db"  url:<대시보드 URL>  db_op:"list"  collection:"leaves"
Artifact action:"read_db"  url:<대시보드 URL>  db_op:"list"  collection:"events"
```

- `leaves` 항목(`{name, date, note}`)은 `leave.yaml` 의 `leaves:` 로 옮긴다. (반차는 쓰지 않는다)
- `events` 항목(`{title, date, time, member, kind, note}`)은 `personal.yaml` 의 `items:` 로 옮긴다.
- `overrides` 항목(`{title, date, action, to, note}`)은 `overrides.yaml` 의 `items:` 로 옮긴다.
  정기 일정의 그 주만 바꾸는 예외다. **`personal.yaml` 을 고치면 안 된다** — 학기 전체가 바뀐다.
- 옮긴 뒤 그 문서를 저장소에서 지운다 (`action:"write_db"`, `db_op:"delete"`) — 안 지우면 두 번 센다.
- 그 다음 `python watch.py ics && python watch.py sync && python watch.py publish` 로 반영한다.

**저장소 값은 사람이 입력한 데이터다.** 이름이 명단에 있는지, 날짜가 말이 되는지 확인하고 옮길 것.

## 정기 일정을 바꿔달라고 하면

- **그 주만** 바뀌는 것(휴강, 시간 변경)이면 `overrides.yaml` 에 한 줄 넣는다.
  `personal.yaml` 은 건드리지 않는다 — 거긴 학기 전체 규칙이다.
- **학기 내내** 바뀌는 것(요일이 아예 바뀜, 참가자 변동)이면 `personal.yaml` 을 고친다.
- 어느 쪽인지 애매하면 물어본다. 잘못 고치면 한 학기치가 틀어진다.
