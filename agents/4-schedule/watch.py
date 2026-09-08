#!/usr/bin/env python3
"""학회 일정 에이전트 — 상태 저장 / 변경 감지 / PDF 스캔 / 캘린더 · 리포트 생성.

페이지와 PDF를 읽고 의미를 해석하는 일은 에이전트(LLM)가 하고,
이 스크립트는 결정적으로 처리해야 하는 부분만 담당한다.

  python watch.py list                       감시 대상 학회 + 연구실 명단
  python watch.py scan-pdf 프로그램.pdf       PDF에서 우리 사람 나오는 페이지 추출
  python watch.py upsert-deadlines f.json     마감 병합 → 신규/변경 출력
  python watch.py upsert-sessions  f.json     발표 일정 병합 → 신규/변경 출력
  python watch.py month [YYYY-MM]             이번 달(또는 지정 달) 주요 일정 브리핑
  python watch.py leave                       연차 현황 (남은 연차 · 사용 내역)
  python watch.py rotation [YYYY-MM-DD]       다음 개인미팅 순서
  python watch.py report                      통합 리포트 (LATEST.md)
  python watch.py ics                         캘린더 파일 생성 (전체 + 개인별)
  python watch.py web                         대시보드용 data.json 생성
  python watch.py sync                        data.json 을 dashboard.html 에 주입
  python watch.py publish                     공유 DB(shared/lab.db)에 일정 내보내기
  python watch.py site                        공개용 정적 사이트 생성 (docs/)
  python watch.py show                        저장된 전체 데이터
  python watch.py prune-members               명단에서 빠진 사람의 발표 정리
  python watch.py reset                       수집된 일정 전부 비우기 (설정은 유지)
"""
import hashlib
import json
import random
import sqlite3
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent

# 연구실 플랫폼(lab-agents)의 shared/ 를 찾는다.
# 명단은 5개 에이전트가 모두 쓰므로 여기가 유일한 원본이다.
def _find_shared(start):
    for d in [start] + list(start.parents):
        cand = d / "shared"
        if (cand / "members.yaml").exists():
            return cand
    return start          # 독립 실행 시 폴백

SHARED = _find_shared(ROOT)
TARGETS = ROOT / "targets.yaml"
MEMBERS = SHARED / "members.yaml"
PERSONAL = ROOT / "personal.yaml"
LEAVE = ROOT / "leave.yaml"
OVERRIDES = ROOT / "overrides.yaml"
SHARED_DB = SHARED / "lab.db"
STATE = ROOT / "state.json"
REPORTS = ROOT / "reports"
CALENDARS = ROOT / "calendars"
SCANS = ROOT / "scans"

KST_OFFSET = timedelta(hours=9)

DEADLINE_LABEL = {
    "abstract": "초록 마감",
    "full_paper": "논문 마감",
    "earlybird": "사전등록 마감",
    "registration": "등록 마감",
    "camera_ready": "최종본 마감",
    "event": "행사 개최",
}
SESSION_LABEL = {
    "oral": "구두발표",
    "poster": "포스터",
    "invited": "초청강연",
    "keynote": "기조강연",
    "chair": "좌장",
}
PERSONAL_LABEL = {
    "revision": "논문 수정본",
    "submission": "논문 투고",
    "review": "리뷰",
    "report": "보고서",
    "proposal": "제안서",
    "seminar": "세미나",
    "meeting": "미팅",
    "etc": "기타",
}


# ---------------------------------------------------------------- 공통

def load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_members():
    cfg = load_yaml(MEMBERS)
    return cfg.get("lab", "연구실"), cfg.get("members", []) or []


def load_affiliations():
    """소속 표기. 이름 표기는 로마자 변환이 제각각이라 자주 빗나가므로,
    소속으로 한 번 더 훑는 것이 실제로 더 잘 잡는다."""
    return load_yaml(MEMBERS).get("affiliations", []) or []


def load_state():
    s = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    s.setdefault("deadlines", {})
    s.setdefault("sessions", {})
    s.setdefault("archive", [])
    return s


def save_state(state):
    STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def iso_str(value):
    """YAML이 date/datetime으로 읽어버린 값을 문자열 표기로 되돌린다."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M") if (value.hour or value.minute) \
            else value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    return "" if value is None else str(value).strip()


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_dt(value):
    """'2026-10-22T14:30' 또는 '2026-10-22' 를 받아 (값, 종일여부)."""
    if not value:
        return None, False
    v = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(v, fmt), False
        except ValueError:
            pass
    d = parse_date(v)
    return (d, True) if d else (None, False)


def days_left(value):
    d = parse_date(value)
    return None if d is None else (d - date.today()).days


def add_months(d, n):
    """월 단위 이동. 말일이 없는 달은 그 달의 말일로 맞춘다 (1/31 + 1개월 = 2/28)."""
    y, m = divmod((d.year * 12 + d.month - 1) + n, 12)
    m += 1
    for day in range(d.day, 27, -1):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    return date(y, m, min(d.day, 28))


def rotation_for(series_key, members, index):
    """그 회차의 개인미팅 순서. 사다리타기 대신 쓴다.

    매주 독립적으로 뽑으면 한 학기 동안 순번이 쏠린다 — 누구는 1번만 네 번,
    누구는 한 번도 못 한다. 그래서 학기 초에 한 번 무작위로 섞고,
    매주 한 칸씩 밀어서 돌린다. N명이면 N주에 걸쳐 모두가 모든 순번을 한 번씩 맡는다.

    같은 회차·같은 명단이면 언제 돌려도 같은 순서가 나온다.
    매번 달라지면 "아까랑 다른데?" 소리가 나와서 못 쓴다.
    """
    if not members:
        return []
    seed = hashlib.md5(("%s|%s" % (series_key, ",".join(members))).encode("utf-8")).hexdigest()
    base = list(members)
    random.Random(int(seed[:16], 16)).shuffle(base)
    k = index % len(base)
    return base[k:] + base[:k]


def load_overrides():
    """정기 일정의 그 주만 바꾸는 예외. {(제목, 날짜): {...}} 로 돌려준다."""
    if not OVERRIDES.exists():
        return {}
    out = {}
    for it in (load_yaml(OVERRIDES).get("items") or []):
        t, d = str(it.get("title") or "").strip(), iso_str(it.get("date"))[:10]
        act = str(it.get("action") or "").lower()
        if not t or not parse_date(d) or act not in ("skip", "move"):
            continue
        out[(t, d)] = {"action": act, "to": iso_str(it.get("to")),
                       "note": str(it.get("note") or "")}
    return out


def load_personal():
    """personal.yaml 을 읽어 반복 일정을 펼친 목록으로 돌려준다.

    overrides.yaml 의 그 주 예외(휴강·시간 변경)를 적용한 뒤 돌려준다.
    """
    if not PERSONAL.exists():
        return []
    ov = load_overrides()

    def apply(item):
        """예외를 적용. 취소된 회차면 None."""
        key = (item["title"], item["due"][:10])
        rule = ov.get(key)
        if not rule:
            return item
        if rule["action"] == "skip":
            return None
        to = rule["to"]
        if not parse_date(to):
            return item
        item = dict(item)
        item["due"] = to if len(to) > 10 else to + item["due"][10:]
        item["moved_from"] = key[1]
        item["repeat"] = ""          # 일회성이므로 정기 블록에 접히면 안 된다
        if rule["note"]:
            item["note"] = "; ".join(x for x in [rule["note"], item.get("note", "")] if x)
        return item

    out = []
    for it in (load_yaml(PERSONAL).get("items") or []):
        if not it.get("title") or not it.get("due"):
            continue
        base = dict(it)
        base.setdefault("kind", "etc")
        base.setdefault("note", "")
        # member 는 한 명(문자열)도, 여러 명(리스트)도 쓸 수 있다.
        # 빈 값이면 랩 공통 — 모두의 캘린더에 들어간다.
        who = base.get("member") or []
        base["member"] = [who] if isinstance(who, str) and who.strip() else (
            [w for w in who if str(w).strip()] if isinstance(who, list) else [])
        rotate = bool(base.pop("rotate", False))
        # YAML은 따옴표 없는 날짜를 date/datetime 객체로 읽는다. 문자열로 통일한다.
        base["due"] = iso_str(base["due"])
        rep = str(base.pop("repeat", "") or "").lower()
        until = parse_date(base.pop("until", None))
        if rep in ("monthly", "weekly") and until is not None:
            base["repeat"] = rep
        series = "%s|%s" % (base["title"], base["due"][:10])   # 학기 내내 고정
        if rotate:
            base["order"] = rotation_for(series, base["member"], 0)
        first_item = apply(base)
        if first_item is not None:
            out.append(first_item)
        if rep not in ("monthly", "weekly") or until is None:
            continue
        first, _ = parse_dt(base["due"])
        if first is None:
            continue
        d0 = first.date() if isinstance(first, datetime) else first
        tail = base["due"][10:]          # 'T16:00' 같은 시각 부분 보존
        step = 1
        while True:
            nxt = (add_months(d0, step) if rep == "monthly"
                   else d0 + timedelta(days=7 * step))
            if nxt > until:
                break
            clone = dict(base)
            clone["due"] = nxt.isoformat() + tail
            clone["note"] = (base["note"] + " (반복)").strip()
            if rotate:
                clone["order"] = rotation_for(series, base["member"], step)
            got = apply(clone)
            if got is not None:
                out.append(got)
            step += 1
    return out


def order_line(e):
    """개인미팅 순서를 한 줄로. 순서가 없는 일정이면 빈 문자열."""
    o = e.get("order") or []
    return "순서: " + " → ".join(o) if o else ""


def load_leave():
    """leave.yaml 을 읽어 사람별 연차 현황과 날짜별 연차 목록을 돌려준다.

    남은 연차 = total − used_before 합계 − leaves 합계 (반차는 0.5)
    """
    if not LEAVE.exists():
        return {"year": date.today().year, "people": [], "leaves": []}
    cfg = load_yaml(LEAVE)
    year = cfg.get("year") or date.today().year

    leaves = []
    for it in (cfg.get("leaves") or []):
        d = parse_date(iso_str(it.get("date")))
        if not it.get("name") or d is None:
            continue
        leaves.append({"name": it["name"], "date": d.isoformat(),
                       "days": 0.5 if it.get("half") else 1.0,
                       "half": bool(it.get("half")), "note": it.get("note", "")})
    leaves.sort(key=lambda x: (x["date"], x["name"]))

    people = []
    for pp in (cfg.get("people") or []):
        # used_before 의 키는 YAML이 날짜로 읽을 수 있으므로 문자열로 통일
        before = {}
        for k, v in (pp.get("used_before") or {}).items():
            before[iso_str(k)[:7]] = float(v or 0)
        mine = [l for l in leaves if l["name"] == pp["name"]]
        used = sum(before.values()) + sum(l["days"] for l in mine)
        total = float(pp.get("total") or 0)
        people.append({
            "name": pp["name"], "total": total, "used": used,
            "left": total - used, "used_before": before,
            "dated": mine,
        })
    return {"year": year, "people": people, "leaves": leaves}


def timeline(include_undated=False):
    """마감 · 발표 · 개인 일정을 하나의 정렬된 목록으로 합친다.

    반환 항목 공통 필드: date(YYYY-MM-DD) / time / title / label / member / where / note / url
    """
    state = load_state()
    rows = []

    for e in state["deadlines"].values():
        # event 이름이 이미 학회명으로 시작하면 겹쳐 쓰지 않는다 ("NAT NAT 2026" 방지)
        title = e["event"] if e["event"].startswith(e["society"]) \
            else "%s %s" % (e["society"], e["event"])
        rows.append({
            "src": "due", "date": (e.get("due") or "")[:10], "time": None,
            "title": title, "label": DEADLINE_LABEL.get(e["kind"], e["kind"]),
            "member": "", "where": "", "note": e.get("note", ""),
            "url": e.get("url", ""), "changed": e.get("prev_due"),
            "end": e.get("end"), "kind": e["kind"],
        })
    for e in state["sessions"].values():
        dt, allday = parse_dt(e.get("start"))
        rows.append({
            "src": "talk", "date": (e.get("start") or "")[:10],
            "time": None if allday or dt is None else dt.strftime("%H:%M"),
            "title": e["title"], "label": SESSION_LABEL.get(e["kind"], e["kind"]),
            "member": e.get("member", ""), "where": e.get("room", ""),
            "note": e.get("note", ""), "url": "", "changed": e.get("prev_start"),
            "sub": "%s %s" % (e["society"], e["event"]),
        })
    for e in load_personal():
        dt, allday = parse_dt(e.get("due"))
        rows.append({
            "src": "mine", "date": (e.get("due") or "")[:10],
            "time": None if allday or dt is None else dt.strftime("%H:%M"),
            "title": e["title"], "label": PERSONAL_LABEL.get(e["kind"], e["kind"]),
            "member": ", ".join(e.get("member") or []), "where": "",
            "note": "; ".join(x for x in [order_line(e), e.get("note", "")] if x),
            "url": "", "changed": None, "repeat": e.get("repeat", ""),
        })

    for l in load_leave()["leaves"]:
        rows.append({
            "src": "leave", "date": l["date"], "time": None,
            "title": "%s 연차%s" % (l["name"], " (반차)" if l["half"] else ""),
            "label": "연차", "member": l["name"], "where": "",
            "note": l.get("note", ""), "url": "", "changed": None, "repeat": "",
        })

    dated = [r for r in rows if parse_date(r["date"])]
    dated.sort(key=lambda r: (r["date"], r["time"] or ""))
    if not include_undated:
        return dated
    return dated, [r for r in rows if not parse_date(r["date"])]


# ---------------------------------------------------------------- list

def cmd_list():
    cfg = load_yaml(TARGETS)
    active = [t for t in cfg.get("targets", []) if t.get("enabled")]
    lab, members = load_members()

    print("# 오늘: " + date.today().isoformat() + "\n")
    print("## 감시 대상 학회 %d건" % len(active))
    if not active:
        print("  (없음 — targets.yaml 에서 enabled: true 로 켜주세요)")
    for t in active:
        print("\n- " + t["name"])
        print("    url:   " + t["url"])
        print("    watch: " + ", ".join(t.get("watch") or []))
        if t.get("note"):
            print("    note:  " + t["note"])

    print("\n## %s 명단 %d명" % (lab, len(members)))
    for m in members:
        print("- %s (%s) — 표기: %s"
              % (m["name"], m.get("role", ""), " / ".join(m.get("aliases") or [])))


# ---------------------------------------------------------------- scan-pdf

def norm(s):
    """비교용 정규화: 유니코드 정규화 + 공백 제거 + 소문자."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).lower()


def cmd_scan_pdf(pdf_path, context_chars=1800):
    import pdfplumber

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise SystemExit("파일 없음: %s" % pdf_path)

    lab, members = load_members()
    probes = []
    for m in members:
        for a in [m["name"]] + list(m.get("aliases") or []):
            if a and a.strip():
                probes.append((m["name"], norm(a)))
    if not probes:
        raise SystemExit("members.yaml 에 명단이 비어 있습니다.")

    affs = [(a, norm(a)) for a in load_affiliations()]
    hits, aff_hits, pages_text = {}, {}, {}
    empty = 0
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                empty += 1
                continue
            flat = norm(text)
            found = set(name for name, alias in probes if alias and alias in flat)
            afound = set(a for a, na in affs if na and na in flat)
            if found:
                hits[i] = found
            if afound:
                aff_hits[i] = afound
            if found or afound:
                pages_text[i] = text

    SCANS.mkdir(exist_ok=True)
    out = SCANS / (pdf_path.stem + ".hits.txt")
    bar = "=" * 70
    with out.open("w", encoding="utf-8") as f:
        f.write("# %s — 전체 %d쪽, 이름 %d쪽 / 소속 %d쪽에서 발견\n"
                % (pdf_path.name, total, len(hits), len(aff_hits)))
        f.write("# 스캔 시각: %s\n\n" % datetime.now().isoformat(timespec="seconds"))
        for p in sorted(pages_text):
            tags = []
            if p in hits:
                tags.append("이름: " + ", ".join(sorted(hits[p])))
            if p in aff_hits:
                tags.append("소속: " + ", ".join(sorted(aff_hits[p])))
            f.write("\n%s\n[p.%d] %s\n%s\n" % (bar, p, " | ".join(tags), bar))
            f.write(pages_text[p][:context_chars] + "\n")

    print("전체 %d쪽 (텍스트 없는 쪽 %d)" % (total, empty))
    print("이름으로 %d쪽, 소속으로 %d쪽 발견" % (len(hits), len(aff_hits)))
    for p in sorted(pages_text):
        parts = []
        if p in hits:
            parts.append("이름 " + ", ".join(sorted(hits[p])))
        if p in aff_hits:
            parts.append("소속 " + ", ".join(sorted(aff_hits[p])))
        print("  p.%-4d %s" % (p, " | ".join(parts)))

    only_aff = sorted(set(aff_hits) - set(hits))
    if only_aff:
        print("\n⚠️  소속만 잡힌 쪽: %s" % ", ".join("p.%d" % p for p in only_aff))
        print("    영문 표기가 members.yaml 의 aliases 와 다를 수 있습니다. 직접 확인하세요.")
    if empty == total:
        print("\n⚠️  이 PDF는 텍스트가 전혀 없습니다 (스캔 이미지).")
        print("    0건은 '없다'가 아니라 '못 읽었다'입니다. 사람이 직접 읽어야 합니다.")
    elif not pages_text:
        print("\n한 명도 못 찾았습니다. aliases 표기가 프로그램과 다를 수 있습니다.")
    print("\n추출 결과: %s" % out)


# ---------------------------------------------------------------- upsert

DEADLINE_REQ = ("society", "event", "kind", "due", "url")
SESSION_REQ = ("society", "event", "member", "title", "kind", "start")


def _load_items(path):
    """JSON 도 YAML 도 받는다.

    이 저장소의 다른 설정 파일은 전부 YAML 인데 여기만 JSON 이라
    손으로 한 건 넣을 때마다 파서 오류가 났다. 둘 다 받게 한다.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError:
        data = yaml.safe_load(text)
    if data is None:
        return []
    items = data.get("items", []) if isinstance(data, dict) else data
    # YAML 은 `due: 2026-09-18` 을 date 객체로 읽는다. 뒤에서 문자열로 자르므로
    # 여기서 ISO 문자열로 통일해 둔다.
    for e in items:
        for k in ("due", "end", "start", "finish"):
            if k in e:
                e[k] = iso_str(e[k])
    return items


def _line(e, label_map, cmpfield):
    head = "%s · %s" % (e["society"], e["event"])
    if e.get("member"):
        head += " · " + e["member"]
    return "%s · %s → %s" % (head, label_map.get(e["kind"], e["kind"]), e.get(cmpfield))


def _upsert(bucket, items, required, keyfn, cmpfield, label_map, kindname):
    state = load_state()
    store = state[bucket]
    today = date.today().isoformat()
    new, changed, unchanged = [], [], 0

    for e in items:
        missing = [f for f in required if f not in e]
        if missing:
            raise SystemExit("필수 필드 누락 %s: %s" % (missing, e))
        k = keyfn(e)
        prev = store.get(k)
        if prev is None:
            e.update(first_seen=today, last_seen=today, last_changed=today)
            store[k] = e
            new.append(e)
        elif str(prev.get(cmpfield)) != str(e[cmpfield]):
            e["first_seen"] = prev.get("first_seen", today)
            e["last_seen"] = today
            e["last_changed"] = today
            e["prev_" + cmpfield] = prev.get(cmpfield)
            store[k] = e
            changed.append(e)
        else:
            prev.update(dict((kk, vv) for kk, vv in e.items() if vv not in (None, "")))
            prev["last_seen"] = today
            unchanged += 1

    moved = 0
    for k in list(store):
        dl = days_left(store[k].get(cmpfield))
        if dl is not None and dl < -30:
            state["archive"].append(store.pop(k))
            moved += 1

    save_state(state)
    print("[%s] 신규 %d / 변경 %d / 그대로 %d / 아카이브 %d"
          % (kindname, len(new), len(changed), unchanged, moved))
    for e in new:
        print("  [신규] " + _line(e, label_map, cmpfield))
    for e in changed:
        print("  [변경] %s  (이전: %s)"
              % (_line(e, label_map, cmpfield), e.get("prev_" + cmpfield)))


def cmd_upsert_deadlines(path):
    _upsert(
        "deadlines", _load_items(path), DEADLINE_REQ,
        lambda e: "|".join([e["society"], e["event"], e["kind"]]),
        "due", DEADLINE_LABEL, "마감",
    )


def cmd_upsert_sessions(path):
    """발표 일정 병합.

    규칙: **연구실 구성원이 제1저자인 발표만** 기록한다.
    남의 논문에 우리 사람이 공저자로 들어간 건은 우리 랩 일정이 아니다.
    경고만 띄우면 그냥 들어가 버리므로 아예 막는다.
    """
    _, members = load_members()
    known = set(m["name"] for m in members)
    items = _load_items(path)
    kept, rejected = [], []
    for e in items:
        (kept if e.get("member") in known else rejected).append(e)
    for e in rejected:
        print("  ✗ 제외: 제1저자 '%s' 가 명단에 없음 — %s"
              % (e.get("member"), e.get("title", "")[:60]))
    if rejected:
        print("    (연구실 구성원이 제1저자인 발표만 기록합니다)\n")
    if not kept:
        print("기록할 발표가 없습니다.")
        return
    items = kept
    _upsert(
        "sessions", items, SESSION_REQ,
        lambda e: "|".join([e["society"], e["event"], e["member"], e["title"][:60]]),
        "start", SESSION_LABEL, "발표",
    )


# ---------------------------------------------------------------- report

BUCKETS = ["긴급 — 7일 이내", "임박 — 30일 이내", "예정", "지난 마감", "날짜 미정 · 확인 필요"]


def bucket_of(dl):
    if dl is None:
        return 4
    if dl < 0:
        return 3
    return 0 if dl <= 7 else (1 if dl <= 30 else 2)


def cmd_report():
    state = load_state()
    lab, _ = load_members()
    today = date.today()
    L = ["# %s 연구·학회 일정 — %s" % (lab, today.isoformat()), ""]

    deadlines = list(state["deadlines"].values())
    sessions = list(state["sessions"].values())
    mine = load_personal()
    # 매주 반복은 회차를 다 세면 숫자가 부풀어 오해를 부른다. 종류 단위로 센다.
    weekly_kinds = sorted(set(e["title"] for e in mine if e.get("repeat") == "weekly"))
    mine_once = [e for e in mine if e.get("repeat") != "weekly"]
    urgent = sum(1 for r in timeline()
                 if r.get("repeat") != "weekly" and bucket_of(days_left(r["date"])) == 0)
    tail = " · 매주 정기 **%d건**" % len(weekly_kinds) if weekly_kinds else ""
    L.append("마감 **%d건** · 발표 **%d건** · 연구 일정 **%d건**%s — 7일 이내 **%d건**"
             % (len(deadlines), len(sessions), len(mine_once), tail, urgent))
    L.append("")

    upcoming = [r for r in timeline()
                if r.get("repeat") != "weekly" and days_left(r["date"]) is not None
                and 0 <= days_left(r["date"]) <= 14]
    if upcoming:
        L += ["## 2주 안에 할 일", ""]
        for r in upcoming:
            dl = days_left(r["date"])
            when = "오늘" if dl == 0 else "D-%d" % dl
            what = r["title"] if r["src"] != "due" else "%s %s" % (r["title"], r["label"])
            if r["src"] == "talk":
                what = "%s %s — %s" % (r["member"], r["label"], r["title"])
            extra = " · " + r["note"] if r["note"] else ""
            L.append("- **%s** (%s) %s%s" % (when, r["date"][5:].replace("-", "/"), what, extra))
        L.append("")

    if sessions:
        L += ["## 우리 랩 발표 일정", "",
              "| 일시 | 이름 | 구분 | 제목 | 장소 | 학회 |", "|---|---|---|---|---|---|"]
        for e in sorted(sessions, key=lambda x: str(x.get("start") or "9999")):
            dt, allday = parse_dt(e.get("start"))
            when = "미정" if dt is None else (
                dt.isoformat() if allday else dt.strftime("%m/%d %H:%M"))
            L.append("| %s | **%s** | %s | %s | %s | %s |"
                     % (when, e["member"], SESSION_LABEL.get(e["kind"], e["kind"]),
                        e["title"], e.get("room", ""), e["society"]))
        L.append("")

    if deadlines:
        groups = dict((i, []) for i in range(5))
        for e in deadlines:
            groups[bucket_of(days_left(e.get("due")))].append(e)
        for i in range(5):
            rows = groups[i]
            if not rows:
                continue
            rows.sort(key=lambda e: (parse_date(e.get("due")) or date.max, e["society"]))
            L += ["## 마감 · " + BUCKETS[i], "",
                  "| D-day | 마감일 | 학회 | 행사 | 구분 | 비고 |", "|---|---|---|---|---|---|"]
            for e in rows:
                dl = days_left(e.get("due"))
                # 남은 날은 D-n, 지난 날은 D+n (한국 관행)
                dday = "미정" if dl is None else (
                    "D-day" if dl == 0 else ("D-%d" % dl if dl > 0 else "D+%d" % -dl))
                soc = ("[%s](%s)" % (e["society"], e["url"])) if e.get("url") else e["society"]
                note = (e.get("note") or "").replace("|", "/")
                if e.get("prev_due") and e.get("last_changed") == today.isoformat():
                    note = (note + " ⚠️ %s에서 변경됨" % e["prev_due"]).strip()
                L.append("| %s | %s | %s | %s | %s | %s |"
                         % (dday, e.get("due") or "미정", soc, e["event"],
                            DEADLINE_LABEL.get(e["kind"], e["kind"]), note))
            L.append("")

    if weekly_kinds:
        WD = "월화수목금토일"
        L += ["## 학기 정기 일정 (매주)", ""]
        seen = set()
        for e in mine:
            if e.get("repeat") != "weekly" or e["title"] in seen:
                continue
            seen.add(e["title"])
            dt, allday = parse_dt(e["due"])
            d = dt.date() if isinstance(dt, datetime) else dt
            tm = "" if allday else " %s" % dt.strftime("%H:%M")
            who = ", ".join(e.get("member") or []) or "랩 전체"
            L.append("- **%s요일**%s — %s (%s)%s"
                     % (WD[d.weekday()], tm, e["title"], who,
                        " · " + e["note"] if e.get("note") else ""))
        L.append("")

    if mine_once:
        L += ["## 연구 일정 (논문 · 과제)", "",
              "| 날짜 | 구분 | 내용 | 담당 | 비고 |", "|---|---|---|---|---|"]
        for e in sorted(mine_once, key=lambda x: str(x.get("due"))):
            dt, allday = parse_dt(e.get("due"))
            when = "미정" if dt is None else (
                dt.strftime("%m/%d") if allday else dt.strftime("%m/%d %H:%M"))
            L.append("| %s | %s | %s | %s | %s |"
                     % (when, PERSONAL_LABEL.get(e["kind"], e["kind"]), e["title"],
                        ", ".join(e.get("member") or []) or "랩 공통",
                        (e.get("note") or "").replace("|", "/")))
        L.append("")

    lv = load_leave()
    if lv["people"]:
        L += ["## 연차 현황 (%d년)" % lv["year"], "",
              "| 이름 | 총 휴가 | 사용 | 남음 |", "|---|---|---|---|"]
        for p in lv["people"]:
            L.append("| %s | %g | %g | **%g** |"
                     % (p["name"], p["total"], p["used"], p["left"]))
        L.append("")
        up = [l for l in lv["leaves"] if (days_left(l["date"]) or -1) >= 0]
        if up:
            L.append("예정: " + ", ".join(
                "%s %s(%s)" % (l["date"][5:].replace("-", "/"), l["name"],
                               "반차" if l["half"] else "연차") for l in up))
            L.append("")

    if not deadlines and not sessions and not mine:
        L.append("저장된 일정이 없습니다. 먼저 수집을 실행하세요.")

    md = "\n".join(L).rstrip() + "\n"
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / (today.isoformat() + ".md")).write_text(md, encoding="utf-8")
    (ROOT / "LATEST.md").write_text(md, encoding="utf-8")
    print(md)


# ---------------------------------------------------------------- ics

def fold(line):
    """RFC5545 폴딩. 한글이 깨지지 않도록 문자 단위로 누적하며 바이트 길이를 센다."""
    if len(line.encode("utf-8")) <= 73:
        return line
    out, cur = [], b""
    for ch in line:
        e = ch.encode("utf-8")
        if len(cur) + len(e) > 73:
            out.append(cur.decode("utf-8"))
            cur = b" "
        cur += e
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def esc(s):
    return (str(s or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def uid(prefix, *parts):
    """일정 내용에서 만든 고정 UID.

    배열 순서로 UID를 만들면 항목이 하나 늘 때 뒤쪽 UID가 전부 밀려서, 캘린더에 다시
    가져올 때 엉뚱한 일정을 덮어쓴다. 파이썬 hash()는 실행마다 값이 바뀌므로 쓸 수 없다.
    """
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return "%s-%s@conf-agent" % (prefix, hashlib.md5(raw).hexdigest()[:16])


def utc(dt):
    return (dt - KST_OFFSET).strftime("%Y%m%dT%H%M%SZ")


def vevent(uid, summary, dtstart, allday, end=None, location="", desc="", alarms=(),
           span_end=None):
    L = ["BEGIN:VEVENT", "UID:" + uid,
         "DTSTAMP:" + datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")]
    if allday:
        # 종일 일정의 DTEND는 배타적이므로 마지막 날 +1일을 넣는다.
        last = span_end if (span_end and span_end >= dtstart) else dtstart
        L.append("DTSTART;VALUE=DATE:" + dtstart.strftime("%Y%m%d"))
        L.append("DTEND;VALUE=DATE:" + (last + timedelta(days=1)).strftime("%Y%m%d"))
    else:
        L.append("DTSTART:" + utc(dtstart))
        L.append("DTEND:" + utc(end or dtstart + timedelta(minutes=20)))
    L.append("SUMMARY:" + esc(summary))
    if location:
        L.append("LOCATION:" + esc(location))
    if desc:
        L.append("DESCRIPTION:" + esc(desc))
    for trig, msg in alarms:
        L += ["BEGIN:VALARM", "TRIGGER:" + trig, "ACTION:DISPLAY",
              "DESCRIPTION:" + esc(msg), "END:VALARM"]
    L.append("END:VEVENT")
    return L


def build_ics(name, events):
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//conf-agent//KR//",
         "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:" + esc(name),
         "X-WR-TIMEZONE:Asia/Seoul"]
    L += events
    L.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in L) + "\r\n"


def cmd_ics():
    state = load_state()
    lab, members = load_members()
    CALENDARS.mkdir(exist_ok=True)
    # 명단에서 빠진 사람의 옛 캘린더가 남아 잘못 배포되지 않도록 매번 새로 만든다.
    for old in CALENDARS.glob("*.ics"):
        old.unlink()

    shared = []
    per_member = dict((m["name"], []) for m in members)

    for e in state["deadlines"].values():
        d = parse_date(e.get("due"))
        if d is None:
            continue
        label = DEADLINE_LABEL.get(e["kind"], e["kind"])
        head = e["event"] if e["event"].startswith(e["society"])             else "%s %s" % (e["society"], e["event"])
        ev = vevent(
            uid("dl", e["society"], e["event"], e["kind"]),
            "[%s] %s" % (label, head), d, True,
            span_end=parse_date(e.get("end")),
            desc="%s\n%s" % (e.get("url", ""), e.get("note", "")),
            alarms=[("-P7D", "7일 뒤 " + label), ("-P1D", "내일 " + label)],
        )
        shared += ev
        for k in per_member:
            per_member[k] += ev

    for e in state["sessions"].values():
        dt, allday = parse_dt(e.get("start"))
        if dt is None:
            continue
        end, _ = parse_dt(e.get("end"))
        label = SESSION_LABEL.get(e["kind"], e["kind"])
        ev = vevent(
            uid("ss", e["society"], e["event"], e["member"], e["title"][:60]),
            "[%s] %s — %s" % (label, e["member"], e["title"][:60]), dt, allday, end=end,
            location=e.get("room", ""),
            desc="%s %s\n세션: %s\n%s" % (e["society"], e["event"],
                                        e.get("session", ""), e.get("note", "")),
            alarms=[("-P1D", "내일 발표"), ("-PT1H", "1시간 뒤 발표")],
        )
        shared += ev
        if e["member"] in per_member:
            per_member[e["member"]] += ev

    for e in load_personal():
        dt, allday = parse_dt(e.get("due"))
        if dt is None:
            continue
        label = PERSONAL_LABEL.get(e["kind"], e["kind"])
        timed = e["kind"] in ("seminar", "meeting")
        ev = vevent(
            uid("mine", e["title"], e["kind"], e["due"]),
            "[%s] %s" % (label, e["title"]), dt, allday,
            desc="\n".join(x for x in [order_line(e), e.get("note", "")] if x),
            alarms=([("-PT1H", "1시간 뒤: " + e["title"])] if timed and not allday
                    else [("-P3D", "3일 뒤: " + e["title"]), ("-P1D", "내일: " + e["title"])]),
        )
        shared += ev
        who = e.get("member") or []
        targets = who if who else list(per_member)
        for k in targets:
            if k in per_member:
                per_member[k] += ev

    for l in load_leave()["leaves"]:
        d = parse_date(l["date"])
        if d is None:
            continue
        label = "반차" if l["half"] else "연차"
        ev = vevent(
            uid("lv", l["name"], l["date"], label),
            "[%s] %s" % (label, l["name"]), d, True,
            desc=l.get("note", ""),
            alarms=[("-P1D", "내일 %s — %s" % (label, l["name"]))],
        )
        shared += ev
        if l["name"] in per_member:
            per_member[l["name"]] += ev

    (CALENDARS / "all.ics").write_text(build_ics(lab + " 연구·학회 일정", shared), encoding="utf-8")
    made = ["calendars/all.ics"]
    for name, evs in per_member.items():
        if not evs:
            continue
        safe = re.sub(r"[^\w가-힣.-]", "_", name)
        (CALENDARS / (safe + ".ics")).write_text(
            build_ics("학회 일정 — " + name, evs), encoding="utf-8")
        made.append("calendars/%s.ics" % safe)

    print("캘린더 생성:")
    for m in made:
        print("  " + m)
    print("\n각자 폰/구글 캘린더로 이 파일을 가져오면 마감 7일 전·1일 전,")
    print("발표 1일 전·1시간 전에 자동으로 알림이 옵니다.")


# ---------------------------------------------------------------- web / show

def cmd_web():
    state = load_state()
    lab, members = load_members()
    data = {
        "lab": lab,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "today": date.today().isoformat(),
        "members": [m["name"] for m in members],
        "deadlines": sorted(state["deadlines"].values(),
                            key=lambda e: str(e.get("due") or "9999")),
        "sessions": sorted(state["sessions"].values(),
                           key=lambda e: str(e.get("start") or "9999")),
        "personal": sorted(load_personal(), key=lambda e: str(e.get("due") or "9999")),
        "leave": load_leave(),
    }
    out = ROOT / "data.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("대시보드 데이터 생성: %s" % out)
    print("  마감 %d건 / 발표 %d건 / 연구 일정 %d건 / 구성원 %d명"
          % (len(data["deadlines"]), len(data["sessions"]),
             len(data["personal"]), len(data["members"])))


def cmd_month(arg=None):
    """이번 달(또는 YYYY-MM으로 지정한 달) 주요 일정 브리핑."""
    today = date.today()
    if arg:
        try:
            y, m = int(str(arg)[:4]), int(str(arg)[5:7])
        except (ValueError, IndexError):
            raise SystemExit("달 형식은 YYYY-MM 입니다. 예: python watch.py month 2026-10")
    else:
        y, m = today.year, today.month

    tag = "%04d-%02d" % (y, m)
    rows = [r for r in timeline() if r["date"][:7] == tag]

    changed = [(t, d, r) for (t, d), r in load_overrides().items() if d[:7] == tag]

    head = "%d월 주요 일정" % m
    if (y, m) != (today.year, today.month):
        head = "%d년 %d월 주요 일정" % (y, m)
    print(head)

    if not rows:
        print("\n이 달에 등록된 일정이 없습니다.")
        return

    # 매주 반복은 한 달에 4~5번씩 나와 정작 중요한 마감을 묻어버린다.
    # 요일별로 한 줄씩 접어서 맨 위에 보여주고, 날짜별 목록에서는 뺀다.
    WD = "월화수목금토일"
    weekly, once = {}, []
    for r in rows:
        if r.get("repeat") == "weekly":
            wd = parse_date(r["date"]).weekday()
            key = (wd, r["time"] or "", r["title"])
            weekly.setdefault(key, 0)
            weekly[key] += 1
        else:
            once.append(r)

    if weekly:
        print("\n매주 정기")
        for (wd, tm, title) in sorted(weekly):
            print("  %s  %s%s" % (WD[wd], (tm + "  ") if tm else "", title))

        # 순서가 매주 바뀌는 일정은 접어두면 정작 필요한 정보가 사라진다
        rotating = [e for e in load_personal()
                    if e.get("order") and e["due"][:7] == tag]
        for e in sorted(rotating, key=lambda x: x["due"]):
            d = parse_date(e["due"])
            mark = "  ← 다음" if d > today and all(
                parse_date(x["due"]) >= d for x in rotating
                if parse_date(x["due"]) > today) else ""
            print("     %d/%d %s: %s%s"
                  % (d.month, d.day, e["title"], " → ".join(e["order"]), mark))

    if changed:
        print("\n이번 달 변경")
        for t, d, r in sorted(changed, key=lambda x: x[1]):
            dd = parse_date(d)
            when = "%d/%d(%s)" % (dd.month, dd.day, WD[dd.weekday()])
            if r["action"] == "skip":
                print("  %s %s 휴강%s" % (when, t, (" — " + r["note"]) if r["note"] else ""))
            else:
                to = parse_date(r["to"])
                print("  %s %s → %d/%d(%s)%s%s"
                      % (when, t, to.month, to.day, WD[to.weekday()],
                         (" " + r["to"][11:]) if len(r["to"]) > 10 else "",
                         (" — " + r["note"]) if r["note"] else ""))

    rows = once
    if not rows:
        print("\n(정기 일정 외에는 없습니다)")
        return

    last = None
    for r in rows:
        if r["date"] != last:
            d = parse_date(r["date"])
            mark = ""
            if d == today:
                mark = "  ← 오늘"
            elif d < today:
                mark = "  (지남)"
            elif (d - today).days <= 7:
                mark = "  (D-%d)" % (d - today).days
            print("\n%d/%d%s" % (d.month, d.day, mark))
            last = r["date"]

        line = r["title"]
        if r["src"] == "due":
            end = parse_date(r.get("end"))
            if r.get("kind") == "event" and end and end != parse_date(r["date"]):
                line += " 개최 (~%d/%d)" % (end.month, end.day)
            else:
                line += " " + r["label"]
        elif r["src"] == "talk":
            line = "%s %s — %s" % (r["member"], r["label"], r["title"])
        if r["time"]:
            line = r["time"] + "  " + line
        if r["where"]:
            line += " (%s)" % r["where"]
        print(line)
        if r["note"]:
            print("    · " + r["note"])

    print("\n(총 %d건)" % len(rows))


DATA_OPEN, DATA_CLOSE = "/*DATA*/", "/*/DATA*/"


def cmd_sync():
    """data.json 내용을 dashboard.html 의 DATA 블록에 밀어 넣는다."""
    cmd_web()
    html_path = ROOT / "dashboard.html"
    if not html_path.exists():
        raise SystemExit("dashboard.html 이 없습니다.")
    html = html_path.read_text(encoding="utf-8")
    i, j = html.find(DATA_OPEN), html.find(DATA_CLOSE)
    if i < 0 or j < 0 or j < i:
        raise SystemExit("dashboard.html 에서 /*DATA*/ ... /*/DATA*/ 표시를 찾지 못했습니다.")
    payload = (ROOT / "data.json").read_text(encoding="utf-8").strip()
    new = html[:i + len(DATA_OPEN)] + payload + html[j:]
    html_path.write_text(new, encoding="utf-8")
    print("dashboard.html 갱신 완료 (%d바이트)" % len(payload))
    print("Artifact를 같은 파일 경로로 다시 퍼블리시하면 URL이 유지된 채 반영됩니다.")


SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
  name TEXT PRIMARY KEY, sid TEXT, role TEXT, email TEXT, aliases TEXT
);
CREATE TABLE IF NOT EXISTS schedule (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,          -- 이 행을 쓴 에이전트
  category TEXT NOT NULL,       -- deadline | talk | routine
  kind TEXT NOT NULL,           -- abstract, oral, seminar ...
  kind_label TEXT NOT NULL,
  title TEXT NOT NULL,
  member TEXT,                  -- 쉼표 구분. 빈 값이면 랩 전체
  society TEXT, event TEXT,
  starts_at TEXT, ends_at TEXT, all_day INTEGER,
  location TEXT, note TEXT, url TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedule_start ON schedule(starts_at);
CREATE INDEX IF NOT EXISTS idx_schedule_member ON schedule(member);
"""

AGENT_ID = "4-schedule"


def cmd_publish():
    """다른 에이전트가 읽을 수 있도록 공유 DB에 일정을 내보낸다.

    이 에이전트가 쓴 행만 지우고 다시 넣는다 — 다른 에이전트의 행은 건드리지 않는다.
    """
    state = load_state()
    lab, members = load_members()
    now = datetime.now().isoformat(timespec="seconds")
    rows = []

    def add(category, kind, label, title, member, starts, ends, allday,
            loc="", note="", url="", society="", event=""):
        rows.append((
            uid(category, society, event, member, title[:60], str(starts)),
            AGENT_ID, category, kind, label, title, member, society, event,
            starts, ends, 1 if allday else 0, loc, note, url, now))

    for e in state["deadlines"].values():
        d = parse_date(e.get("due"))
        if d is None:
            continue
        end = parse_date(e.get("end")) or d
        add("deadline", e["kind"], DEADLINE_LABEL.get(e["kind"], e["kind"]),
            "%s %s" % (e["society"], e["event"]), "", d.isoformat(), end.isoformat(),
            True, "", e.get("note", ""), e.get("url", ""), e["society"], e["event"])

    for e in state["sessions"].values():
        dt, allday = parse_dt(e.get("start"))
        if dt is None:
            continue
        end, _ = parse_dt(e.get("end"))
        add("talk", e["kind"], SESSION_LABEL.get(e["kind"], e["kind"]), e["title"],
            e.get("member", ""), iso_str(dt), iso_str(end) if end else "", allday,
            e.get("room", ""), e.get("note", ""), "", e["society"], e["event"])

    for e in load_personal():
        dt, allday = parse_dt(e.get("due"))
        if dt is None:
            continue
        add("routine", e["kind"], PERSONAL_LABEL.get(e["kind"], e["kind"]), e["title"],
            ", ".join(e.get("member") or []), iso_str(dt), "", allday,
            "", e.get("note", ""))

    # 연차도 내보낸다 — 회계·근태 에이전트가 읽는다.
    for l in load_leave()["leaves"]:
        label = "반차" if l["half"] else "연차"
        add("leave", "half" if l["half"] else "full", label,
            "%s %s" % (l["name"], label), l["name"], l["date"], l["date"], True,
            "", l.get("note", ""))

    SHARED.mkdir(exist_ok=True)
    con = sqlite3.connect(SHARED_DB)
    try:
        con.executescript(SCHEMA)
        con.execute("DELETE FROM schedule WHERE agent = ?", (AGENT_ID,))
        con.executemany(
            "INSERT OR REPLACE INTO schedule VALUES (%s)" % ",".join("?" * 16), rows)
        con.execute("DELETE FROM members")
        con.executemany("INSERT INTO members VALUES (?,?,?,?,?)", [
            (m["name"], str(m.get("sid", "")), m.get("role", ""), m.get("email", ""),
             ", ".join(m.get("aliases") or [])) for m in members])
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM schedule").fetchone()[0]
    finally:
        con.close()

    print("공유 DB 갱신: %s" % SHARED_DB)
    print("  이 에이전트가 쓴 일정 %d건 (DB 전체 %d건) / 구성원 %d명"
          % (len(rows), n, len(members)))


def cmd_leave():
    d = load_leave()
    if not d["people"]:
        print("leave.yaml 에 등록된 사람이 없습니다.")
        return
    print("%d년 연차 현황  (오늘: %s)\n" % (d["year"], date.today().isoformat()))
    print("  이름     총   사용   남음   진행")
    print("  " + "-" * 46)
    for p in d["people"]:
        ratio = (p["used"] / p["total"]) if p["total"] else 0
        bar = "█" * int(round(ratio * 12)) + "·" * (12 - int(round(ratio * 12)))
        print("  %-5s %5.1f %5.1f  %5.1f   %s %3d%%"
              % (p["name"], p["total"], p["used"], p["left"], bar, round(ratio * 100)))

    upcoming = [l for l in d["leaves"] if days_left(l["date"]) is not None
                and days_left(l["date"]) >= 0]
    print("\n예정된 연차 %d건" % len(upcoming))
    for l in upcoming:
        dl = days_left(l["date"])
        print("  %s  %-5s %s%s"
              % (l["date"], l["name"], "반차" if l["half"] else "연차",
                 ("  (D-%d)" % dl) if dl > 0 else "  ← 오늘"))
    if not upcoming:
        print("  (없음 — leave.yaml 의 leaves 에 추가하면 캘린더에 뜹니다)")

    unknown = [(p["name"], sum(p["used_before"].values())) for p in d["people"]
               if p["used_before"]]
    if unknown:
        print("\n날짜를 모르는 기존 사용분 (캘린더에 안 뜸)")
        for n, v in unknown:
            print("  %-5s %.1f일" % (n, v))


SITE = SHARED.parent / "docs"          # GitHub Pages 가 그대로 서비스하는 폴더


def _public_payload():
    """공개 사이트에 실을 데이터. 연차는 뺀다.

    GitHub Pages 는 저장소를 private 으로 두어도 사이트 자체는 공개다.
    누구 연차가 며칠 남았는지는 링크를 아는 아무나 볼 정보가 아니다.
    화면에서 감추는 것으로는 부족하다 — HTML 소스에 남으므로 아예 뺀다.
    (발표 제목은 이미 학회 프로그램북에 공개된 것이라 그대로 둔다.)
    """
    data = json.loads((ROOT / "data.json").read_text(encoding="utf-8"))
    dropped = len((data.get("leave") or {}).get("people") or [])
    data.pop("leave", None)
    return json.dumps(data, ensure_ascii=False, indent=1), dropped


def _write_icon(path, size):
    """홈 화면 아이콘. 폰에서 '홈 화면에 추가' 하면 이게 앱 아이콘이 된다.

    한글 폰트에 기대지 않으려고 글자 대신 달력 모양을 직접 그린다.
    폰트는 OS마다 있고 없고가 달라서 빌드가 조용히 깨진다.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=(29, 78, 216, 255))
    # 달력 몸통
    x0, y0, x1, y1 = int(s * .22), int(s * .28), int(s * .78), int(s * .76)
    d.rounded_rectangle([x0, y0, x1, y1], radius=int(s * .06), fill=(255, 255, 255, 255))
    # 상단 머리띠
    d.rounded_rectangle([x0, y0, x1, y0 + int(s * .12)], radius=int(s * .06),
                        fill=(147, 197, 253, 255))
    d.rectangle([x0, y0 + int(s * .07), x1, y0 + int(s * .12)], fill=(147, 197, 253, 255))
    # 고리 두 개
    for cx in (int(s * .36), int(s * .64)):
        d.rounded_rectangle([cx - int(s * .025), int(s * .20),
                             cx + int(s * .025), int(s * .33)],
                            radius=int(s * .025), fill=(255, 255, 255, 255))
    # 오늘 표시 점
    cx, cy, rr = int(s * .5), int(s * .585), int(s * .075)
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(29, 78, 216, 255))
    img.save(str(path), "PNG")
    return True


# 공개 사이트에만 붙는 부분 — 폰에서 한 번에 들어오고, 한 번에 구독하게 한다.
SITE_EXTRA = u"""
<style>
.addon{max-width:1180px;margin:26px auto 0;padding:0 18px;font:14px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
.addon section{border:1px solid #e2e5ea;border-radius:14px;padding:18px 20px;margin-bottom:14px;background:#fff}
.addon h2{margin:0 0 4px;font-size:15px;letter-spacing:-.01em}
.addon p{margin:0 0 12px;color:#5b6270;font-size:13px}
.addon .hint{margin:12px 0 0;font-size:12px;color:#7b828e}
.subgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px}
.subrow{display:flex;align-items:center;gap:6px;border:1px solid #e6e9ee;border-radius:10px;padding:7px 9px}
.subrow b{flex:1;font-weight:600;font-size:13px}
.subrow a{text-decoration:none;font-size:12px;padding:4px 9px;border-radius:7px;white-space:nowrap}
.subrow .go{background:#1d4ed8;color:#fff}
.subrow .gg{background:#eef1f6;color:#3c4655}
.steps{margin:0;padding-left:18px;color:#4b525e;font-size:13px}
.steps li{margin:3px 0}
@media (prefers-color-scheme:dark){
 .addon section{background:#171a1f;border-color:#2a2f38}
 .addon p,.steps{color:#9aa2ae} .addon .hint{color:#7b828e}
 .subrow{border-color:#2a2f38} .subrow .gg{background:#242932;color:#c9d0da}
}
</style>
<div class="addon">
  <section>
    <h2>내 일정 폰에 넣기</h2>
    <p>이름을 누르면 폰 캘린더에 <b>구독</b>으로 들어갑니다. 한 번만 해두면 일정이 바뀔 때마다 자동으로 따라옵니다 — 다시 받을 필요가 없습니다.</p>
    <div class="subgrid" id="subGrid"></div>
    <p class="hint">아이폰은 파란 버튼(구독)을 누르면 "구독하시겠습니까?" 가 뜹니다.
      안드로이드는 구글 캘린더 버튼을 쓰세요. 마감 7일·1일 전, 발표 1일 전·1시간 전에 알림이 옵니다.</p>
  </section>
  <section>
    <h2>홈 화면에 추가</h2>
    <p>앱처럼 한 번에 열립니다. 설치가 아니라 바로가기라 용량을 쓰지 않습니다.</p>
    <ol class="steps">
      <li><b>아이폰(사파리)</b> — 아래 공유 버튼 <span aria-hidden="true">⬆︎</span> → "홈 화면에 추가"</li>
      <li><b>안드로이드(크롬)</b> — 오른쪽 위 ⋮ → "홈 화면에 추가"</li>
      <li><b>PC</b> — 주소창 오른쪽 설치 아이콘, 또는 그냥 즐겨찾기</li>
    </ol>
  </section>
</div>
<script>
(function(){
  /* 주소를 코드에 박지 않는다. 저장소 이름이 바뀌거나 다른 곳에 올려도 그대로 동작해야 한다. */
  var base = location.href.split(/[?#]/)[0].replace(/[^\\/]*$/, "");
  var web  = base.replace(/^https?:/, "webcal:");
  var rows = [{name:"전체 일정", file:"all.ics", all:true}];
  ((typeof DATA !== "undefined" && DATA.members) || []).forEach(function(n){
    rows.push({name:n, file:n + ".ics"});
  });
  var g = document.getElementById("subGrid");
  if (!g) return;
  g.innerHTML = rows.map(function(r){
    var enc = encodeURIComponent(r.file);
    var wurl = web + "calendars/" + enc;
    var gurl = "https://calendar.google.com/calendar/r?cid=" + encodeURIComponent(wurl);
    return '<div class="subrow"><b>' + r.name + '</b>'
         + '<a class="go" href="' + wurl + '">구독</a>'
         + '<a class="gg" href="' + gurl + '" target="_blank" rel="noopener">구글</a></div>';
  }).join("");
})();
</script>
"""


def cmd_qr(url=None):
    """주소를 QR 로 만든다 (docs/qr.svg, qr.png).

    11명한테 주소를 타이핑하게 하는 것보다 단톡방에 그림 한 장 던지는 게 빠르다.
    연구실 문에 붙여도 된다.
    """
    saved = load_state().get("site_url")
    url = url or saved
    if not url:
        print("주소가 없습니다: python watch.py qr https://아이디.github.io/저장소/")
        return
    if url != saved:
        st = load_state()
        st["site_url"] = url
        save_state(st)
    try:
        import segno
    except ImportError:
        print("  ⚠ QR 을 만들려면: pip install segno")
        return
    SITE.mkdir(exist_ok=True)
    q = segno.make(url, error="m")
    q.save(str(SITE / "qr.svg"), scale=8, border=3, dark="#111827")
    q.save(str(SITE / "qr.png"), scale=10, border=3, dark="#111827")
    print("  QR 생성: docs/qr.svg, docs/qr.png  →  %s" % url)


def cmd_site(base_url=None):
    """공개용 정적 사이트를 만든다 (docs/).

    대시보드와 같은 파일을 쓴다. 그 페이지는 Claude 런타임이 없으면 채팅·삭제를
    스스로 숨기므로, 정적으로 올려도 보기 전용으로 알아서 동작한다.
    HTML 한 벌만 관리하면 되도록 일부러 이렇게 했다.

    다만 데이터는 같지 않다 — 공개본에서는 연차를 뺀다. _public_payload 참고.
    """
    cmd_sync()
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")

    title = "연구실 일정"
    m = re.search(r"<title>(.*?)</title>", html)
    if m:
        title = m.group(1)
        html = html.replace(m.group(0), "", 1)

    payload, dropped = _public_payload()
    i, j = html.find(DATA_OPEN), html.find(DATA_CLOSE)
    if i < 0 or j < 0 or j < i:
        raise SystemExit("dashboard.html 에서 /*DATA*/ 표시를 찾지 못했습니다.")
    html = html[:i + len(DATA_OPEN)] + payload + html[j:]

    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(
        "<!doctype html>\n<html lang=\"ko\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, "
        "viewport-fit=cover\">\n"
        "<meta name=\"robots\" content=\"noindex\">\n"
        "<meta name=\"theme-color\" content=\"#1d4ed8\">\n"
        "<meta name=\"apple-mobile-web-app-capable\" content=\"yes\">\n"
        "<meta name=\"apple-mobile-web-app-title\" content=\"연구실 일정\">\n"
        "<meta name=\"apple-mobile-web-app-status-bar-style\" content=\"default\">\n"
        "<link rel=\"manifest\" href=\"manifest.webmanifest\">\n"
        "<link rel=\"apple-touch-icon\" href=\"icon-180.png\">\n"
        "<link rel=\"icon\" href=\"icon-192.png\">\n"
        "<title>%s</title>\n"
        "<style>:root{color-scheme:light dark}body{margin:0;font:14px system-ui}"
        "img{max-width:100%%}[hidden]{display:none!important}</style>\n"
        "</head>\n<body>\n%s\n%s\n</body>\n</html>\n" % (title, html, SITE_EXTRA),
        encoding="utf-8")

    (SITE / "manifest.webmanifest").write_text(json.dumps({
        "name": "인천대 연구실 일정",
        "short_name": "연구실 일정",
        "start_url": ".",
        "scope": ".",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#1d4ed8",
        "icons": [
            {"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any maskable"},
        ],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    icons = all(_write_icon(SITE / ("icon-%d.png" % s), s) for s in (180, 192, 512))

    # 캘린더도 함께 올린다 — 구글 캘린더에서 URL 구독이 가능해진다
    cal = SITE / "calendars"
    cal.mkdir(exist_ok=True)
    for f in cal.glob("*.ics"):
        f.unlink()
    n = 0
    for f in sorted(CALENDARS.glob("*.ics")):
        (cal / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        n += 1

    (SITE / ".nojekyll").write_text("", encoding="utf-8")   # _ 로 시작하는 파일 보호

    print("정적 사이트 생성: %s" % SITE)
    print("  index.html (보기 전용) + 캘린더 %d개" % n)
    if dropped:
        print("  연차 %d명분은 공개본에서 뺐습니다 (사이트는 링크만 알면 누구나 봅니다)" % dropped)
    if not icons:
        print("  ⚠ Pillow 가 없어 홈 화면 아이콘을 만들지 못했습니다 — pip install pillow")
    if base_url or load_state().get("site_url"):
        cmd_qr(base_url)
    else:
        print("  주소가 정해지면 한 번만: python watch.py site https://아이디.github.io/저장소/")
    print("  GitHub 저장소 Settings > Pages 에서 main 브랜치의 /docs 를 지정하세요.")


def cmd_show():
    s = load_state()
    print(json.dumps({"deadlines": s["deadlines"], "sessions": s["sessions"]},
                     ensure_ascii=False, indent=2))


def cmd_rotation(arg=None):
    """다음 개인미팅 순서. 인자를 주면 그 날짜가 속한 회차를 본다."""
    want = parse_date(arg) if arg else None
    rows = [e for e in load_personal() if e.get("order")]
    if not rows:
        print("순서를 정할 일정이 없습니다.")
        print("personal.yaml 의 해당 항목에 `rotate: true` 와 참가자 목록을 넣으세요.")
        return
    rows.sort(key=lambda e: e["due"])
    if want:
        pick = [e for e in rows if parse_date(e["due"]) >= want]
    else:
        today = date.today()
        pick = [e for e in rows if parse_date(e["due"]) > today]
    if not pick:
        print("남은 회차가 없습니다. personal.yaml 의 until 을 확인하세요.")
        return

    WDN = "월화수목금토일"
    for e in pick[:4]:
        d = parse_date(e["due"])
        dl = (d - date.today()).days
        head = "%s(%s)" % (d.strftime("%m/%d"), WDN[d.weekday()])
        tail = "  ← 다음 회차" if e is pick[0] else ""
        print("\n%s %s  D-%d%s" % (head, e["title"], dl, tail))
        for i, name in enumerate(e["order"], 1):
            print("   %d. %s" % (i, name))
    print("\n같은 주는 몇 번을 돌려도 같은 순서가 나옵니다 (날짜에서 뽑음).")
    print("참가자가 바뀌면 순서도 바뀝니다.")


def cmd_prune_members():
    """members.yaml 에서 빠진 사람의 발표 일정을 정리한다.

    명단에서 사람을 지워도 발표 기록은 남아 있어 리포트·캘린더에 계속 뜬다.
    지우기 전에 무엇이 사라지는지 먼저 보여준다 — 확인된 발표를 조용히 잃으면 안 된다.
    """
    state = load_state()
    known = set(m["name"] for m in load_members()[1])
    orphans = {k: e for k, e in state["sessions"].items() if e.get("member") not in known}
    if not orphans:
        print("명단에 없는 발표가 없습니다.")
        return
    print("명단에 없는 사람의 발표 %d건을 제거합니다:" % len(orphans))
    for e in orphans.values():
        print("  - %s · %s · %s" % (e.get("member"), e["society"], e["title"][:60]))
        print("      %s %s | %s" % (e.get("start"), e.get("room", ""), e.get("note", "")[:70]))
    for k in orphans:
        state["archive"].append(state["sessions"].pop(k))
    save_state(state)
    print("\n아카이브(state.json의 archive)로 옮겼습니다 — 되살릴 수 있습니다.")


def cmd_reset():
    """수집된 일정을 모두 비운다. targets.yaml / members.yaml 은 건드리지 않는다."""
    s = load_state()
    n = len(s["deadlines"]) + len(s["sessions"])
    save_state({"deadlines": {}, "sessions": {}, "archive": s["archive"]})
    for d in (REPORTS, CALENDARS, SCANS):
        if d.exists():
            for f in d.iterdir():
                f.unlink()
    for f in (ROOT / "LATEST.md", ROOT / "data.json"):
        if f.exists():
            f.unlink()
    print("일정 %d건과 생성물(리포트/캘린더/스캔)을 지웠습니다." % n)
    print("targets.yaml, members.yaml 은 그대로입니다.")


CMDS = {"list": cmd_list, "report": cmd_report, "ics": cmd_ics,
        "web": cmd_web, "sync": cmd_sync, "publish": cmd_publish, "leave": cmd_leave,
        "show": cmd_show, "reset": cmd_reset,
        "prune-members": cmd_prune_members}
# 인자를 줘도 되고 안 줘도 되는 것들. CMDS 에 넣어두면 인자가 조용히 무시된다 —
# `rotation 2026-09-08` 이 날짜를 씹고 엉뚱한 회차를 보여준 적이 있다.
OPT_CMDS = {"month": cmd_month, "site": cmd_site, "qr": cmd_qr,
            "rotation": cmd_rotation}
ARG_CMDS = {"scan-pdf": cmd_scan_pdf,
            "upsert-deadlines": cmd_upsert_deadlines,
            "upsert-sessions": cmd_upsert_sessions}

if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else "report"
    if cmd in OPT_CMDS:
        OPT_CMDS[cmd](a[1] if len(a) > 1 else None)
    elif cmd in CMDS:
        CMDS[cmd]()
    elif cmd in ARG_CMDS:
        if len(a) < 2:
            raise SystemExit("사용법: python watch.py %s <파일>" % cmd)
        ARG_CMDS[cmd](a[1])
    else:
        raise SystemExit(__doc__)
