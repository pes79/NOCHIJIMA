# -*- coding: utf-8 -*-
"""
말레이시아 기상청(MET Malaysia) 공식 특보 + 배수관개국(JPS) 하천 수위 감시 — 일본기상청감시.py 와 같은 원칙

■ 자료(무료 · 키 불필요 · 정부 공식)
  1) 기상특보   : 말레이시아 정부 공개 API  https://api.data.gov.my/weather/warning   (MET Malaysia 발표)
  2) 하천 수위  : 배수관개국 Public InfoBanjir  https://publicinfobanjir.water.gov.my  (주별 수위 표, 15분 간격 갱신)
     ※ 정부 공개 API 의 flood-warning 은 2024-02-23 이후 갱신이 멈춘 자료라 쓰지 않음(2026-10-14 확인).
■ 무엇을 보내나
  · 기상특보: 육지에 영향이 있는 것만(폭우·지속 강우·뇌우·열대폭풍·홍수). 바다 특보(강풍·높은 파도)와 '특보 없음'은 뺌.
             말레이어 특보 이름은 아래 고정 표로 한국어로 옮김(AI·번역기 안 씀). 표에 없는 이름은 원문을 그대로 적고 브리핑으로만.
             폭우 '심각·위험' 단계 → 🚨(카톡), 폭우 '주의' 단계·뇌우 → 🌧️(브리핑만)
  · 하천 수위: 주(州)별로 묶어 1건. '위험(Danger)' 수위 측정소가 있으면 🚨(카톡), '경고·주의'만 있으면 🌧️(브리핑만).
  · 알림마다 주 코드(@iso:MY-xx)를 붙여, 받는 사람이 등록한 주(또는 등록한 도시가 있는 주)만 통과(지역필터.py).
■ 같은 주·같은 날은 등급이 올라갈 때만 다시 알림(기상특보·하천 수위 따로).
■ 받은 자료가 24시간보다 오래되면 "말레이시아 기상청 자료가 오래됨" 시스템 알림(하루 1번).
■ 실행 : 30분마다(작업 스케줄러 nochijima_my_met)
         py 말레이시아기상청감시.py 미리   ← 화면에만 보고, 알린 기록·시스템 알림은 남기지 않음
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

from 지역필터 import iso_tag, admins

BASE = os.path.dirname(os.path.abspath(__file__))
SAVE_FILE = os.path.join(BASE, "my_met_seen.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
WARN_URL = "https://api.data.gov.my/weather/warning?limit=200"
LEVEL_URL = "https://publicinfobanjir.water.gov.my/index.php/aras-air/data-paras-air/aras-air-data/?state={code}&district=ALL&station=ALL&lang=en"
STALE_HOURS = 24
RANK = {"yellow": 1, "orange": 2, "red": 3}

# ── 말레이어 특보 이름 → 한국어 (고정 표). (낱말, 한국어, 등급) — 위에서부터 먼저 맞는 것 ──
#   등급: red=🚨🚨(카톡·최고), orange=🚨(카톡), yellow=🌧️(브리핑만), None=보내지 않음(바다 특보 등)
WARNING_KO = [
    # (특보 이름에 '모두' 들어 있어야 하는 낱말들, 한국어, 등급) — 실제 이름 예: "Amaran Hujan Lebat Tahap Buruk"
    (("Tiada Nasihat",),            "특보 없음",                 None),
    (("Laut Bergelora",),           "강풍·높은 파도(바다)",        None),
    (("Gelombang",),                "높은 파도(바다)",             None),
    (("Kabus",),                    "짙은 안개",                  None),
    (("Hujan Lebat", "Bahaya"),     "폭우 경보(위험 단계)",         "red"),
    (("Hujan Lebat", "Buruk"),      "폭우 경보(심각 단계)",         "orange"),
    (("Hujan Lebat", "Waspada"),    "폭우 주의보(주의 단계)",       "yellow"),
    (("Hujan Berterusan", "Bahaya"), "지속 강우 경보(위험 단계)",    "red"),
    (("Hujan Berterusan", "Buruk"),  "지속 강우 경보(심각 단계)",    "orange"),
    (("Hujan Berterusan", "Waspada"), "지속 강우 주의보(주의 단계)", "yellow"),
    (("Hujan Berterusan",),         "지속 강우 경보",             "orange"),
    (("Hujan Lebat",),              "폭우 경보",                 "orange"),
    (("Siklon Tropika",),           "열대저기압 경보",             "orange"),
    (("Ribut Tropika",),            "열대폭풍 경보",              "orange"),
    (("Banjir",),                   "홍수 경보",                 "orange"),
    (("Ribut Petir",),              "뇌우 경보",                 "yellow"),
    (("Angin Kencang",),            "강풍 경보(육지)",             "yellow"),
]

# 특보 본문(영어)의 주 이름 → 행정구역.json 영어 이름
STATE_EN = {
    "perlis": "Perlis", "kedah": "Kedah", "penang": "Penang", "pulau pinang": "Penang", "perak": "Perak",
    "kelantan": "Kelantan", "terengganu": "Terengganu", "pahang": "Pahang", "selangor": "Selangor",
    "w.p. kuala lumpur": "Kuala Lumpur", "kuala lumpur": "Kuala Lumpur", "w.p. putrajaya": "Putrajaya", "putrajaya": "Putrajaya",
    "w.p. labuan": "Labuan", "labuan": "Labuan", "negeri sembilan": "Negeri Sembilan", "melaka": "Melaka", "malacca": "Melaka",
    "johor": "Johor", "sabah": "Sabah", "sarawak": "Sarawak",
}
# 배수관개국 주 코드 → 행정구역.json 영어 이름 (MCK=시험용 자료는 뺌)
JPS_STATES = {"PLS": "Perlis", "KDH": "Kedah", "PNG": "Penang", "PRK": "Perak", "SEL": "Selangor", "WLH": "Kuala Lumpur",
              "PTJ": "Putrajaya", "NSN": "Negeri Sembilan", "MLK": "Melaka", "JHR": "Johor", "PHG": "Pahang",
              "TRG": "Terengganu", "KEL": "Kelantan", "SRK": "Sarawak", "SAB": "Sabah", "WLP": "Labuan"}


def admin_by_en(en):
    return next((a for a in admins("말레이시아") if a.get("en") == en), None)


def get_text(url):
    with urlopen(Request(url, headers=UA), timeout=40) as r:
        return r.read().decode("utf-8", "replace")


def warning_kind(title_bm):
    t = (title_bm or "").lower()
    for words, ko, level in WARNING_KO:
        if all(w.lower() in t for w in words):
            return ko, level, True
    return f"기상 특보(원문: {title_bm})", "yellow", False     # 표에 없는 이름 → 원문 그대로, 브리핑으로만


def states_in(text_en):
    """'… over the states of Perlis • Kedah (Kubang Pasu, …) • Penang • Sarawak: Serian …' → {영어 주 이름: '세부 지역'}"""
    out = {}
    m0 = re.search(r"states? of\s+(.*)", text_en or "", re.S) or re.search(r"\bover\s+(?:the\s+)?(.*)", text_en or "", re.S)
    body = m0.group(1) if m0 else (text_en or "")
    for seg in re.split(r"[•;]|\band\b(?=\s+(?:W\.P\.|[A-Z]))", body):
        seg = seg.strip(" .\n")
        m = re.match(r"^((?:W\.P\.\s*)?[A-Za-z .]+?)(?:\s*[:(]\s*(.*?))?\)?$", seg)
        if not m:
            continue
        key = m.group(1).strip().lower()
        en = STATE_EN.get(key)
        if en:
            out[en] = (out.get(en, "") + " " + (m.group(2) or "")).strip()
    return out


def _fmt(ts):
    try:
        return datetime.strptime(ts[:16], "%Y-%m-%dT%H:%M").strftime("%m/%d %H:%M")
    except Exception:
        return ts or ""


# ── 기상특보 ──
def weather_blocks(warns, seen, today):
    out, skipped = [], []
    for w in warns:
        wi = w.get("warning_issue") or {}
        ko, level, known = warning_kind(wi.get("title_bm", ""))
        if level is None:
            skipped.append(ko)
            continue
        try:
            if datetime.strptime((w.get("valid_to") or "")[:16], "%Y-%m-%dT%H:%M") < datetime.now() - timedelta(hours=1):
                continue                                   # 이미 끝난 특보
        except Exception:
            pass
        for en, detail in states_in(w.get("text_en", "")).items():
            a = admin_by_en(en)
            if not a:
                continue
            day = (wi.get("issued") or today)[:10]
            if not should_alert(seen, f"W-{a['code']}", day, level):
                continue
            emoji = "🚨🚨" if level == "red" else "🚨" if level == "orange" else "🌧️"
            lines = [f"{emoji} [말레이시아 {a['ko']}] {ko}"]
            detail = re.sub(r"\s*\([^)]*$", "", detail).strip(" ,")          # 잘린 괄호 정리
            if detail:
                lines.append(f"대상 지역: {detail[:80]}")
            lines.append(f"기간: {_fmt(w.get('valid_from'))} ~ {_fmt(w.get('valid_to'))} (현지)")
            if not known:
                lines.append("※ 처음 보는 특보 이름이라 원문을 그대로 적었습니다.")
            lines.append(f"출처: 말레이시아 기상청(MET) · 발표 {_fmt(wi.get('issued'))}")
            if a.get("iso"):
                lines.append(iso_tag(a["iso"]))
            out.append((RANK[level], "\n".join(lines)))
    return out, skipped


# ── 하천 수위 ──
def parse_levels(html):
    """수위 표 → [(측정소, 지구, 갱신 시각, 현재 수위, 주의, 경고, 위험)]"""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        c = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        c = [x for x in c if x != ""]
        if len(c) < 11 or not c[0].isdigit():
            continue
        def num(s):
            try:
                return float(s)
            except Exception:
                return None
        try:
            t = datetime.strptime(c[6], "%d/%m/%Y %H:%M")
        except Exception:
            continue
        rows.append((c[2], c[3], t, num(c[7]), num(c[9]), num(c[10]), num(c[11]) if len(c) > 11 else None))
    return rows


def level_of(wl, alert, warning, danger):
    if wl is None:
        return None
    if danger is not None and wl >= danger:
        return "red"
    if warning is not None and wl >= warning:
        return "orange"
    if alert is not None and wl >= alert:
        return "yellow"
    return None


def river_blocks(seen, today):
    out, newest, errors = [], None, 0
    for code, en in JPS_STATES.items():
        a = admin_by_en(en)
        try:
            rows = parse_levels(get_text(LEVEL_URL.format(code=code)))
        except Exception:
            errors += 1
            continue
        fresh = [r for r in rows if datetime.now() - r[2] <= timedelta(hours=6)]     # 몇 시간째 안 바뀐 측정소(고장)는 뺌
        if rows:
            newest = max([newest] + [r[2] for r in rows if r[2] <= datetime.now() + timedelta(hours=1)] if newest else [r[2] for r in rows])
        hits = [(level_of(r[3], r[4], r[5], r[6]), r) for r in fresh]
        hits = [(lv, r) for lv, r in hits if lv]
        if not hits or not a:
            continue
        worst = max(RANK[lv] for lv, r in hits)
        top = max((r for lv, r in hits if RANK[lv] == worst), key=lambda r: (r[3] or 0) - (r[6] or 0))
        level = {3: "red", 2: "orange", 1: "yellow"}[worst]
        note = ""
        if level == "red" and sum(1 for lv, r in hits if lv in ("red", "orange")) < 2:
            # 위험 수위 측정소가 1곳뿐이고 주변 측정소는 정상 → 측정 오류일 수 있음(2026-10-14 트렝가누 Kg. Mangkuk 사례).
            #  카톡으로 보내지 않고 브리핑으로만, '확인 필요' 표시
            level, note = "orange", "※ 위험 수위 측정소가 1곳뿐이라 측정 오류일 수 있어 브리핑으로만 알립니다."
        if not should_alert(seen, f"R-{a['code']}", today, level):
            continue
        n = {k: sum(1 for lv, r in hits if lv == k) for k in ("red", "orange", "yellow")}
        word = {"red": "하천 위험 수위", "orange": "하천 경고 수위", "yellow": "하천 주의 수위"}[level]
        if note:
            word = "하천 위험 수위(1곳 — 확인 필요)"
        emoji = "🚨" if level == "red" else "🌧️"                    # 위험 단계만 카톡, 나머지는 브리핑만
        lines = [f"{emoji} [말레이시아 {a['ko']}] {word} (측정소 위험 {n['red']}·경고 {n['orange']}·주의 {n['yellow']}곳)",
                 f"가장 높은 곳: {top[0]} ({top[1]}) 수위 {top[3]:.2f}m · 위험 기준 {top[6]:.2f}m" if top[6] is not None else f"가장 높은 곳: {top[0]} ({top[1]})",
                 f"출처: 말레이시아 배수관개국(JPS) Public InfoBanjir · {top[2]:%m/%d %H:%M} (현지)"]
        if note:
            lines.insert(2, note)
        if a.get("iso"):
            lines.append(iso_tag(a["iso"]))
        out.append((worst, "\n".join(lines)))
    return out, newest, errors


# ── 같은 주·같은 날: 등급이 오를 때만 ──
def should_alert(seen, key, day, level):
    rank = RANK[level]
    if any(f"{key}-{day}-L{r}" in seen for r in range(rank, 4)):
        return False
    seen.add(f"{key}-{day}-L{rank}")
    return True


def load_seen():
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("seen_ids", []))
    except Exception:
        return set()


def save_seen(seen):
    ids = sorted(seen, key=lambda u: (re.search(r"\d{4}-\d\d-\d\d", u) or [""])[0])[-2000:]
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "seen_ids": ids}, f, ensure_ascii=False, indent=2)


def stale_alert(name, t, preview):
    if t and datetime.now() - t <= timedelta(hours=STALE_HOURS):
        return False
    when = t.strftime("%m/%d %H:%M") if t else "알 수 없음"
    print(f"⚠️ 말레이시아 {name} 자료가 오래됨 (마지막 {when})")
    if not preview:
        try:
            import 시스템알림
            시스템알림.send(f"말레이시아 {name} 자료 오래됨",
                         f"⚠️ [놓치지마] 말레이시아 {name} 자료가 오래됨\n마지막 자료: {when} (현지)\n"
                         f"{STALE_HOURS}시간 넘게 새 자료가 없습니다. 말레이시아 특보 감시가 멈췄을 수 있어 확인이 필요합니다.",
                         cooldown_min=24 * 60)
        except Exception:
            pass
    return True


def main():
    preview = len(sys.argv) > 1 and sys.argv[1] == "미리"
    seen = load_seen()
    today = datetime.now().strftime("%Y-%m-%d")
    blocks = []
    try:
        warns = json.loads(get_text(WARN_URL))
        latest = max((datetime.strptime(w["warning_issue"]["issued"][:16], "%Y-%m-%dT%H:%M") for w in warns
                      if (w.get("warning_issue") or {}).get("issued")), default=None)
        stale_alert("기상청 특보", latest, preview)       # 특보 없음·바다 특보도 매일 나오므로, 가장 최근 발표가 24시간 넘으면 멈춘 것
        wb, skipped = weather_blocks(warns, seen, today)
        blocks += wb
        if skipped:
            print(f"🟢 바다·특보 없음 등 보내지 않은 특보 {len(skipped)}건: {', '.join(sorted(set(skipped)))}")
    except Exception as e:
        print(f"⚠️ 말레이시아 기상청 특보를 가져오지 못했습니다. ({e})")
    rb, newest, errors = river_blocks(seen, today)
    blocks += rb
    if errors == len(JPS_STATES):
        print("⚠️ 말레이시아 하천 수위를 가져오지 못했습니다. (모든 주 실패)")
    else:
        stale_alert("하천 수위", newest, preview)
    if blocks:
        blocks.sort(key=lambda x: -x[0])
        print("\n\n".join(b for _, b in blocks))
    else:
        print("🟢 말레이시아 기상청: 새 특보·하천 위험 없음")
    if not preview:
        save_seen(seen)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
