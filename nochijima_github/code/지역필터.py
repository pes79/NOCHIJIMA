# -*- coding: utf-8 -*-
"""
지역필터.py — '내가 고른 지역(주·도·현) 또는 도시' 안에서 일어난 알림만 통과시키는 필터.

■ 어떻게 동작하나
  1) 감시 프로그램(지진·재난·폭우)이 알림 끝에 좌표 한 줄(@geo:위도,경도)을 붙여 로그에 남김.
     일본 기상청 특보는 좌표 대신 도도부현 코드 한 줄(@iso:JP-01 등)을 붙임.
     → 로그에는 전 세계 소식이 '전부' 그대로 남음(스토리감·홍보 캡처·전체 소식용).
  2) 카톡발송·브리핑이 보낼 때 block_passes()로 "내 지역 안이야?"를 계산해 거름.
     숨은 줄(@geo·@iso)은 카톡에 안 보이게 지움(카톡발송.to_kakao_text).
  3) 미국 기상청(주 단위 특보)은 좌표 대신 '주(州)'로 거름 → us_allowed_states().

■ 지역을 고르는 두 가지 방법
  (가) 주·도·현(1단계 행정구역) — 행정구역.json (공개 데이터 GeoNames+Wikidata, 행정구역_만들기.py 로 생성)
       · 그 지역 '모든 마을(인구 500명↑) 좌표'로 지역 전체를 덮음 → 홋카이도처럼 커도, 오키나와 섬들처럼
         흩어져 있어도 빠짐없이 걸림. 판정: 알림 위치가 그 지역 마을 중 하나라도 '종류별 반경' 안이면 통과.
  (나) 도시 — open-meteo 무료 지오코딩(키 불필요)으로 이름 → 좌표. 한 번 찾은 결과는 지역캐시.json 에 저장.
       · 판정: 도시 한 점에서 '종류별 반경' 안이면 통과.
  지역설정.json 에서 이름 뒤에 "(전역)"을 붙이면 무조건 (가) 주·도·현으로 해석(예: "오키나와(전역)").
  안 붙이면: 주·도·현 공식 이름과 딱 맞으면 (가), 대도시 이름이면 (나) — 예: "도쿄"=도쿄 시내, "도쿄도"=도쿄도 전체.

■ 반경(km) — 종류별로 다름 (이유는 RADIUS_KM 주석 참고). 주·도·현도 같은 반경을 '지역 경계 바깥으로' 적용.

■ 안전 원칙
  - enabled=false 이거나 그 나라가 "전체"면 → 전부 통과(지금과 똑같음).
  - 지역 이름을 못 찾았거나, 좌표 없는 옛 알림이면 → 통과(놓치지 않는 쪽).
"""

import json
import math
import os
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "지역설정.json")
CACHE = os.path.join(BASE, "지역캐시.json")
ADMIN_FILE = os.path.join(BASE, "행정구역.json")
GEO_PREFIX = "@geo:"
ISO_PREFIX = "@iso:"
HIDDEN_PREFIXES = (GEO_PREFIX, ISO_PREFIX)     # 카톡에 안 보이는 숨은 줄
UA = {"User-Agent": "Mozilla/5.0 (nochijima)"}
CACHE_VER = "rule3"          # 캐시 형식이 바뀌면 숫자를 올림(옛 캐시는 자동으로 무시)
BIG_CITY = 1_000_000         # 지역 이름과 겹치는 도시가 이 인구 이상이면 '도시'로 봄(예: 도쿄)

# 종류별 반경(km)
#  지진  : 작은 지진(4~5)은 100km 안에서나 느껴짐 → 100. 규모 6+는 흔들림·피해가 넓어 300,
#          7+는 수백 km 밖에서도 큰 영향(예: 노토 7.5 → 도쿄 흔들림) → 500.
#  폭우  : 비는 동네 단위이고, 우리 강수 데이터가 '도시 한 점' 값이라 그 도시 자체(30km).
#  재난  : GDACS 좌표는 홍수·산불 '중심점'이고 실제 피해 범위가 넓음 → 300.
#  태풍  : 폭이 수백 km라 중심이 멀어도 영향권 → 600.
#  사고  : 대형 인명사고(붕괴·폭발·화재 등)는 그 도시·인근 생활권 일 → 200.
#          (여객기 추락 같은 '초대형'은 좌표를 안 붙여 나라 전체에 알림 — 대형사고감시.py)
RADIUS_KM = {"지진": 100, "지진_6": 300, "지진_7": 500, "폭우": 30, "재난": 300, "태풍": 600, "사고": 200}

# 30개국 → ISO 국가코드 (지오코딩 검색을 그 나라로 한정)
COUNTRY_CODE = {
    "일본": "JP", "베트남": "VN", "대만": "TW", "태국": "TH", "필리핀": "PH", "말레이시아": "MY",
    "싱가포르": "SG", "인도네시아": "ID", "미국": "US", "캐나다": "CA", "호주": "AU", "영국": "GB",
    "독일": "DE", "프랑스": "FR", "인도": "IN", "이탈리아": "IT", "스페인": "ES", "네덜란드": "NL",
    "스위스": "CH", "브라질": "BR", "멕시코": "MX", "뉴질랜드": "NZ", "러시아": "RU", "스웨덴": "SE",
    "튀르키예": "TR", "포르투갈": "PT", "폴란드": "PL", "오스트리아": "AT", "벨기에": "BE",
    "아르헨티나": "AR",
}

# 미국 주: 약자 → (영어, 한국어)  — 미국 기상청 문구·예전 설정 호환용
US_STATES = {
    "AL": ("Alabama", "앨라배마"), "AK": ("Alaska", "알래스카"), "AZ": ("Arizona", "애리조나"),
    "AR": ("Arkansas", "아칸소"), "CA": ("California", "캘리포니아"), "CO": ("Colorado", "콜로라도"),
    "CT": ("Connecticut", "코네티컷"), "DE": ("Delaware", "델라웨어"), "FL": ("Florida", "플로리다"),
    "GA": ("Georgia", "조지아"), "HI": ("Hawaii", "하와이"), "ID": ("Idaho", "아이다호"),
    "IL": ("Illinois", "일리노이"), "IN": ("Indiana", "인디애나"), "IA": ("Iowa", "아이오와"),
    "KS": ("Kansas", "캔자스"), "KY": ("Kentucky", "켄터키"), "LA": ("Louisiana", "루이지애나"),
    "ME": ("Maine", "메인"), "MD": ("Maryland", "메릴랜드"), "MA": ("Massachusetts", "매사추세츠"),
    "MI": ("Michigan", "미시간"), "MN": ("Minnesota", "미네소타"), "MS": ("Mississippi", "미시시피"),
    "MO": ("Missouri", "미주리"), "MT": ("Montana", "몬태나"), "NE": ("Nebraska", "네브래스카"),
    "NV": ("Nevada", "네바다"), "NH": ("New Hampshire", "뉴햄프셔"), "NJ": ("New Jersey", "뉴저지"),
    "NM": ("New Mexico", "뉴멕시코"), "NY": ("New York", "뉴욕"), "NC": ("North Carolina", "노스캐롤라이나"),
    "ND": ("North Dakota", "노스다코타"), "OH": ("Ohio", "오하이오"), "OK": ("Oklahoma", "오클라호마"),
    "OR": ("Oregon", "오리건"), "PA": ("Pennsylvania", "펜실베이니아"), "RI": ("Rhode Island", "로드아일랜드"),
    "SC": ("South Carolina", "사우스캐롤라이나"), "SD": ("South Dakota", "사우스다코타"),
    "TN": ("Tennessee", "테네시"), "TX": ("Texas", "텍사스"), "UT": ("Utah", "유타"),
    "VT": ("Vermont", "버몬트"), "VA": ("Virginia", "버지니아"), "WA": ("Washington", "워싱턴주"),
    "WV": ("West Virginia", "웨스트버지니아"), "WI": ("Wisconsin", "위스콘신"), "WY": ("Wyoming", "와이오밍"),
    "DC": ("Washington DC", "워싱턴DC"),
}


# ── 공통 도구 ───────────────────────────────────────────────
def geo_tag(lat, lon):
    """감시 프로그램이 알림 끝에 붙이는 좌표 한 줄."""
    return f"{GEO_PREFIX}{float(lat):.4f},{float(lon):.4f}"


def iso_tag(code):
    """좌표 대신 행정구역 코드를 붙일 때(일본 기상청). 예: 'JP-01'."""
    return f"{ISO_PREFIX}{code}"


def parse_geo(block):
    m = re.search(r"^" + re.escape(GEO_PREFIX) + r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\s*$",
                  block or "", re.MULTILINE)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def parse_iso(block):
    m = re.search(r"^" + re.escape(ISO_PREFIX) + r"([A-Z]{2}-[A-Z0-9]+)\s*$", block or "", re.MULTILINE)
    return m.group(1) if m else ""


def strip_geo(text):
    """카톡으로 보낼 때 숨은 줄(좌표·코드) 제거."""
    return "\n".join(l for l in (text or "").splitlines() if not l.strip().startswith(HIDDEN_PREFIXES))


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _n(s):
    """비교용: 공백 제거 + 소문자."""
    return re.sub(r"\s+", "", s or "").lower()


def _variants(name):
    """'도쿄 도'→['도쿄도','도쿄'], '가나가와 현'→['가나가와현','가나가와']."""
    v = _n(name)
    out = {v} if v else set()
    short = re.sub(r"(특별시|광역시|도|현|부|주|성|시|군|구)$", "", v)
    if len(short) >= 2:
        out.add(short)
    return out


def load_config():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": False, "regions": {}}


def _load_cache():
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(c):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ── 주·도·현 데이터(행정구역.json) ─────────────────────────────
_ADMIN = None


# 행정구역.json 에 코드(ISO)가 빠진 곳 보완 — 공식 ISO 3166-2 코드
ISO_FIX = {"말레이시아": {"쿠알라룸푸르": "MY-14"}}


def admins(country):
    """그 나라 주·도·현 목록(없으면 [])."""
    global _ADMIN
    if _ADMIN is None:
        try:
            with open(ADMIN_FILE, "r", encoding="utf-8") as f:
                _ADMIN = json.load(f).get("나라", {})
        except Exception:
            _ADMIN = {}
        for c, fix in ISO_FIX.items():
            for a in _ADMIN.get(c, []):
                if not a.get("iso") and a.get("ko") in fix:
                    a["iso"] = fix[a["ko"]]
    return _ADMIN.get(country, [])


def _admin_names(a):
    return {_n(a["ko"]), _n(a["en"]), _n(a.get("ascii", "")), _n(a["code"]), _n(a.get("iso", ""))} \
        | {_n(x) for x in a.get("alt", [])} - {""}


def find_admin(country, name, loose=False):
    """이름·코드로 주·도·현 찾기. loose=True 면 '현·도·주' 등 꼬리를 뗀 이름으로도."""
    key = _n(name)
    if not key:
        return None
    for a in admins(country):
        if key in _admin_names(a):
            return a
    if country == "미국":
        ab = us_state(name)
        if ab:
            return next((a for a in admins(country) if a["code"] == f"US.{ab}"), None)
    if loose:
        keys = _variants(name)
        for a in admins(country):
            names = set()
            for x in [a["ko"], *a.get("alt", [])]:
                names |= _variants(x)
            if keys & names or _n(a["en"]) in keys:
                return a
    return None


def _admin_rule(country, a):
    ko = a["ko"]
    return {"type": "admin", "code": a["code"], "name": ko, "en": a["en"], "iso": a.get("iso", ""),
            "abbr": a.get("abbr", ""),
            "terms": sorted((_variants(ko) | {_n(a["en"])} | {_n(x) for x in a.get("alt", [])}) - {""})}


def _hydrate(country, rule):
    """캐시에 저장된 규칙(코드만)에 좌표 목록을 붙임."""
    if rule and rule.get("type") == "admin":
        a = next((x for x in admins(country) if x["code"] == rule["code"]), None)
        if not a:
            return None
        r = dict(rule)
        r["points"] = a.get("points") or []
        r["rain"] = a.get("rain") or []
        return r
    if rule and rule.get("type") == "city" and not rule.get("iso"):
        home = (find_admin(country, rule.get("admin1", "")) or find_admin(country, rule.get("admin1_en", ""))
                or find_admin(country, rule.get("admin1", ""), loose=True))
        if home and home.get("iso"):
            rule = dict(rule, iso=home["iso"])       # 예전 캐시에 코드가 비어 있던 도시(쿠알라룸푸르 등)
    return rule


# ── 지역 이름 → 규칙 ────────────────────────────────────────
def nearest_admin(country, lat, lon):
    """그 좌표에서 가장 가까운 주·도·현과 거리(km). 행정구역.json 의 마을 좌표 기준. 없으면 (None, None)."""
    if lat is None or lon is None:
        return None, None
    best, best_d = None, None
    for a in admins(country):
        for pt in (a.get("points") or []) + [a.get("center") or []]:
            if len(pt) != 2:
                continue
            d = haversine_km(lat, lon, pt[0], pt[1])
            if best_d is None or d < best_d:
                best, best_d = a, d
    return best, best_d


def _town_ko(country, town, dist_km, lat, lon, adm):
    """마을의 한국어 이름(참고용). 아래를 모두 만족할 때만 돌려줌 — 아니면 '' (이름을 지어내지 않음)
       ① 지명 검색에 한글 이름이 있음  ② 그 마을~진앙 거리가 USGS가 말한 거리와 맞음(같은 이름의 다른 마을 방지)
       ③ 그 마을이 속한 광역이 진앙 좌표의 광역과 같음"""
    cc = COUNTRY_CODE.get(country)
    if not (town and cc and dist_km is not None):
        return ""
    try:
        u = ("https://geocoding-api.open-meteo.com/v1/search?name=" + quote(town)
             + f"&count=10&language=ko&format=json&countryCode={cc}")
        with urlopen(Request(u, headers=UA), timeout=8) as r:
            cands = json.loads(r.read().decode("utf-8")).get("results") or []
    except Exception:
        return ""
    tol = max(15.0, dist_km * 0.2)
    for c in cands:
        name = c.get("name") or ""
        if not re.search(r"[가-힣]", name) or re.search(r"[A-Za-z]", name):
            continue
        if abs(haversine_km(lat, lon, c["latitude"], c["longitude"]) - dist_km) > tol:
            continue
        a2, _ = nearest_admin(country, c["latitude"], c["longitude"])
        if a2 and a2["code"] == adm["code"]:
            return name
    return ""


def quake_place_ko(country, lat, lon, raw="", lookup=True):
    """지진 위치를 한국어로. 광역은 '진앙 좌표'로만 정함(지명 이름으로 찾지 않음).
       예) '지바현 요코시바 남동쪽 13km'  ← 마을 한국어 이름을 확인했을 때만
           '미마로파 지방 부근'            ← 보통(마을 이름 없음 / 광역이 다름 → 마을 이름은 뺌)
           '다바오 지방에서 약 190km'      ← 가장 가까운 광역이 100km 넘게 떨어진 먼바다
       광역을 알 수 없으면(지원 30개국 밖·좌표 없음·500km 밖) '' → 부르는 쪽이 원래 표기를 씀."""
    adm, d = nearest_admin(country, lat, lon)
    if not adm or d > 500:
        return ""
    m = re.match(r"^(.+?)\s+([동서남북]{1,3}쪽)\s*(\d+)\s*km$", (raw or "").strip())
    if m and lookup:
        town = _town_ko(country, m.group(1).strip(), float(m.group(3)), lat, lon, adm)
        if town:
            return f"{adm['ko']} {town} {m.group(2)} {m.group(3)}km"
    if d <= 100:
        return f"{adm['ko']} 부근"
    return f"{adm['ko']}에서 약 {int(round(d, -1))}km"


def us_state(name):
    """'California'·'캘리포니아'·'캘리포니아주'·'CA' → 'CA'. 아니면 None."""
    k = _n(name).replace("주", "") if name else ""
    for ab, (en, ko) in US_STATES.items():
        if k in (ab.lower(), _n(en), _n(ko).replace("주", "")):
            return ab
    return None


def _search(name, cc, lang):
    u = ("https://geocoding-api.open-meteo.com/v1/search?name=" + quote(name)
         + f"&count=10&language={lang}&format=json&countryCode={cc}")
    with urlopen(Request(u, headers=UA), timeout=15) as r:
        d = json.loads(r.read().decode("utf-8"))
    return [x for x in (d.get("results") or []) if str(x.get("feature_code", "")).startswith("PPL")]


def _city_rule(country, name):
    """도시 이름 → 도시 규칙. 못 찾으면 None, 네트워크 실패면 'ERR'."""
    cc = COUNTRY_CODE.get(country)
    if not cc:
        return None
    try:
        ko_res = _search(name, cc, "ko")
        if not ko_res:
            return None
        top = ko_res[0]
        en_res = _search(name, cc, "en")
        en_top = next((x for x in en_res if x.get("id") == top.get("id")), en_res[0] if en_res else {})
    except Exception:
        return "ERR"
    admin_ko, admin_en = top.get("admin1", ""), en_top.get("admin1", "")
    home = (find_admin(country, admin_ko) or find_admin(country, admin_en)
            or find_admin(country, admin_ko, loose=True))
    return {"type": "city", "name": top.get("name", name),
            "lat": round(top["latitude"], 4), "lon": round(top["longitude"], 4),
            "population": top.get("population") or 0,
            "admin1": admin_ko, "admin1_en": admin_en,
            "iso": home.get("iso", "") if home else "",
            "abbr": (home.get("abbr") if home else "") or ((us_state(admin_en) or us_state(admin_ko) or "") if cc == "US" else ""),
            "terms": sorted(_variants(top.get("name", "")) | _variants(admin_ko) | {_n(admin_en)} - {""})}


# 자주 쓰는 다른 한국어 표기 → 지명 검색이 알아듣는 표기 (2026-10-14: '조호바루'가 검색되지 않아 지역 필터가 꺼진 채 나라 전체가 통과했음)
NAME_ALIASES = {
    "말레이시아": {"조호바루": "조호르바루", "조호르 바루": "조호르바루", "조호 바루": "조호르바루", "JB": "조호르바루",
               "쿠알라 룸푸르": "쿠알라룸푸르", "KL": "쿠알라룸푸르", "페낭섬": "페낭", "피낭": "페낭",
               "코타 키나발루": "코타키나발루", "말라카": "믈라카주", "멜라카": "믈라카주", "셀랑고르": "슬랑오르주"},
}


def resolve(country, name, cache=None):
    """지역 이름을 규칙으로 바꿈. 못 찾으면 None. 결과는 지역캐시.json에 저장(주·도·현은 코드만)."""
    own = cache is None
    cache = _load_cache() if own else cache
    key = f"{CACHE_VER}|{country}|{name}"
    if key in cache:
        return _hydrate(country, cache[key])

    raw = (name or "").strip()
    forced = bool(re.search(r"\(?\s*전역\s*\)?$", raw)) or raw.startswith("지역:")
    clean = re.sub(r"\(?\s*전역\s*\)?$", "", raw.replace("지역:", "")).strip()
    clean = NAME_ALIASES.get(country, {}).get(clean, clean)

    rule = None
    if forced:
        a = find_admin(country, clean, loose=True)
        rule = _admin_rule(country, a) if a else None
    elif country == "미국" and us_state(clean):                # 예전 설정 호환: 'California' = 주 전체
        a = find_admin(country, clean)
        rule = _admin_rule(country, a) if a else None
    else:
        a = find_admin(country, clean)                           # 공식 이름·별칭과 딱 맞는 주·도·현
        city = _city_rule(country, clean)
        if city == "ERR":
            if a:
                rule = _admin_rule(country, a)
            else:
                return None                                      # 네트워크 실패 → 캐시 안 하고 다음에 다시
        elif a and city and city.get("population", 0) >= BIG_CITY and _n(city["name"]).startswith(_n(clean)[:2]):
            rule = city                                          # 예: '도쿄' → 도쿄 시내(대도시)
        elif a:
            rule = _admin_rule(country, a)                       # 예: '홋카이도'·'페낭'·'발리'
        elif city:
            rule = city
        else:
            a = find_admin(country, clean, loose=True)
            rule = _admin_rule(country, a) if a else None
    cache[key] = rule
    if own:
        _save_cache(cache)
    return _hydrate(country, rule)


def _places(country, cfg):
    """설정에서 그 나라 지역 목록. '전체'·빈값이면 None."""
    v = (cfg.get("regions") or {}).get(country)
    if not v or v == "전체":
        return None
    places = [v] if isinstance(v, str) else list(v)
    places = [p for p in places if p and p != "전체"]
    return places or None


def resolve_status(country, name, cache):
    """(상태, 규칙). 상태 = 'ok' / 'notfound'(지명 검색으로도 못 찾음) / 'error'(검색 실패 — 다음에 다시)."""
    key = f"{CACHE_VER}|{country}|{name}"
    if key in cache:
        rule = _hydrate(country, cache[key])
        return ("ok", rule) if rule else ("notfound", None)
    rule = resolve(country, name, cache)
    if rule:
        return "ok", rule
    return ("notfound", None) if key in cache else ("error", None)


_UNKNOWN_ALERTED = set()


def _alert_unknown(country, name):
    """인식 못 한 도시 → 시스템 알림(같은 도시는 하루 1번). 이 프로그램 안에서는 한 번만 시도."""
    if (country, name) in _UNKNOWN_ALERTED:
        return
    _UNKNOWN_ALERTED.add((country, name))
    try:
        import 시스템알림
        시스템알림.send(f"도시 인식 실패 {country} {name}",
                     f"⚠️ [놓치지마] '{name}'({country}) 도시를 인식하지 못함\n"
                     f"이 도시 알림은 보내지 않고 보류 중입니다(나라 전체로 넘기지 않음).\n"
                     f"지역설정.json 의 도시 이름을 다른 표기로 고치거나 지역필터.py 의 NAME_ALIASES 에 추가해 주세요.",
                     cooldown_min=24 * 60)
    except Exception:
        pass


def rules_for(country, cfg=None):
    """그 나라의 규칙 목록. 필터 없음(전체)·꺼짐이면 None → 전부 통과.
    설정한 도시를 인식하지 못하면: 나라 전체로 넘기지 않고 '보류' 규칙을 돌려줌(그 나라 지역 알림은 통과 안 함) + 시스템 알림.
    (2026-10-14 변경 — 전에는 '조호바루'를 못 찾자 말레이시아 전체 알림이 그대로 통과했음)"""
    cfg = cfg if cfg is not None else load_config()
    if not cfg.get("enabled"):
        return None
    places = _places(country, cfg)
    if not places:
        return None
    cache = _load_cache()
    rules, missing = [], []
    for p in places:
        st, r = resolve_status(country, p, cache)
        if st == "ok":
            rules.append(r)
        else:
            missing.append((p, st))
            if st == "notfound":
                _alert_unknown(country, p)
    _save_cache(cache)
    if rules:
        return rules                                  # 일부라도 인식되면 그 도시들 기준으로
    return [{"type": "hold", "name": p, "status": st} for p, st in missing]


# ── 알림 블록 판별 ──────────────────────────────────────────
def country_of(block):
    """알림 첫 줄 [..] 에서 나라. 예: '[미국 캘리포니아]'→'미국', '[Japan region]'→'일본'."""
    first = (block or "").splitlines()[0] if block else ""
    m = re.search(r"\[([^\]]+)\]", first)
    if not m:
        return ""
    head = m.group(1)
    tok = head.split()[0].rstrip(",") if head.split() else ""
    if tok in COUNTRY_CODE:
        return tok
    try:
        from 나라정보 import ENGLISH_TO_KR
        base = re.sub(r"\s+region$", "", head.split(",")[0].strip(), flags=re.I).lower()
        return ENGLISH_TO_KR.get(base, ENGLISH_TO_KR.get(tok.lower(), ""))
    except Exception:
        return ""


def kind_of(block, prefix=""):
    first = (block or "").splitlines()[0] if block else ""
    if prefix == "지진감시" or "지진 발생" in first:
        return "지진"
    if prefix in ("호우감시", "일본기상청감시", "말레이시아기상청감시"):
        return "폭우"
    if prefix == "재난감시":
        return "태풍" if re.search(r"태풍|허리케인|사이클론", first) else "재난"
    if prefix == "대형사고감시" or "대형 사고" in first:
        return "사고"    # 좌표가 붙은 '대형'만 거리로 거름. 좌표 없는 '초대형'·옛 알림은 통과
    if not prefix and re.search(r"폭우|호우|비 많음|우박", first):
        return "폭우"
    return None          # 정책·환율·미국기상청 등 → 지역 필터 대상 아님


def radius_km(kind, mag=None, cfg=None):
    r = dict(RADIUS_KM)
    r.update((cfg or {}).get("radius_km") or {})
    if kind == "지진" and mag is not None:
        if mag >= 7.0:
            return r["지진_7"]
        if mag >= 6.0:
            return r["지진_6"]
    return r.get(kind, r["재난"])


def _text_for_match(block):
    keep = [l for l in (block or "").splitlines()
            if not l.strip().startswith((*HIDDEN_PREFIXES, "출처", "자세히", "링크", "━"))]
    return _n(" ".join(keep[:3]))


def _near_any(points, lat, lon, limit):
    """points 중 하나라도 limit km 안이면 True (빠르게: 위도·경도 차이로 먼저 거름)."""
    dlat = limit / 111.0
    for plat, plon in points:
        if abs(plat - lat) > dlat:
            continue
        dlon = limit / (111.0 * max(0.05, math.cos(math.radians((plat + lat) / 2))))
        dl = abs(plon - lon)
        if min(dl, 360 - dl) > dlon:
            continue
        if haversine_km(plat, plon, lat, lon) <= limit:
            return True
    return False


def _rule_match(rule, kind, lat, lon, text, mag, prefix, iso, cfg):
    if rule.get("type") == "hold":
        return False                                  # 인식 못 한 도시 → 보류(보내지 않음)
    limit = radius_km(kind, mag, cfg)
    if rule["type"] == "admin":
        if lat is not None and lon is not None:
            return _near_any(rule.get("points") or [], lat, lon, limit)
        if iso:                                            # 일본 기상청: 도도부현 코드로 정확히
            return iso == rule.get("iso")
        if any(t and t in text for t in rule["terms"]):
            return True
        return prefix != "일본기상청감시"                    # 좌표 없는 옛 알림 → 놓치지 않게 통과
    # 도시 규칙
    if lat is not None and lon is not None:
        return haversine_km(rule["lat"], rule["lon"], lat, lon) <= limit
    if iso and rule.get("iso"):                            # 일본 기상청: 도시가 속한 도도부현 특보면 통과
        return iso == rule["iso"]
    if prefix == "일본기상청감시":                          # (코드 없는 옛 특보) 이름으로
        return any(t and t in text for t in rule["terms"])
    return True                                            # 좌표 없는 옛 알림 → 놓치지 않게 통과


def block_passes(block, prefix="", config=None):
    """이 알림을 내 지역 설정상 보내야 하면 True."""
    cfg = config if config is not None else load_config()
    if not cfg.get("enabled"):
        return True
    kind = kind_of(block, prefix)
    if kind is None:
        return True
    country = country_of(block)
    rules = rules_for(country, cfg) if country else None
    if not rules:
        return True
    lat, lon = parse_geo(block)
    iso = parse_iso(block)
    m = re.search(r"규모\s*(\d+\.\d)", block)
    mag = float(m.group(1)) if m else None
    text = _text_for_match(block)
    return any(_rule_match(r, kind, lat, lon, text, mag, prefix, iso, cfg) for r in rules)


def unplaced_local(block, prefix="", config=None):
    """대형사고·산사태(🚨 한 개) 알림인데 장소 좌표가 없고, 그 나라에 '내 지역' 설정이 있으면 True.
    → 내 지역 근처인지 알 수 없으니 카톡 즉시 발송 대신 브리핑에만 넣음(카톡발송.py 가 사용).
    · 초대형(🚨🚨)은 해당 없음 — 지금처럼 나라 전체에 즉시.
    · 그 나라가 '전체'이거나 지역 필터가 꺼져 있으면 해당 없음 — 장소와 상관없이 지금처럼 즉시.
    (2026-10-15 — 튀르키예 신문의 '미국 숲속 비행기 추락' 기사가 장소 없이 들어와, 좌표가 없으면 통과시키는
     예전 규칙 때문에 California 설정인데도 위스콘신 사고가 카톡으로 나갔음)"""
    s = (block or "").lstrip()
    if not s.startswith("🚨") or s.startswith("🚨🚨"):
        return False
    if kind_of(block, prefix) != "사고":
        return False
    cfg = config if config is not None else load_config()
    if not cfg.get("enabled"):
        return False
    country = country_of(block)
    if not country or not rules_for(country, cfg):
        return False
    lat, _lon = parse_geo(block)
    return lat is None


def passes(country, lat=None, lon=None, place_text="", config=None, kind="지진", mag=None):
    """(예전 방식 호환) 나라·좌표·위치문구로 직접 판별."""
    cfg = config if config is not None else load_config()
    if not cfg.get("enabled"):
        return True
    rules = rules_for(country, cfg)
    if not rules:
        return True
    return any(_rule_match(r, kind, lat, lon, _n(place_text), mag, "", "", cfg) for r in rules)


# ── 다른 감시 프로그램용 ─────────────────────────────────────
def us_allowed_states(config=None):
    """미국 기상청용: 통과시킬 주 약자 집합. None이면 전부 통과."""
    rules = rules_for("미국", config)
    if not rules:
        return None
    if all(r.get("type") == "hold" for r in rules):
        return set()                                  # 설정한 곳을 인식 못 함 → 보류(어느 주도 통과 안 함)
    abbrs = {r.get("abbr") for r in rules if r.get("type") != "hold"}
    return None if (not abbrs or "" in abbrs or None in abbrs) else abbrs


def extra_rain_points(countries, config=None):
    """폭우 감시에 추가할 '내가 고른 곳' 좌표. [(이름, 위도, 경도, 나라)]
    도시는 그 도시 1곳, 주·도·현은 그 지역에 고르게 퍼진 대표 도시 최대 5곳."""
    out = []
    for c in countries:
        for r in rules_for(c, config) or []:
            if r["type"] == "city":
                out.append((r["name"], r["lat"], r["lon"], c))
            elif r["type"] == "admin":
                out += [(nm, la, lo, c) for nm, la, lo in r.get("rain", [])]
    return out


def region_label(country, config=None):
    cfg = config if config is not None else load_config()
    if not cfg.get("enabled"):
        return "전체(필터 꺼짐)"
    places = _places(country, cfg)
    return "·".join(places) if places else "전체"
