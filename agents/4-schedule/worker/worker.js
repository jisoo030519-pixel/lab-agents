/**
 * 연구실 일정판 AI 중계 서버 (Cloudflare Worker)
 *
 * 공개 페이지에 API 키를 심으면 링크를 아는 누구나 그 키로 요금을 태울 수 있다.
 * 그래서 키는 여기(서버)에만 둔다. 페이지는 질문만 보내고, 답만 받는다.
 *
 * 요금이 새지 않게 하는 장치가 이 파일의 절반이다:
 *  - 일정 자료를 페이지에서 받지 않고 서버가 직접 가져온다
 *    (받으면 아무나 긴 글을 밀어넣어 토큰을 태울 수 있다)
 *  - 질문 길이 제한, 답 길이 제한
 *  - IP당 시간 제한 + 전체 일일 상한
 *  - 우리 사이트에서 온 요청만 받는다
 *
 * 배포: wrangler deploy   (설정은 wrangler.toml)
 */

const MODEL = "claude-sonnet-5";
const MAX_Q = 400;          // 질문 글자 수 상한
const MAX_TOKENS = 700;     // 답 길이 상한
const PER_IP_HOUR = 30;     // IP당 시간당 질문 수
const DAILY_TOTAL = 500;    // 전체 하루 상한 (요금 폭주 차단)
const DATA_TTL = 600;       // 일정 자료 캐시 (초)

const SYSTEM = `당신은 인천대학교 에너지공정시스템 연구실의 일정 안내 담당입니다.
아래 <일정자료> 안에 있는 내용만으로 답하세요.

규칙:
- 자료에 없는 것은 "자료에 없습니다" 라고 답하고, 절대 지어내지 마세요.
  날짜·시각·장소·발표 제목을 추측해서 만들어내면 안 됩니다.
- 날짜는 "9/18(금)" 처럼 요일까지 붙여 쓰고, 남은 날은 자료의 D-day 를 그대로 쓰세요.
  요일을 직접 계산하지 마세요 — 자료에 적힌 것을 씁니다.
- 짧게 답하세요. 목록이면 한 줄에 하나씩.
- 이 화면에서는 일정을 등록하거나 바꿀 수 없습니다. 그런 요청이 오면
  "담당자에게 말해주세요" 라고 안내하세요. 등록했다고 답하면 안 됩니다.
- 연차 정보는 이 자료에 없습니다. 물으면 담당자에게 문의하라고 하세요.
- 일정과 무관한 질문(번역, 코드, 잡담 등)은 정중히 거절하세요.`;

export default {
  async fetch(req, env, ctx) {
    const origin = req.headers.get("Origin") || "";
    const allowed = (env.ALLOWED_ORIGINS || "").split(",").map(s => s.trim()).filter(Boolean);
    const okOrigin = allowed.length === 0 || allowed.includes(origin);
    const cors = {
      "Access-Control-Allow-Origin": okOrigin && origin ? origin : (allowed[0] || "*"),
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
      "Vary": "Origin",
    };
    const json = (obj, status = 200) =>
      new Response(JSON.stringify(obj), {
        status, headers: { ...cors, "Content-Type": "application/json; charset=utf-8" },
      });

    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    if (req.method !== "POST") return json({ error: "POST 로 보내주세요" }, 405);
    if (!okOrigin) return json({ error: "허용되지 않은 곳에서의 요청입니다" }, 403);
    if (!env.ANTHROPIC_API_KEY) return json({ error: "서버에 API 키가 설정되지 않았습니다" }, 500);

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
          system: [
            { type: "text", text: SYSTEM },
            // 자료는 매번 같으므로 캐시해서 토큰 값을 아낀다
            { type: "text", text: "<일정자료>\n" + facts + "\n</일정자료>",
              cache_control: { type: "ephemeral" } },
          ],
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
    const text = (data.content || [])
      .filter(b => b.type === "text").map(b => b.text).join("").trim();
    return json({ text: text || "답을 만들지 못했습니다.", usage: data.usage || null });
  },
};

/* 일정 자료는 서버가 직접 가져온다 — 페이지가 보내주면 아무나 바꿔 보낼 수 있다 */
async function loadFacts(env, ctx) {
  const url = env.DATA_URL;
  if (!url) throw new Error("DATA_URL 이 설정되지 않았습니다");
  const cache = caches.default;
  const ckey = new Request(url + "#facts");
  const hit = await cache.match(ckey);
  if (hit) return await hit.text();

  const r = await fetch(url, { cf: { cacheTtl: DATA_TTL } });
  if (!r.ok) throw new Error("자료 " + r.status);
  const facts = summarize(await r.json());
  const put = new Response(facts, { headers: { "Cache-Control": "max-age=" + DATA_TTL } });
  ctx.waitUntil(cache.put(ckey, put.clone()));
  return facts;
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

  L.push("[학회 마감·행사]");
  (d.deadlines || []).filter(x => x.due).sort((a, b) => a.due.localeCompare(b.due))
    .forEach(x => {
      const name = x.event.startsWith(x.society) ? x.event : `${x.society} ${x.event}`;
      const note = cleanNote(x.note);
      L.push(`- ${fd(x.due)} ${dd(x.due)} | ${name} | ${DUE_KO[x.kind] || x.kind}`
        + (note ? ` | ${note}` : ""));
    });

  L.push("", "[우리 랩 발표 (제1저자만)]");
  (d.sessions || []).sort((a, b) => (a.start || "").localeCompare(b.start || ""))
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
  return L.join("\n");
}

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
