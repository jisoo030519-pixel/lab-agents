# 사이트에서 누구나 일정 등록하게 하기

GitHub Pages 는 정적이라 글을 쓸 곳이 없다. 저장을 맡아줄 작은 서버를 하나 둔다.

```
랩 사람  →  일정판(공개)  →  Worker  →  KV(저장소)
                              ↑ 등록된 일정이 여기 쌓인다
```

**등록 기능은 API 키가 필요 없다.** Cloudflare 계정(무료, 카드 없이 가입)만 있으면 된다.
AI 는 별개이고 선택이다 — 아래 "AI 도 붙이려면" 참고.

## 설치 (등록만 · 무료)

### 1. wrangler 준비

```bash
npm install -g wrangler
wrangler login
```

브라우저가 열리면 승인한다.

### 2. 저장소 두 개 만들기

```bash
cd agents/4-schedule/worker
wrangler kv namespace create EVENTS
wrangler kv namespace create RATE
```

각각 `id = "..."` 를 알려준다. `wrangler.toml` 의 해당 자리에 붙여넣는다.

| binding | 무엇 | 없으면 |
|---|---|---|
| `EVENTS` | 등록된 일정이 쌓인다 | 등록 기능이 아예 안 된다 |
| `RATE` | 사용 횟수를 센다 | 동작하지만 **한도가 안 걸린다** (권장하지 않음) |

### 3. 올리기

```bash
wrangler deploy
```

`https://lab-schedule.<계정>.workers.dev` 같은 주소가 나온다.

### 4. 일정판에 연결

```bash
cd ..
python watch.py store https://lab-schedule.<계정>.workers.dev
python watch.py site
cd ../..
git add -A && git commit -m "feat: 사이트에서 일정 등록" && git push
```

1~2분 뒤 사이트 아래에 **"일정 추가"** 칸이 생긴다.

## 확인

```bash
curl -X POST https://lab-schedule.<계정>.workers.dev/events \
  -H "Content-Type: application/json" \
  -H "Origin: https://jisoo030519-pixel.github.io" \
  -d "{\"title\":\"시험\",\"date\":\"2026-12-25\",\"time\":\"18:00\",\"by\":\"테스트\"}"
```

`{"ok":true,...}` 가 오면 성공이다. 목록은 `GET /events`, 취소는 `DELETE /events/<id>`.

`Origin` 헤더 없이 부르면 막힌다 — 우리 사이트에서 온 요청만 받는다.

## ⚠️ 알고 써야 하는 것

**링크를 아는 사람은 누구나 등록하고 취소할 수 있다.** 로그인이 없다.
공용 화이트보드로 쓰려는 것이므로 의도된 동작이지만:

- 링크를 랩 밖에 뿌리지 말 것 (검색엔진 차단은 걸어뒀지만 링크는 링크다)
- 누가 넣었는지는 **등록자 칸에 적은 이름**뿐이다. 확인할 방법은 없다.
- 문제가 생기면 `python watch.py store off && python watch.py site && git push` 로 즉시 끈다.
  이미 등록된 것은 KV 에 남아 있으니 다시 켜면 그대로 돌아온다.

막아둔 것:

| 장치 | 값 | 어디서 바꾸나 |
|---|---|---|
| IP당 시간당 등록 수 | 20 | `wrangler.toml` 의 `WRITE_PER_IP_HOUR` |
| 저장 개수 상한 | 400 | `worker.js` 의 `MAX_EVENTS` |
| 제목·비고 길이 | 60 / 200자 | `worker.js` 의 `addEvent` |
| 참석자 이름 | **연구실 명단에 있는 사람만** | `data.json` 의 members |
| 허용 사이트 | 우리 사이트만 | `ALLOWED_ORIGINS` |

## 등록한 게 캘린더 파일(.ics)에도 들어가려면

Worker 에 쌓인 것은 사이트 화면에는 바로 보이지만, `.ics` 와 리포트는 YAML 을 보고 만든다.
매일 09:00 작업이 회수한다 (`conf-agent-daily`). 수동으로 하려면:

```bash
curl https://lab-schedule.<계정>.workers.dev/events > /tmp/site.json
# {"events":[...]} 를 {"events":[...]} 형태로 그대로 pull 에 넘기면 된다
python watch.py pull /tmp/site.json
python watch.py ics && python watch.py site
```

## AI 에이전트 (무료 · API 키 없음)

물어보기 칸이 **진짜 AI 에이전트**다 — 말을 알아듣고, 직접 등록·변경·취소까지 한다.
Cloudflare 가 무료로 돌려주는 공개 모델(Workers AI)을 쓴다. **API 키도 결제도 없다.**
`wrangler.toml` 의 `[ai] binding = "AI"` 한 줄이 전부다.

```
"다음 주 화요일 점심 12시에 회식 잡아줘"  → add_event    → 9/15(화) 12:00 등록
"방금 넣은 거 1시로 바꿔줘"               → update_event → 같은 일정의 시각만 변경
"방금 넣은 회식 지워줘"                   → delete_event
"이번 주에 뭐 있어?"                      → 자료 보고 답
"9월 31일에 미팅 넣어줘"                  → 없는 날짜라고 되물음
```

### 믿고 쓸 수 있게 해둔 것

- 에이전트가 도구를 불러도 **서버가 폼 등록과 똑같은 검사를 다시 거친다** (없는 날짜·명단 밖 이름·시각 형식).
- 날짜는 모델이 세지 않는다. 서버가 45일치 날짜표(요일·이번 주/다음 주)를 넣어주고 거기서 고르게 한다.
- 도구 왕복은 4회로 막혀 있다.
- 모델이 생각만 하다 답을 못 쓰면(실측으로 가끔 있었다) 도구를 빼고 "답만 짧게" 로 **한 번만** 다시 묻는다.
- AI 가 실패하면 페이지는 규칙 기반 처리로 떨어진다 (화면에 이유가 뜬다).

### 모델 고른 근거 (실측)

같은 질문을 두 모델에 던져 비교했다.

| 모델 | 날짜 | 답 | 질문당 |
|---|---|---|---|
| `@cf/google/gemma-4-26b-a4b-it` (채택) | 정확 | 자연스러운 한국어 | 약 50 뉴런 |
| `@cf/qwen/qwen3-30b-a3b-fp8` | 정확 | "2026-09-16 15:00에" 식 | 더 많음 |

자료가 약 5천 토큰이라 질문 하나에 대략 50 뉴런, 도구를 쓰면 두 배쯤 든다.
**하루 무료 10,000 뉴런 → 대략 100~200 질문.** 랩 11명이 쓰기엔 넉넉하다.
다 쓰면 그날은 "오늘 AI 무료 사용량을 다 썼습니다" 가 뜨고 규칙 기반으로 떨어진다.
매일 09:00 (KST) 에 초기화된다.

Gemma 는 애매한 말에 속으로 오래 고민하다 답을 못 쓰는 일이 있었다
("새로고침해줘" → 생각만 1200토큰, 82뉴런, 빈 답). `reasoning_effort: "low"` 로 막았다.

### 켜기

```bash
cd agents/4-schedule/worker
wrangler deploy
cd ..
python watch.py ai https://lab-schedule.<계정>.workers.dev
python watch.py site
```

한도는 `wrangler.toml` 의 `PER_IP_HOUR`(30), `DAILY_TOTAL`(200).

## 끄기

```bash
python watch.py store off    # 등록 끄기
python watch.py ai off       # AI 끄기
python watch.py site && cd ../.. && git push
```
