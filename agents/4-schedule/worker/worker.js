/**
 * 연구실 일정판 서버 (Cloudflare Worker)
 *
 * 두 가지를 한다. 둘은 서로 독립이다.
 *
 *  POST /events      일정 등록          ← API 키 필요 없음. 이것만 쓰려면 키 없이 배포해도 된다.
 *  GET  /events      등록된 일정 목록
 *  DEL  /events/:id  등록 취소
 *  POST /ai          AI 에게 질문        ← ANTHROPIC_API_KEY 를 넣었을 때만 동작
 *
 * 왜 서버가 필요한가:
 *  - 등록: GitHub Pages 는 정적이라 글을 쓸 곳이 없다. 저장할 데가 필요하다.
 *  - AI:   공개 페이지에 API 키를 심으면 링크를 아는 누구나 그 키로 요금을 태운다.
 *
 * 배포: wrangler deploy   (설정은 wrangler.toml)
 */

const MODEL = "claude-sonnet-5";
const MAX_Q = 400;          // 질문 글자 수 상한
const MAX_TOKENS = 700;     // 답 길이 상한
const PER_IP_HOUR = 30;     // IP당 시간당 질문 수
const WRITE_PER_IP_HOUR = 20;  // IP당 시간당 등록 수
const DAILY_TOTAL = 500;    // AI 전체 일일 상한 (요금 폭주 차단)
const MAX_EVENTS = 400;     // 저장할 일정 개수 상한 (스팸으로 채워지는 것 방지)
const DATA_TTL = 600;       // 일정 자료 캐시 (초)
const MAX_ROUNDS = 4;       // 도구 호출 왕복 상한 (무한 반복·요금 폭주 방지)
const DATE_DAYS = 70;       // 날짜표에 넣을 날 수 (오늘부터)

const SYSTEM = `당신은 인천대학교 에너지공정시스템 연구실의 일정 안내 담당입니다.
아래 <일정자료> 안에 있는 내용만으로 답하세요.

규칙:
- 자료에 없는 것은 "자료에 없습니다" 라고 답하고, 절대 지어내지 마세요.
  날짜·시각·장소·발표 제목을 추측해서 만들어내면 안 됩니다.
- 날짜는 "9/18(금)" 처럼 요일까지 붙여 쓰고, 남은 날은 자료의 D-day 를 그대로 쓰세요.
  요일을 직접 계산하지 마세요 — 자료에 적힌 것을 씁니다.
- 짧게 답하세요. 목록이면 한 줄에 하나씩.
- 이미 지난 마감·발표는 꺼내지 마세요. 자료에는 앞으로 남은 것만 있습니다.
  끝난 일을 짚지 말고, 없으면 "남은 일정이 없습니다" 라고만 하세요.
- 연차 정보는 이 자료에 없습니다. 물으면 담당자에게 문의하라고 하세요.
- 일정과 무관한 질문(번역, 코드, 잡담 등)은 정중히 거절하세요.

등록·취소 (도구를 씁니다)
- "넣어줘 / 잡아줘 / 등록해줘 / 추가해줘" → add_event 를 부르세요. 종류를 가리지 마세요.
  회식·출장·미팅·마감·제출 무엇이든 넣습니다. 되묻지 말고 바로 넣으세요.
- "지워줘 / 취소해줘 / 빼줘" → delete_event 를 부르세요. id 는 <등록된 일정> 에 적혀 있습니다.
  <학회 마감>·<우리 랩 발표>·<정기 미팅> 은 id 가 없어 지울 수 없습니다.
  그런 걸 지워달라면 담당자에게 말하라고 안내하세요.
- **날짜는 <날짜표> 에 적힌 것을 그대로 쓰세요.** "다음 주 화요일" 이 며칠인지 직접 세지 마세요.
  날짜표에 없는 날이면 사용자에게 되물으세요.
- 시각은 24시간 HH:MM 으로 넘기세요. "오후 6시" 는 18:00 입니다. 없으면 비워 두세요.
- 참석자는 구성원 명단에 있는 이름만 넘기세요. 안 밝히면 비워 두세요 (랩 전체).
- **도구를 부르기 전에 "등록했습니다" 라고 말하지 마세요.** 도구 결과를 받은 뒤에만 알리세요.
- 도구가 오류를 돌려주면 그 문구를 그대로 전하세요. 원인을 지어내지 마세요.
- 등록·취소한 뒤에는 무엇이 언제로 처리됐는지 한 줄로 알려주세요.`;

const TOOLS = [
  {
    name: "add_event",
    description: "연구실 일정을 등록한다. 회식·출장·미팅·마감·제출 등 종류를 가리지 않는다.",
    input_schema: {
      type: "object",
      properties: {
        title: { type: "string", description: "무슨 일정인지 (예: 회식, 교수님 면담)" },
        date: { type: "string", description: "YYYY-MM-DD. 반드시 <날짜표> 에 있는 날짜를 쓴다." },
        time: { type: "string", description: "HH:MM (24시간). 종일이면 빈 문자열." },
        member: { type: "array", items: { type: "string" },
                  description: "참석자 (구성원 명단의 한글 이름). 랩 전체면 빈 배열." },
        note: { type: "string", description: "비고. 없으면 빈 문자열." },
        by: { type: "string", description: "등록을 요청한 사람 이름. 모르면 빈 문자열." },
      },
      required: ["title", "date"],
    },
  },
  {
    name: "delete_event",
    description: "사이트에서 등록됐던 일정을 취소한다. <등록된 일정> 에 있는 id 만 지울 수 있다.",
    input_schema: {
      type: "object",
      properties: { id: { type: "string", description: "취소할 일정의 id" } },
      required: ["id"],
    },
  },
];

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const origin = req.headers.get("Origin") || "";
    const allowed = (env.ALLOWED_ORIGINS || "").split(",").map(s => s.trim()).filter(Boolean);
    const okOrigin = allowed.length === 0 || allowed.includes(origin);
    const cors = {
      "Access-Control-Allow-Origin": okOrigin && origin ? origin : (allowed[0] || "*"),
      "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
      "Vary": "Origin",
    };
    const json = (obj, status = 200) =>
      new Response(JSON.stringify(obj), {
        status, headers: { ...cors, "Content-Type": "application/json; charset=utf-8" },
      });

    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    if (!okOrigin) return json({ error: "허용되지 않은 곳에서의 요청입니다" }, 403);

    const path = url.pathname.replace(/\/+$/, "") || "/";

    // ---- 일정 등록 (키 없이 동작) ----
    if (path === "/events" || path.startsWith("/events/")) {
      if (!env.EVENTS) return json({ error: "서버에 저장소(EVENTS)가 연결되지 않았습니다" }, 500);
      if (req.method === "GET") return json({ events: await listEvents(env) });
      if (req.method === "POST") return await addEvent(req, env, ctx, json);
      if (req.method === "DELETE") {
        const id = path.slice("/events/".length);
        if (!/^[A-Za-z0-9_-]{4,40}$/.test(id)) return json({ error: "잘못된 id" }, 400);
        const cur = await env.EVENTS.get("ev:" + id);
        if (!cur) return json({ error: "이미 없는 일정입니다" }, 404);
        await env.EVENTS.delete("ev:" + id);
        return json({ ok: true, id });
      }
      return json({ error: "GET / POST / DELETE 만 받습니다" }, 405);
    }

    // ---- AI (키가 있을 때만) ----
    if (path === "/ai" || path === "/") {
      if (req.method !== "POST") return json({ error: "POST 로 보내주세요" }, 405);
      if (!env.ANTHROPIC_API_KEY)
        return json({ error: "이 서버에는 AI 가 연결돼 있지 않습니다 (등록 기능만 씁니다)" }, 501);
      return await askAi(req, env, ctx, json);
    }

    return json({ error: "없는 주소입니다" }, 404);
  },
};

/* ---------------------------------------------------------------- 일정 등록 */

async function listEvents(env) {
  const out = [];
  let cursor;
  do {
    const page = await env.EVENTS.list({ prefix: "ev:", cursor, limit: 1000 });
    for (const k of page.keys) {
      const v = await env.EVENTS.get(k.name, "json");
      if (v) out.push({ id: k.name.slice(3), ...v });
    }
    cursor = page.list_complete ? null : page.cursor;
  } while (cursor);
  out.sort((a, b) => (a.date + (a.time || "")).localeCompare(b.date + (b.time || "")));
  return out;
}

async function addEvent(req, env, ctx, json) {
  const limited = await checkWriteLimit(env, req);
  if (limited) return json({ error: limited }, 429);
  let b;
  try { b = await req.json(); } catch { return json({ error: "형식이 잘못됐습니다" }, 400); }
  const r = await putEvent(b, env, ctx);
  return r.error ? json({ error: r.error }, r.status || 400) : json(r);
}

/* 실제로 저장하는 곳. 폼(POST)과 AI 도구가 같은 문을 쓴다 —
   따로 두면 한쪽 검사만 고치고 다른 쪽은 빠뜨리게 된다. */
async function putEvent(b, env, ctx) {
  const str = (v, max) => String(v == null ? "" : v).trim().slice(0, max);
  const title = str(b.title, 60);
  const date = str(b.date, 10);
  const time = str(b.time, 5);
  const note = str(b.note, 200);
  const by = str(b.by, 20);

  if (!title) return { error: "무슨 일정인지 적어주세요" };
  // 날짜는 부르는 쪽에서 정규화해 보낸다. 여기서는 형식만 확인한다.
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date))
    return { error: "날짜는 2026-09-25 형태여야 합니다" };
  const d = new Date(date + "T00:00:00Z");
  if (isNaN(d) || d.toISOString().slice(0, 10) !== date) return { error: "없는 날짜입니다" };
  if (time && !/^([01]\d|2[0-3]):[0-5]\d$/.test(time))
    return { error: "시각은 14:30 처럼 24시간 형식이어야 합니다" };

  // 참석자는 실제 구성원인지 확인한다. 아무 이름이나 들어가면 남의 캘린더에 뜬다.
  let member = Array.isArray(b.member) ? b.member.map(x => str(x, 12)).filter(Boolean) : [];
  member = member.slice(0, 12);
  if (member.length) {
    let known = [];
    try { known = (await loadData(env, ctx)).members || []; } catch {}
    if (known.length) {
      const bad = member.filter(m => !known.includes(m));
      if (bad.length) return { error: `연구실 명단에 없는 이름입니다: ${bad.join(", ")}` };
    }
  }

  const n = (await env.EVENTS.list({ prefix: "ev:", limit: 1000 })).keys.length;
  if (n >= MAX_EVENTS)
    return { error: "등록된 일정이 너무 많습니다. 담당자에게 정리를 요청하세요.", status: 409 };

  const id = crypto.randomUUID().replace(/-/g, "").slice(0, 12);
  const rec = { title, date, time, member, note, by, at: new Date().toISOString() };
  await env.EVENTS.put("ev:" + id, JSON.stringify(rec));
  return { ok: true, id, ...rec };
}

/* ---------------------------------------------------------------- AI */

async function askAi(req, env, ctx, json) {
  let body;
  try { body = await req.json(); } catch { return json({ error: "형식이 잘못됐습니다" }, 400); }
  const q = String(body.q || "").trim().slice(0, MAX_Q);
  if (!q) return json({ error: "질문이 비어 있습니다" }, 400);

  // 직전 대화만 짧게. 길이를 안 자르면 여기로 토큰을 밀어넣을 수 있다.
  const history = (Array.isArray(body.history) ? body.history : [])
    .slice(-4)
    .filter(m => m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string")
    .map(m => ({ role: m.role, content: m.content.slice(0, 600) }));

  const limited = await checkLimits(env, req);
  if (limited) return json({ error: limited }, 429);

  let facts;
  try {
    facts = await loadFacts(env, ctx);
  } catch (e) {
    return json({ error: "일정 자료를 불러오지 못했습니다: " + e.message }, 502);
  }

  const messages = [...history, { role: "user", content: q }];
  const system = [
    { type: "text", text: SYSTEM },
    // 자료는 매번 같으므로 캐시해서 토큰 값을 아낀다
    { type: "text", text: "<일정자료>\n" + facts.fixed + "\n</일정자료>",
      cache_control: { type: "ephemeral" } },
    // 방금 누가 넣었을 수 있으니 캐시 뒤에 붙인다 (이 부분만 매번 새로 읽힌다)
    { type: "text", text: facts.live },
  ];

  /* 도구를 쓰면 한 번에 안 끝난다: 모델이 도구를 부르고 → 우리가 실행하고 →
     결과를 돌려주면 그제서야 답을 쓴다. 무한히 돌지 않게 횟수를 막는다. */
  let changed = false;
  const actions = [];   // 페이지가 저장소 지연 반영을 메우는 데 쓴다 (방금 넣은 것을 바로 그리기)
  for (let round = 0; round < MAX_ROUNDS; round++) {
    let res;
    try {
      res = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-api-key": env.ANTHROPIC_API_KEY,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: env.MODEL || MODEL,
          max_tokens: MAX_TOKENS,
          system,
          tools: TOOLS,
          messages,
        }),
      });
    } catch (e) {
      return json({ error: "AI 서버에 닿지 못했습니다" }, 502);
    }

    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      // 요금·한도 문제는 그대로 알려줘야 담당자가 조치할 수 있다
      const msg = res.status === 401 ? "API 키가 잘못됐습니다"
                : res.status === 429 ? "AI 서버 한도에 걸렸습니다. 잠시 뒤 다시 시도하세요"
                : res.status === 400 && /credit|balance/i.test(detail) ? "API 잔액이 부족합니다"
                : "AI 응답 실패 (" + res.status + ")";
      return json({ error: msg }, 502);
    }

    const data = await res.json();
    const blocks = data.content || [];
    const calls = blocks.filter(b => b.type === "tool_use");

    if (!calls.length || data.stop_reason !== "tool_use") {
      const text = blocks.filter(b => b.type === "text").map(b => b.text).join("").trim();
      return json({ text: text || "답을 만들지 못했습니다.", changed, actions,
                    usage: data.usage || null });
    }

    messages.push({ role: "assistant", content: blocks });
    const results = [];
    for (const c of calls) {
      const out = await runTool(c.name, c.input || {}, env, ctx, req);
      if (out.ok) {
        changed = true;
        if (c.name === "add_event") actions.push({ type: "add", rec: out.data });
        if (c.name === "delete_event") actions.push({ type: "delete", id: out.data.deleted });
      }
      results.push({
        type: "tool_result", tool_use_id: c.id,
        content: out.ok ? JSON.stringify(out.data) : ("오류: " + out.error),
        is_error: !out.ok,
      });
    }
    messages.push({ role: "user", content: results });
  }
  return json({ error: "정리하지 못했습니다. 좀 더 간단히 말해주세요.", changed, actions }, 200);
}

/* 모델이 부른 도구를 실제로 실행한다.
   모델이 시켰다고 그대로 믿지 않는다 — 등록 경로와 똑같은 검사를 다시 거친다. */
async function runTool(name, input, env, ctx, req) {
  if (!env.EVENTS) return { ok: false, error: "저장소가 연결되지 않았습니다" };
  try {
    if (name === "add_event") {
      const limited = await checkWriteLimit(env, req);
      if (limited) return { ok: false, error: limited };
      const r = await putEvent({
        title: input.title, date: input.date, time: input.time,
        member: input.member, note: input.note, by: input.by,
      }, env, ctx);
      return r.error ? { ok: false, error: r.error } : { ok: true, data: r };
    }
    if (name === "delete_event") {
      const id = String(input.id || "");
      if (!/^[A-Za-z0-9_-]{4,40}$/.test(id)) return { ok: false, error: "잘못된 id" };
      if (!(await env.EVENTS.get("ev:" + id))) return { ok: false, error: "이미 없는 일정입니다" };
      await env.EVENTS.delete("ev:" + id);
      return { ok: true, data: { deleted: id } };
    }
    return { ok: false, error: "모르는 도구입니다: " + name };
  } catch (e) {
    return { ok: false, error: "실행 중 오류: " + (e && e.message) };
  }
}

/* ---------------------------------------------------------------- 일정 자료 */

/* 자료는 서버가 직접 가져온다 — 페이지가 보내주면 아무나 바꿔 보낼 수 있다 */
async function loadData(env, ctx) {
  const url = env.DATA_URL;
  if (!url) throw new Error("DATA_URL 이 설정되지 않았습니다");
  const r = await fetch(url, { cf: { cacheTtl: DATA_TTL } });
  if (!r.ok) throw new Error("자료 " + r.status);
  return await r.json();
}

/* 자료를 두 덩어리로 나눈다.
   - 고정 자료(학회·발표·정기 일정·날짜표): 하루 종일 같다 → 프롬프트 캐시에 태운다
   - 등록된 일정: 방금 누가 넣었을 수 있다 → 캐시하지 않고 매번 새로 읽는다
   예전엔 둘을 묶어 60초 캐시했더니, 방금 등록한 것의 id 를 AI 가 몰라서
   "방금 넣은 거 지워줘" 를 못 했다. */
async function loadFacts(env, ctx) {
  const data = await loadData(env, ctx);
  let added = [];
  try { if (env.EVENTS) added = await listEvents(env); } catch {}
  return { fixed: summarize(data), live: summarizeAdded(added) };
}

/* 날짜표. 모델이 "다음 주 화요일" 을 직접 세면 틀린다 (실제로 9/20 을 토요일이라 한 적이 있다).
   아예 달력을 펼쳐 주고 거기서 고르게 한다. */
function dateTable(today) {
  const WD = ["일", "월", "화", "수", "목", "금", "토"];
  const base = new Date(today + "T00:00:00Z");
  const dow = base.getUTCDay();
  const thisMon = new Date(base); thisMon.setUTCDate(base.getUTCDate() - ((dow + 6) % 7));
  const L = [];
  for (let i = 0; i < DATE_DAYS; i++) {
    const t = new Date(base); t.setUTCDate(base.getUTCDate() + i);
    const iso = t.toISOString().slice(0, 10);
    const weeks = Math.floor((t - thisMon) / (7 * 86400000));
    const wk = weeks === 0 ? "이번 주" : weeks === 1 ? "다음 주" : weeks === 2 ? "다다음 주" : `${weeks}주 뒤`;
    const rel = i === 0 ? " · 오늘" : i === 1 ? " · 내일" : i === 2 ? " · 모레" : "";
    L.push(`${iso} ${t.getUTCMonth() + 1}/${t.getUTCDate()}(${WD[t.getUTCDay()]}) ${wk}${rel}`);
  }
  return L.join("\n");
}

function summarizeAdded(added) {
  const today = new Date(Date.now() + 9 * 3600000).toISOString().slice(0, 10);
  const rows = (added || []).filter(x => x.date >= today);
  if (!rows.length) return "<등록된 일정>\n(없음)\n</등록된 일정>";
  const WD = ["일", "월", "화", "수", "목", "금", "토"];
  const fd = iso => { const t = new Date(iso + "T00:00:00Z");
    return `${t.getUTCMonth() + 1}/${t.getUTCDate()}(${WD[t.getUTCDay()]})`; };
  return "<등록된 일정> (delete_event 로 지울 수 있는 것은 이것뿐)\n"
    + rows.map(x => `- id=${x.id} | ${fd(x.date)} ${x.time || "종일"} | ${x.title}`
      + (x.member && x.member.length ? ` | ${x.member.join(", ")}` : "")
      + (x.note ? ` | ${x.note}` : "") + (x.by ? ` | 등록: ${x.by}` : "")).join("\n")
    + "\n</등록된 일정>";
}

// 자료에는 kind 가 영어로 들어 있다. 그대로 넣으면 AI 가 "camera_ready" 라고 답한다.
const DUE_KO = { abstract: "초록 마감", full_paper: "논문 마감", earlybird: "사전등록 마감",
  registration: "등록 마감", camera_ready: "최종본 마감", event: "행사 개최" };
const TALK_KO = { oral: "구두발표", poster: "포스터", invited: "초청강연",
  keynote: "기조강연", chair: "좌장" };

/* note 에는 담당자용 메모가 섞여 있다 ("due 를 ... 형태로 고치세요").
   랩 사람에게 그대로 읽어주면 무슨 소린지 모른다. 그런 문장만 걷어낸다. */
function cleanNote(s) {
  if (!s) return "";
  return String(s).split(/[.·]\s*|—\s*/)
    .filter(part => !/고치세요|고쳐주세요|\.yaml|형태로|대략값/.test(part))
    .join(". ").replace(/\s*\(반복\)\s*$/, "").replace(/\s{2,}/g, " ").trim();
}

/* data.json 을 사람이 읽는 줄글로. 그대로 넣으면 토큰이 두 배로 든다. */
function summarize(d) {
  const today = new Date(Date.now() + 9 * 3600000).toISOString().slice(0, 10);
  const WD = ["일", "월", "화", "수", "목", "금", "토"];
  const dd = iso => {
    const n = Math.round((new Date(iso.slice(0, 10) + "T00:00:00Z")
      - new Date(today + "T00:00:00Z")) / 86400000);
    return n === 0 ? "오늘" : n > 0 ? "D-" + n : "D+" + -n;
  };
  const fd = iso => {
    const t = new Date(iso.slice(0, 10) + "T00:00:00Z");
    return `${t.getUTCMonth() + 1}/${t.getUTCDate()}(${WD[t.getUTCDay()]})`;
  };
  const L = [`오늘은 ${today} ${fd(today)} 입니다.`, `연구실: ${d.lab}`,
             `구성원: ${(d.members || []).join(", ")}`, ""];

  // 이미 지난 것은 넣지 않는다. 넣어두면 모델이 끝난 얘기를 굳이 짚는다.
  L.push("[학회 마감·행사] (앞으로 남은 것만)");
  (d.deadlines || []).filter(x => x.due && x.due >= today).sort((a, b) => a.due.localeCompare(b.due))
    .forEach(x => {
      const name = x.event.startsWith(x.society) ? x.event : `${x.society} ${x.event}`;
      const note = cleanNote(x.note);
      L.push(`- ${fd(x.due)} ${dd(x.due)} | ${name} | ${DUE_KO[x.kind] || x.kind}`
        + (note ? ` | ${note}` : ""));
    });

  L.push("", "[우리 랩 발표 (제1저자만 · 앞으로 남은 것만)]");
  (d.sessions || []).filter(x => !x.start || x.start.slice(0, 10) >= today)
    .sort((a, b) => (a.start || "").localeCompare(b.start || ""))
    .forEach(x => L.push(x.start
      ? `- ${fd(x.start)} ${x.start.length > 10 ? x.start.slice(11, 16) : ""} ${dd(x.start)}`
        + ` | ${x.member} | ${TALK_KO[x.kind] || x.kind} | ${x.title}`
        + (x.room ? ` | 장소 ${x.room}` : "") + ` | ${x.society}`
      : `- (날짜 미정) ${x.member} | ${x.title}`));

  L.push("", "[연구실 일정 · 정기 미팅]");
  (d.personal || []).filter(x => x.due >= today).slice(0, 40)
    .sort((a, b) => a.due.localeCompare(b.due))
    .forEach(x => {
      const note = cleanNote(x.note);
      L.push(`- ${fd(x.due)} ${x.due.length > 10 ? x.due.slice(11, 16) : ""} ${dd(x.due)}`
        + ` | ${x.title}`
        + (x.member && x.member.length ? ` | ${[].concat(x.member).join(", ")}` : "")
        + (note ? ` | ${note}` : ""));
    });

  L.push("", "연차 정보는 이 자료에 없습니다.");
  L.push("", "<날짜표> 날짜는 여기서 골라 YYYY-MM-DD 로 넘기세요. 직접 계산하지 마세요.",
         dateTable(today), "</날짜표>");
  return L.join("\n");
}

/* ---------------------------------------------------------------- 한도 */

/* IP당 시간 제한 + 전체 일일 상한. KV 가 없으면 제한 없이 통과시키되 그건 위험하다. */
async function checkLimits(env, req) {
  const kv = env.RATE;
  if (!kv) return null;
  const ip = req.headers.get("CF-Connecting-IP") || "unknown";
  const hour = new Date().toISOString().slice(0, 13);
  const day = new Date().toISOString().slice(0, 10);

  const ipKey = `ip:${ip}:${hour}`;
  const dayKey = `all:${day}`;
  const [ipN, dayN] = await Promise.all([kv.get(ipKey), kv.get(dayKey)]);

  if (Number(ipN || 0) >= (Number(env.PER_IP_HOUR) || PER_IP_HOUR))
    return "질문이 너무 많습니다. 한 시간 뒤에 다시 시도하세요.";
  if (Number(dayN || 0) >= (Number(env.DAILY_TOTAL) || DAILY_TOTAL))
    return "오늘 사용 한도를 넘었습니다. 내일 다시 시도하세요.";

  await Promise.all([
    kv.put(ipKey, String(Number(ipN || 0) + 1), { expirationTtl: 3900 }),
    kv.put(dayKey, String(Number(dayN || 0) + 1), { expirationTtl: 90000 }),
  ]);
  return null;
}

/* 등록은 돈이 들지 않지만, 막지 않으면 스팸으로 캘린더가 뒤덮인다 */
async function checkWriteLimit(env, req) {
  const kv = env.RATE;
  if (!kv) return null;
  const ip = req.headers.get("CF-Connecting-IP") || "unknown";
  const k = `w:${ip}:${new Date().toISOString().slice(0, 13)}`;
  const n = Number((await kv.get(k)) || 0);
  if (n >= (Number(env.WRITE_PER_IP_HOUR) || WRITE_PER_IP_HOUR))
    return "등록이 너무 많습니다. 한 시간 뒤에 다시 시도하세요.";
  await kv.put(k, String(n + 1), { expirationTtl: 3900 });
  return null;
}
