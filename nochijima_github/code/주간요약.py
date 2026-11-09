# -*- coding: utf-8 -*-
"""
주간요약.py — 지난 7일 동안 그 나라에서 잡힌 소식을 한 번에 요약해 카톡으로 보냅니다.

■ 무엇을 모으나 : 감시 프로그램들이 logs 폴더에 남긴 지난 7일 기록
    · 재난      : 지진·재난경보·폭우·기상특보·대형사고 (알림과 같은 '내 지역' 기준으로 거름)
    · 환율      : 일주일 전 → 지금 (하나은행 매매기준율)
    · 제도·정책 : 비자·체류·생활 제도 변화 (AI가 이미 걸러 둔 것)
■ 대상          : 주간요약_나라.json 의 "targets" — 나라 + 자녀가 사는 도시 (지금은 테스트로 일본 / 도쿄도)
                  재난 줄은 그 도시에 해당하는 것만(알림과 같은 지역필터 기준). 나라 안 다른 지역의 큰 일은 '참고' 한 줄.
■ 다가오는 일   : 앞으로 7일 안의 현지 공휴일(holidays 라이브러리) + 지난 30일 안에 시행된 제도 1건(AI가 고름). 없으면 줄 생략.
■ 발송          : 카카오톡 '나에게 보내기' (200자 넘으면 나눠서 보냄)
■ 거르기(코드)  : 기사 날짜가 최근 7일이 아니거나, 제목이 옛날 일(예: "2018년 10월 1일부 …")이면 뺌
■ AI(Gemini)    : 정책·사고·경보 문장을 '자연스러운 한국어 한 줄'로 다시 씀. 같은 내용은 한 줄로 합침.
                  원문에 없는 내용은 못 넣게 하고(원문에 없는 숫자가 나오면 그 줄은 버림), 뜻이 불분명하면 뺌.
                  AI판단.py 와 같은 gemini_config.json 설정을 씀. 지진·폭우·환율은 숫자라 AI 없이 그대로.
                  AI 호출이 실패하면 깨진 원문을 보내지 않고 "소식 n건 (요약 실패 — 확인 필요)" 로만 보냄.
■ 실행          : 매주 일요일 20:00 (작업 스케줄러 nochijima_weekly)
                  py 주간요약.py        ← 지금 보내기
                  py 주간요약.py 미리   ← 보내지 않고 화면에만 보기
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from 카톡발송 import COUNTRY_TO_CURRENCY, extract_blocks, bracket_name, block_hash, send_memo
from 지역필터 import block_passes, strip_geo, parse_geo, quake_place_ko, COUNTRY_CODE
from kakao_token import get_valid_token
import 환율감시 as fx
import AI판단 as ai

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")
CONFIG = os.path.join(BASE, "주간요약_나라.json")
DAYS = 7
MAX_LINES = 3                      # 항목마다 최대 줄 수
LINE_MAX = 46                      # AI가 쓴 한 줄 최대 글자 수
FOOTER = "※ 알림이 오지 않았다고 해서 안전하다는 뜻은 아닙니다."
DISASTER_PREFIXES = ["지진감시", "재난감시", "호우감시", "일본기상청감시", "미국기상청감시", "대형사고감시", "말레이시아기상청감시"]
BOILER = ("━", "출처", "자세히", "관련 있다면", "현지 안전", "현지 가족", "@")


def load_targets():
    """주간요약_나라.json → [(나라, 도시)]. 도시는 자녀가 사는 도시(또는 주·도·현). 없으면 나라 전체."""
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return []
    out = [(t.get("country", ""), t.get("city", "")) for t in d.get("targets") or [] if t.get("country")]
    out += [(c, "") for c in d.get("countries") or []]          # 옛 형식(나라만)도 읽음
    return out


def city_config(country, city):
    """지역필터에 넘길 설정(이 자녀의 도시만 켠 것). 도시가 없으면 None = 나라 전체."""
    return {"enabled": True, "regions": {country: [city]}, "radius_km": {}} if city else None


def in_city(block, prefix, cfg):
    """이 알림이 자녀 도시에 해당하면 True(알림과 같은 지역필터 기준·거리).
       위치 정보(좌표·현 코드)가 없는 알림은 도시 소식으로 치지 않음 → '다른 지역 참고' 쪽으로."""
    if cfg is None:
        return True
    lat, _ = parse_geo(block)
    if lat is None and "@iso:" not in block:
        return False
    return block_passes(block, prefix, cfg)


def week_blocks(country, prefixes, days=DAYS):
    """지난 days일 로그에서 그 나라 알림 블록(중복 제거). [(날짜, 블록, 로그종류)]"""
    today = datetime.now()
    seen, out = set(), []
    for p in prefixes:
        for i in range(days - 1, -1, -1):
            day = today - timedelta(days=i)
            path = os.path.join(LOG_DIR, f"{p}_{day:%Y-%m-%d}.log")
            if not os.path.exists(path):
                continue
            for b in extract_blocks(path):
                if country not in bracket_name(b):
                    continue
                h = block_hash(b)
                if h in seen:
                    continue
                seen.add(h)
                out.append((day, b, p))
    return out


def _title(block, limit=38):
    first = block.splitlines()[0]
    first = re.sub(r"^[🚨🌏💱📋🚗⚡🌧️🟢🟠🔴━ ]+", "", first).strip()
    first = re.sub(r"^\[[^\]]*\]\s*", "", first)
    first = re.sub(r"\s*[🟢🟠🔴]\s*(소규모|중대|대형)\s*$", "", first)
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def _cut(text, limit):
    text = (text or "").strip().rstrip(",·- ")
    return text if len(text) <= limit else text[:limit - 1].rstrip(",·- ") + "…"


# ── 옛날 소식 거르기 ───────────────────────────────────────────────
def article_date(block, log_day):
    """알림에 적힌 기사 날짜('출처: … · 현지 10/1 00:15'). 없으면 로그 날짜."""
    m = re.search(r"(?:현지|한국)\s*(\d{1,2})/(\d{1,2})", block)
    if not m:
        return log_day
    try:
        d = log_day.replace(month=int(m.group(1)), day=int(m.group(2)))
    except ValueError:
        return log_day
    if d > log_day + timedelta(days=2):               # 연말·연초: 12/31 기사를 1/2 에 잡은 경우
        d = d.replace(year=d.year - 1)
    return d


def is_old(block, log_day, days=DAYS):
    """기사 날짜가 최근 days일 밖이거나, 내용이 지난 해의 일(예: '2018년 10월 1일부 …')이면 True."""
    start = (datetime.now() - timedelta(days=days - 1)).date()
    if article_date(block, log_day).date() < start:
        return True
    this_year = datetime.now().year
    years = [int(y) for y in re.findall(r"((?:19|20)\d\d)\s*년", strip_geo(block))]
    return bool(years) and max(years) < this_year


def plain_text(block):
    """AI에 넘길 원문: 제목 + 본문(출처·링크·안내 문구 제외)."""
    rows = [_title(block, 200)]
    for ln in strip_geo(block).splitlines()[1:]:
        ln = ln.strip()
        if ln and not ln.startswith(BOILER) and ln not in rows:
            rows.append(ln)
    return " / ".join(r for r in rows if r)


# ── AI(Gemini): 자연스러운 한 줄로 다시 쓰기 + 같은 내용 합치기 ──
REWRITE_PROMPT = """너는 해외 소식 '주간 요약' 편집자다. 받는 사람은 {country}에 자녀가 사는 한국인 부모다.
아래는 지난 7일 동안 {country}에서 감지된 알림 원문이다. 기계 번역이라 문장이 어색할 수 있다.

할 일: 각 소식을 자연스러운 한국어 한 줄로 다시 써라.

반드시 지킬 것
1. 원문에 없는 사실·숫자·날짜·장소·원인·평가를 절대 추가하지 마라. 추측도 금지.
2. 뜻이 불분명해서 무슨 일인지 확신할 수 없는 항목은 쓰지 말고 dropped 에 넣어라.
3. 옛날 일(지난 해의 개정 안내, 과거 사건 회고 등)은 쓰지 말고 dropped 에 넣어라. 오늘은 {today} 이다.
4. 내용이 같은 소식(같은 제도 변경·같은 사고를 다룬 기사 여러 개)은 한 줄로 합치고, ids 에 해당 id 를 모두 넣어라.
5. 한 줄은 {limit}자 이내, 명사형으로 끝내라(예: "영주허가 요건 강화", "체류 심사 수수료 인상"). 날짜·출처·이모지는 쓰지 마라.
6. "실시간", "안전합니다" 같은 과장·단정 표현, 조언 문구는 쓰지 마라.
7. 모든 id 는 lines 의 ids 또는 dropped 중 한 곳에 꼭 들어가야 한다.

알림 원문(JSON 한 줄에 1건, kind 는 재난 또는 정책):
{items}"""

REWRITE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lines": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"text": {"type": "STRING"}, "ids": {"type": "ARRAY", "items": {"type": "INTEGER"}}},
            "required": ["text", "ids"]}},
        "dropped": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {"id": {"type": "INTEGER"}, "reason": {"type": "STRING"}},
            "required": ["id", "reason"]}},
    },
    "required": ["lines", "dropped"],
}


def _gemini(prompt, schema=None):
    """Gemini 호출(AI판단.py 와 같은 설정·모델 순서). 실패하면 AIError. 키는 어디에도 출력하지 않음."""
    cfg = ai.load_config()
    key = cfg.get("api_key", "")
    if not cfg.get("enabled", True) or not key:
        raise ai.AIError("AI 꺼짐 또는 키 없음")
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema or REWRITE_SCHEMA},
    }).encode("utf-8")
    last = "알 수 없음"
    for model in cfg.get("models") or ai.DEFAULT_MODELS:
        for _ in range(2):
            try:
                req = Request(ai.API.format(model=model), data=body, method="POST",
                              headers={"Content-Type": "application/json", "x-goog-api-key": key})
                ai._pace()
                ai._count(model, "주간요약", "요청")
                with urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                return json.loads(text), model
            except HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code in (401, 403):
                    raise ai.AIError("키 오류")
                if e.code in (404, 429):
                    break
                time.sleep(3)
            except Exception as e:
                last = type(e).__name__
                time.sleep(2)
    raise ai.AIError(last)


def rewrite(country, items):
    """items: [{id, kind, day, text}] → {"재난": [(날짜, 한 줄)], "정책": [...]} . 실패하면 None."""
    if not items:
        return {"재난": [], "정책": []}
    prompt = REWRITE_PROMPT.format(
        country=country, today=f"{datetime.now():%Y-%m-%d}", limit=LINE_MAX - 6,
        items="\n".join(json.dumps({"id": it["id"], "kind": it["kind"], "text": it["text"]}, ensure_ascii=False) for it in items))
    try:
        out, model = _gemini(prompt)
    except Exception as e:
        print(f"   (AI 요약 실패: {e})")
        return None
    by_id = {it["id"]: it for it in items}
    res = {"재난": [], "정책": []}
    used = set()
    for row in out.get("lines") or []:
        ids = [i for i in (row.get("ids") or []) if i in by_id and i not in used]
        text = re.sub(r"\s+", " ", str(row.get("text") or "")).strip().rstrip(".")
        if not ids or not text:
            continue
        src = " ".join(by_id[i]["text"] for i in ids)
        # 안전장치: 원문에 없는 숫자가 들어갔으면 그 줄은 버림(지어낸 내용 방지)
        if any(n not in src for n in re.findall(r"\d+", text)) or re.search(r"실시간|안전합니다", text):
            print(f"   (버림 — 원문에 없는 내용: {text})")
            continue
        used.update(ids)
        day = min(by_id[i]["day"] for i in ids)
        res[by_id[ids[0]]["kind"]].append((day, _cut(text, LINE_MAX)))
    for d in out.get("dropped") or []:
        if d.get("id") in by_id:
            print(f"   (AI가 뺌: {by_id[d['id']]['text'][:40]} — {d.get('reason', '')})")
    print(f"   (AI 요약: {model} · 원문 {len(items)}건 → {sum(len(v) for v in res.values())}줄)")
    for k in res:
        res[k].sort(key=lambda x: x[0])
    return res


# ── 항목별 모으기 ────────────────────────────────────────────────
BIG_QUAKE = 5.5        # 다른 지역이라도 '참고'로 적는 지진 규모


def collect(country, city=""):
    """일주일 치를 나눔 → (도시 지진, 도시 폭우, AI에 넘길 항목들, 다른 지역 큰 일[(날짜, 문구)])"""
    cfg = city_config(country, city)
    quakes, rains, items, others = [], [], [], []
    for day, b, p in week_blocks(country, DISASTER_PREFIXES):
        first = b.splitlines()[0]
        if "🟢" in first or "소규모" in first:
            continue                                   # 소규모(국제경보 Green)는 요약에서도 제외
        if "비 예보" in first:
            continue                                   # 폭우 '예보'는 실제로 온 비가 아니라 요약에서 뺌
        mine = in_city(b, p, cfg)
        if "지진 발생" in first:
            m = re.search(r"규모\s*(\d+\.\d)", b)
            mag = float(m.group(1)) if m else 0.0
            if mine:
                quakes.append((mag, day, b))
            elif mag >= BIG_QUAKE:
                place = quake_place(country, b)
                others.append((mag, day, f"규모 {mag:.1f} 지진" + (f"({place})" if place else "")))
        elif b.lstrip().startswith("🌧") or "open-meteo" in b:
            m = re.search(r"하루\s*(\d+)\s*mm", b)
            if mine:
                rains.append((int(m.group(1)) if m else 0, day, b))
            elif b.lstrip().startswith("🚨🚨"):
                others.append((5.0, day, _title(b, 26)))
        elif mine and not is_old(b, day):              # 대형 사고·재난 경보·기상 특보 → AI가 한 줄로
            items.append({"kind": "재난", "day": article_date(b, day), "text": plain_text(b)})
        elif not mine and b.lstrip().startswith("🚨🚨") and not is_old(b, day):
            others.append((5.0, day, _title(b, 26) if "대형 사고" not in first else "대형 사고"))
    for day, b, p in week_blocks(country, ["정책뉴스감시"]):
        if is_old(b, day):
            continue
        items.append({"kind": "정책", "day": article_date(b, day), "text": plain_text(b)})
    for i, it in enumerate(items, 1):
        it["id"] = i
    return quakes, rains, items, others


def quake_place(country, block):
    """지진 위치를 한국어로(지역필터.quake_place_ko — 광역은 진앙 좌표로 정함). 모르면 ''."""
    rows = strip_geo(block).splitlines()
    raw = (rows[1] if len(rows) > 1 else "").split("·")[0].strip()
    if raw.startswith("규모"):
        raw = ""                                       # 위치 설명이 없는 알림
    if raw and re.search(r"[가-힣]", raw) and not re.search(r"[A-Za-z]", raw):
        return raw                                     # 이미 한국어(새 지진감시가 만든 알림)
    geo = parse_geo(block)
    if not geo or geo[0] is None or geo[1] is None:
        return ""
    return quake_place_ko(country, geo[0], geo[1], raw)


def number_lines(country, quakes, rains):
    """지진·폭우는 숫자 그대로(AI 없이)."""
    lines = []
    if quakes:
        quakes.sort(key=lambda x: -x[0])
        mag, day, b = quakes[0]
        place = quake_place(country, b)
        lines.append(f"지진 {len(quakes)}건 · 가장 큰 것 {day.month}/{day.day} 규모 {mag:.1f}" + (f" ({_cut(place, 24)})" if place else ""))
    if rains:
        rains.sort(key=lambda x: -x[0])
        mm, day, b = rains[0]
        m = re.search(r"\[([^\]]+)\]", b.splitlines()[0])
        loc = (m.group(1) if m else country).replace(country, "").strip() or country
        lines.append(f"폭우 {len(rains)}건 · 가장 많은 곳 {day.month}/{day.day} {loc} 하루 {mm}mm")
    return lines


def fx_line(country):
    """환율 주간 변동 한 줄. 값이 없으면 None."""
    name = COUNTRY_TO_CURRENCY.get(country)
    code = fx.NAME_CODE.get(name or "")
    if not code:
        return None
    hist = fx.load_history(refresh=False).get(code) or {}
    now = (fx._load(fx.SAVE_FILE, {}).get("rates") or {}).get(code)
    days = sorted(d for d in hist if not d.startswith("_"))
    if now is None and days:
        now = hist[days[-1]]["c"]
    cut = (datetime.now() - timedelta(days=DAYS)).strftime("%Y-%m-%d")
    older = [d for d in days if d <= cut]
    if now is None or not older:
        return None
    ref = hist[older[-1]]["c"]
    label = fx.CODE_LABEL[code]
    return f"{name} {label}당 {fx.fmt_won(ref)} → {fx.fmt_won(now)} ({fx._arrow(fx._pct(now, ref))})"


# ── 다가오는 일: 공휴일(오프라인 자료) + 최근 30일 안에 시행된 제도 1건 ──
HOLIDAY_KO = {   # 일본 공휴일 이름(holidays 라이브러리의 일본어 이름 → 한국어)
    "元日": "설날(신정)", "成人の日": "성인의 날", "建国記念の日": "건국기념일", "天皇誕生日": "일왕 탄생일",
    "春分の日": "춘분의 날", "昭和の日": "쇼와의 날", "憲法記念日": "헌법기념일", "みどりの日": "녹색의 날",
    "こどもの日": "어린이날", "海の日": "바다의 날", "山の日": "산의 날", "敬老の日": "경로의 날",
    "秋分の日": "추분의 날", "スポーツの日": "스포츠의 날", "文化の日": "문화의 날", "勤労感謝の日": "근로감사의 날",
    "振替休日": "대체 휴일", "国民の休日": "국민의 휴일",
}
AHEAD_DAYS = 7
POLICY_DAYS = 30


def holiday_lines(country):
    """오늘부터 7일 안의 현지 공휴일. (python 'holidays' 라이브러리 — 인터넷 없이 계산) 없거나 라이브러리가 없으면 []."""
    cc = COUNTRY_CODE.get(country)
    if not cc:
        return []
    try:
        import holidays
        today = datetime.now().date()
        cal = holidays.country_holidays(cc, years=sorted({today.year, (today + timedelta(days=AHEAD_DAYS)).year}))
    except Exception:
        return []
    out = []
    for i in range(AHEAD_DAYS + 1):
        d = today + timedelta(days=i)
        name = cal.get(d)
        if name:
            ko = HOLIDAY_KO.get(name.split(";")[0].strip(), name)
            out.append(f"{d.month}/{d.day} {ko}(현지 공휴일)")
    return out[:2]


EFFECT_PROMPT = """너는 해외 소식 '주간 요약' 편집자다. 받는 사람은 {country}에 자녀(유학생)가 사는 한국인 부모다.
아래는 지난 30일 동안 {country}에서 감지된 비자·체류·생활 제도 소식 원문이다(기계 번역이라 어색할 수 있음). 오늘은 {today} 이다.

할 일: '이미 시행됐고 지금도 유효한 제도 변경' 가운데, 유학생 가족에게 가장 영향이 큰 것 1건만 골라 한 줄로 써라.
- 형식: "시행일 내용 — 누가 확인하면 좋은지". 예: "10/1 체류 수수료 인상 — 갱신 앞두면 확인"
- 시행일은 원문에 있을 때만 "월/일"로 적고, 없으면 시행일 없이 내용부터 쓴다.
- 원문에 없는 사실·숫자·금액·날짜를 절대 추가하지 마라. 추측 금지.
- 아직 검토·논의 중인 것, 옛날 일, 뜻이 불분명한 것은 고르지 마라. 고를 것이 없으면 id 는 -1, text 는 "" 로 한다.
- {limit}자 이내. "실시간", "안전합니다" 같은 과장·단정 표현 금지.

원문(JSON 한 줄에 1건):
{items}"""
EFFECT_SCHEMA = {"type": "OBJECT", "properties": {"id": {"type": "INTEGER"}, "text": {"type": "STRING"}},
                 "required": ["id", "text"]}


def effect_line(country, skip_texts=()):
    """지난 30일 안에 시행돼 아직 유효한 제도 소식 1줄(AI가 고름). 없거나 실패하면 None."""
    cands = []
    for day, b, p in week_blocks(country, ["정책뉴스감시"], POLICY_DAYS):
        if is_old(b, day, POLICY_DAYS):
            continue
        t = plain_text(b)
        if t not in skip_texts and t not in [c["text"] for c in cands]:
            cands.append({"id": len(cands) + 1, "text": t})
    cands = cands[-40:]
    if not cands:
        return None
    prompt = EFFECT_PROMPT.format(country=country, today=f"{datetime.now():%Y-%m-%d}", limit=LINE_MAX,
                                  items="\n".join(json.dumps(c, ensure_ascii=False) for c in cands))
    try:
        out, _ = _gemini(prompt, EFFECT_SCHEMA)
    except Exception as e:
        print(f"   (다가오는 일 — 제도 고르기 실패: {e})")
        return None
    src = next((c["text"] for c in cands if c["id"] == out.get("id")), None)
    text = re.sub(r"\s+", " ", str(out.get("text") or "")).strip().rstrip(".")
    if not src or not text:
        return None
    if any(n not in src for n in re.findall(r"\d+", text)) or re.search(r"실시간|안전합니다", text):
        print(f"   (버림 — 원문에 없는 내용: {text})")
        return None
    return _cut(text, LINE_MAX + 6)


def _section(lines, label, rows, empty="이번 주 감지된 소식 없음"):
    if not rows:
        lines.append(f"· {label}: {empty}")
        return
    shown, more = rows[:MAX_LINES], len(rows) - MAX_LINES
    lines.append(f"· {label}: {shown[0]}")
    lines += ["  " + x for x in shown[1:]]
    if more > 0:
        lines.append(f"  그 외 {more}건")


def build(country, city=""):
    end = datetime.now()
    start = end - timedelta(days=DAYS - 1)
    where = f"{country} {city}".strip()
    lines = [f"[놓치지마 주간 요약 · {where} · {start.month}/{start.day}~{end.month}/{end.day}]"]
    quakes, rains, items, others = collect(country, city)
    n_dis = sum(1 for it in items if it["kind"] == "재난")
    n_pol = len(items) - n_dis
    res = rewrite(country, items)
    fmt = lambda rows: [f"{d.month}/{d.day} {t}" for d, t in rows]

    # 재난: 자녀 도시에 해당하는 것만
    dis = (fmt(res["재난"]) if res is not None else ([f"사고·경보 소식 {n_dis}건 (요약 실패 — 확인 필요)"] if n_dis else []))
    dis += number_lines(country, quakes, rains)
    if city:
        _section(lines, "재난", [f"{city} 주변: {dis[0]}"] + dis[1:] if dis else [],
                 empty=f"{city}: 이번 주 놓치지마가 감지한 재난 알림 없음")
        if others:                                     # 나라 안 다른 지역의 큰 일은 한 줄로만
            others.sort(key=lambda x: -x[0])
            _, d, text = others[0]
            more = f" 외 {len(others) - 1}건" if len(others) > 1 else ""
            lines.append(f"  참고: {country} 다른 지역 — {d.month}/{d.day} {text}{more}")
    else:
        _section(lines, "재난", dis)

    f = fx_line(country)
    lines.append("· 환율: " + (f if f else "이번 주 값을 가져오지 못했습니다"))

    if res is None and n_pol:
        lines.append(f"· 제도·정책: 소식 {n_pol}건 (요약 실패 — 확인 필요)")
    else:
        _section(lines, "제도·정책", fmt(res["정책"]) if res else [])

    # 다가오는 일(없으면 줄 생략)
    ahead = holiday_lines(country)
    eff = effect_line(country, [it["text"] for it in items if it["kind"] == "정책"])
    if eff:
        ahead.append(eff)
    if ahead:
        lines.append("· 다가오는 일: " + ahead[0])
        lines += ["  " + x for x in ahead[1:]]
    lines.append(FOOTER)
    return lines


def split_messages(lines, limit=200):
    """카톡 200자 제한에 맞춰 줄 단위로 나눔. 첫 줄(제목)은 이어지는 메시지에도 짧게 붙임."""
    msgs, cur = [], lines[0]
    cont = lines[0].split(" · ")[0] + " · 이어서]"
    for ln in lines[1:]:
        if len(cur) + 1 + len(ln) > limit:
            msgs.append(cur)
            cur = cont
        cur += "\n" + ln
    msgs.append(cur)
    return msgs


def main():
    preview = len(sys.argv) > 1 and sys.argv[1] == "미리"
    targets = load_targets()
    if not targets:
        print("주간요약_나라.json 에 나라가 없습니다.")
        return
    token = None
    if not preview:
        try:
            token = get_valid_token()
        except Exception as e:
            print(f"❌ 토큰 문제: {e}")
            return
    print(f"[주간 요약 · 대상 {len(targets)}곳]" + (" (미리보기 — 보내지 않음)" if preview else ""))
    for c, city in targets:
        for text in split_messages(build(c, city)):
            if preview:
                print(text + f"\n   [{len(text)}자]\n")
                continue
            ok, msg = send_memo(token, text)
            print(("✅ 발송:\n   " if ok else f"❌ 실패({msg}):\n   ") + text.replace("\n", "\n   ") + f"\n   [{len(text)}자]")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
