# 공개 사이트에 진짜 AI 붙이기

## 왜 서버가 필요한가

공개 웹페이지에 API 키를 심으면 **링크를 아는 누구나 브라우저 개발자도구로 키를 꺼낼 수 있다.**
그 키로 남이 요금을 태우면 막을 방법이 없다.

그래서 키를 들고 있는 작은 서버를 하나 사이에 둔다.

```
랩 사람  →  일정판(공개)  →  Worker(키 보관)  →  Anthropic API
                              ↑ 여기만 키를 안다
```

Worker 는 페이지가 보내주는 자료를 믿지 않고 **일정 자료를 직접 가져온다.**
페이지를 믿으면 누구든 긴 글을 밀어넣어 토큰을 태울 수 있다.

## 준비물 두 개

| | 어디서 | 비용 |
|---|---|---|
| Cloudflare 계정 | dash.cloudflare.com | 무료 (카드 없이 가입) |
| Anthropic API 키 | console.anthropic.com | **쓴 만큼 결제** (claude.ai 구독과 별개) |

⚠️ **API 키는 claude.ai 구독에 포함되지 않는다.** 별도로 크레딧을 넣어야 한다.
콘솔에서 **Spend limit(사용 한도)** 를 꼭 걸어둘 것 — 그게 마지막 안전장치다.

## 설치

### 1. Anthropic API 키 만들기

1. console.anthropic.com 로그인 → **API keys** → **Create Key**
2. 키를 복사해 둔다 (`sk-ant-...`). 다시 못 본다.
3. **Settings → Limits** 에서 월 사용 한도를 설정한다 (예: $5)

### 2. Cloudflare 준비

```bash
npm install -g wrangler
wrangler login
```

브라우저가 열리면 승인한다.

### 3. 사용량 세는 저장소 만들기

```bash
cd agents/4-schedule/worker
wrangler kv namespace create RATE
```

출력에 `id = "..."` 가 나온다. 그 값을 `wrangler.toml` 의 `여기에_KV_ID_붙여넣기` 자리에 넣는다.

> 이걸 건너뛰면 Worker 는 돌아가지만 **횟수 제한이 안 걸린다.** 하지 말 것.

### 4. 키를 서버에 넣기

```bash
wrangler secret put ANTHROPIC_API_KEY
```

물어보면 키를 붙여넣는다. 이 값은 코드에 저장되지 않고 Cloudflare 가 보관한다.

### 5. 올리기

```bash
wrangler deploy
```

`https://lab-schedule-ai.<계정>.workers.dev` 같은 주소가 나온다.

### 6. 일정판에 연결

```bash
cd ..
python watch.py ai https://lab-schedule-ai.<계정>.workers.dev
python watch.py site
cd ../..
git add -A && git commit -m "feat: 사이트에 AI 연결" && git push
```

1~2분 뒤 사이트에서 물어보면 진짜 AI가 답한다.

## 확인

```bash
curl -X POST https://lab-schedule-ai.<계정>.workers.dev \
  -H "Content-Type: application/json" \
  -H "Origin: https://jisoo030519-pixel.github.io" \
  -d "{\"q\":\"이번 주에 뭐 해야 해?\"}"
```

`{"text":"..."}` 가 오면 성공이다.

`Origin` 헤더 없이 부르면 막힌다 — 우리 사이트에서 온 요청만 받게 해뒀다.

## 요금이 새지 않게 해둔 것

| 장치 | 값 | 어디서 바꾸나 |
|---|---|---|
| IP당 시간당 질문 수 | 30 | `wrangler.toml` 의 `PER_IP_HOUR` |
| 전체 하루 질문 수 | 500 | `DAILY_TOTAL` |
| 질문 글자 수 | 400 | `worker.js` 의 `MAX_Q` |
| 답 길이 | 700 토큰 | `MAX_TOKENS` |
| 허용 사이트 | 우리 사이트만 | `ALLOWED_ORIGINS` |
| 자료 출처 | 서버가 직접 가져옴 | `DATA_URL` |

일정 자료(약 2천 토큰)는 **프롬프트 캐시**를 써서 같은 자료를 반복 청구하지 않게 했다.

더 싸게 쓰려면 `wrangler.toml` 의 `MODEL` 을 `claude-haiku-4-5-20251001` 로 바꾼다.

## 서버가 죽으면

페이지가 알아서 **자료 검색 모드로 떨어진다.** 말은 못 알아들어도
"오늘 / 이번 주 / 마감 / 이름" 같은 것은 계속 답한다. 화면에 이유가 뜬다.

AI 를 떼려면:

```bash
python watch.py ai off && python watch.py site
```

## 자료는 언제 갱신되나

Worker 는 `docs/data.json` 을 10분 캐시한다. `git push` 로 사이트가 갱신되면
늦어도 10분 안에 AI도 새 일정을 본다.
