# 공유 계층 규약

에이전트끼리 주고받는 것만 여기 둔다. 각자 내부에서만 쓰는 데이터는 자기 폴더에 둘 것.

## members.yaml — 연구실 명단

**연구실 명단의 유일한 원본이다.** 사람이 직접 편집한다.
5개 에이전트가 모두 이 파일을 읽는다 (회계는 급여 대상, GitLab은 담당자 매칭,
세미나는 발표자, 일정은 발표 검색, 코드는 소유자).

명단을 각자 들고 있으면 반드시 어긋난다. 사람이 나가고 들어오는 건 여기서만 고친다.

```yaml
affiliations: ["인천대", "Incheon National University"]   # 동명이인 판별용
lab: 인천대 연구실
members:
  - name: 홍길동          # 표시 이름. 다른 에이전트가 이 값으로 사람을 가리킨다
    sid: "202512345"
    role: 석사과정
    aliases: [...]       # 학회 프로그램에 실릴 수 있는 표기 (한글·영문)
    email: ""
```

`aliases` 는 4번 에이전트가 학회 프로그램에서 사람을 찾을 때 쓴다.
**표준 로마자로 추정한 표기는 자주 빗나간다** — 실제 프로그램에서 확인한 표기로 고쳐 쓸 것.
(실제 사례: 현지수 → `Ji Su Hyeon`(Hyun 아님), 이승훈 → `Seung Hun Lee`(Hoon 아님))

## lab.db — 공유 DB (SQLite)

에이전트가 만든 결과 중 **다른 에이전트가 볼 필요가 있는 것**만 넣는다.

### 쓰기 규칙

각 에이전트는 **자기 `agent` 값의 행만** 지우고 다시 넣는다. 남의 행은 건드리지 않는다.

```python
con.execute("DELETE FROM schedule WHERE agent = ?", (MY_AGENT_ID,))
con.executemany("INSERT INTO schedule VALUES (...)", my_rows)
```

`agent` 값: `1-finance` / `2-gitlab` / `3-seminar` / `4-schedule` / `5-code-kt`

### 테이블

**`schedule`** — 날짜가 있는 모든 것. 현재 `4-schedule` 이 채운다.

| 컬럼 | 설명 |
|---|---|
| `id` | 내용 기반 고정 해시 (순서가 바뀌어도 안 변함) |
| `agent` | 이 행을 쓴 에이전트 |
| `category` | `deadline` / `talk` / `routine` / `leave` |
| `kind`, `kind_label` | `abstract`, `oral`, `seminar`, `full`… / 한글 표시명 |
| `title` | 제목 |
| `member` | 쉼표 구분. **빈 값이면 랩 전체** |
| `society`, `event` | 학회명, 행사명 (학회 건만) |
| `starts_at`, `ends_at` | `YYYY-MM-DD` 또는 `YYYY-MM-DDTHH:MM` (KST) |
| `all_day` | 1이면 종일 |
| `location`, `note`, `url` | |
| `updated_at` | |

**`members`** — `members.yaml` 의 스냅샷. SQL로 조인하기 편하라고 둔 사본이며,
**원본은 언제나 `members.yaml`** 이다. 여기를 고치면 다음 `publish` 때 덮어써진다.

### 읽기 예시

```python
import sqlite3
con = sqlite3.connect("shared/lab.db")

# 이번 주 마감
con.execute("""SELECT starts_at, title, kind_label FROM schedule
               WHERE category='deadline' AND starts_at BETWEEN ? AND ?
               ORDER BY starts_at""", ("2026-09-04", "2026-09-11")).fetchall()

# 특정 연구원의 일정 (랩 공통 포함)
con.execute("""SELECT * FROM schedule
               WHERE member = '' OR member LIKE ?""", ("%이나경%",)).fetchall()
```

### 아직 안 쓰는 것

2·3·5번 에이전트가 자기 결과를 넣을 때 테이블을 추가하면 된다.
`schedule` 에 넣을지(날짜가 핵심인가) 새 테이블을 만들지는 그 에이전트가 정한다.
예상되는 것: `seminars`(3번), `tasks`(2번), `code_assets`(5번), `payroll`(1번).

**공유 DB는 신뢰 경계가 아니다.** 다른 에이전트가 넣은 값은 검증하고 쓸 것.
