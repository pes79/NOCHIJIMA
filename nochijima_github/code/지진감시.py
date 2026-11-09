# -*- coding: utf-8 -*-
"""
지진 감시 프로그램 (전 세계 · USGS 미국 지질조사국 자료)
- 규모 4.0 이상 지진만 가져옵니다. (그 미만은 무시)
- 최근 일정 시간(기본 24시간) 안에 발생한 지진을 봅니다.
- 각 지진의 지역·규모·발생시각을 한국어로 정리해서 보여줍니다.
- 이미 알린 지진은 파일에 기록해 두고, 다음 실행 때 다시 알리지 않습니다.
- 기록 파일: 같은 폴더의 'seen_quakes.json'
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

from 나라정보 import local_kst_str, ENGLISH_TO_KR, country_ko   # 현지시각 + 영어나라명→한글
from 지역필터 import geo_tag, US_STATES, quake_place_ko   # 알림 끝 좌표 한 줄 · 미국 주 약자표

# ── 설정 ─────────────────────────────────────────────
MIN_MAGNITUDE = 4.0        # 기본 임계값(그 외 모든 나라). USGS 요청도 이 값으로 함.

# 나라별 알림 임계값 — 이 목록에 없는 나라는 위 MIN_MAGNITUDE(4.0)를 씁니다.
#  (일본은 지진이 잦아 4.0~4.4는 알림 제외, 4.5 이상만 알림)
COUNTRY_MIN_MAG = {
    "일본": 4.5,
}

# 지역별 알림 임계값 — 나라보다 '먼저' 적용. (USGS 위치 영어명으로 매칭)
#  알래스카는 지진이 매우 잦아 5.0 이상만 알림(미국 나머지 지역은 그대로 4.0).
REGION_MIN_MAG = {
    "Alaska": 5.0,
}

TIME_WINDOW_HOURS = 24     # 최근 몇 시간 안의 지진을 볼지 (24 = 하루, 1 = 최근 1시간)

# USGS 무료 지진 API (API 키 필요 없음)
API_URL = (
    "https://earthquake.usgs.gov/fdsnws/event/1/query"
    "?format=geojson&orderby=time&minmagnitude={mag}&starttime={start}"
)

# 이미 알린 지진 id를 저장할 파일 (이 프로그램과 같은 폴더)
SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_quakes.json")

# 나라 이름 한국어 변환표 (지진이 자주 나는 나라 위주)
COUNTRY_KR = {
    "Japan": "일본", "Indonesia": "인도네시아", "Philippines": "필리핀",
    "Chile": "칠레", "Peru": "페루", "Mexico": "멕시코", "Russia": "러시아",
    "New Zealand": "뉴질랜드", "Papua New Guinea": "파푸아뉴기니",
    "Turkey": "튀르키예", "Greece": "그리스", "Italy": "이탈리아",
    "Iran": "이란", "China": "중국", "Taiwan": "대만", "India": "인도",
    "Nepal": "네팔", "Argentina": "아르헨티나", "Colombia": "콜롬비아",
    "Ecuador": "에콰도르", "Alaska": "미국 알래스카", "California": "미국 캘리포니아",
    "Hawaii": "미국 하와이", "Fiji": "피지", "Tonga": "통가", "Vanuatu": "바누아투",
    "Guatemala": "과테말라", "Nicaragua": "니카라과", "El Salvador": "엘살바도르",
    "Costa Rica": "코스타리카", "Panama": "파나마", "Afghanistan": "아프가니스탄",
    "Pakistan": "파키스탄", "Myanmar": "미얀마", "Burma (Myanmar)": "미얀마",
    "South Korea": "대한민국", "North Korea": "북한", "Solomon Islands": "솔로몬 제도",
    "Puerto Rico": "푸에르토리코", "Kermadec Islands": "케르마덱 제도",
    "United States": "미국",
    # 미국 주(州) 이름 (USGS는 미국 지진을 주 이름으로 표기함)
    "Oregon": "미국 오리건주", "Washington": "미국 워싱턴주",
    "Nevada": "미국 네바다주", "Utah": "미국 유타주", "Idaho": "미국 아이다호주",
    "Montana": "미국 몬태나주", "Wyoming": "미국 와이오밍주",
    "Oklahoma": "미국 오클라호마주", "Texas": "미국 텍사스주",
    "Tennessee": "미국 테네시주", "Missouri": "미국 미주리주",
    "Kansas": "미국 캔자스주",
}

# 미국 50개 주 전체 (USGS는 미국 지진을 주 이름으로 표기 → 어느 주든 '미국'으로 잡히게)
US_STATES_KR = {
    "Alabama": "미국 앨라배마주", "Alaska": "미국 알래스카", "Arizona": "미국 애리조나주",
    "Arkansas": "미국 아칸소주", "California": "미국 캘리포니아", "Colorado": "미국 콜로라도주",
    "Connecticut": "미국 코네티컷주", "Delaware": "미국 델라웨어주", "Florida": "미국 플로리다주",
    "Georgia": "미국 조지아주", "Hawaii": "미국 하와이", "Idaho": "미국 아이다호주",
    "Illinois": "미국 일리노이주", "Indiana": "미국 인디애나주", "Iowa": "미국 아이오와주",
    "Kansas": "미국 캔자스주", "Kentucky": "미국 켄터키주", "Louisiana": "미국 루이지애나주",
    "Maine": "미국 메인주", "Maryland": "미국 메릴랜드주", "Massachusetts": "미국 매사추세츠주",
    "Michigan": "미국 미시간주", "Minnesota": "미국 미네소타주", "Mississippi": "미국 미시시피주",
    "Missouri": "미국 미주리주", "Montana": "미국 몬태나주", "Nebraska": "미국 네브래스카주",
    "Nevada": "미국 네바다주", "New Hampshire": "미국 뉴햄프셔주", "New Jersey": "미국 뉴저지주",
    "New Mexico": "미국 뉴멕시코주", "New York": "미국 뉴욕주", "North Carolina": "미국 노스캐롤라이나주",
    "North Dakota": "미국 노스다코타주", "Ohio": "미국 오하이오주", "Oklahoma": "미국 오클라호마주",
    "Oregon": "미국 오리건주", "Pennsylvania": "미국 펜실베이니아주", "Rhode Island": "미국 로드아일랜드주",
    "South Carolina": "미국 사우스캐롤라이나주", "South Dakota": "미국 사우스다코타주",
    "Tennessee": "미국 테네시주", "Texas": "미국 텍사스주", "Utah": "미국 유타주",
    "Vermont": "미국 버몬트주", "Virginia": "미국 버지니아주", "Washington": "미국 워싱턴주",
    "West Virginia": "미국 웨스트버지니아주", "Wisconsin": "미국 위스콘신주", "Wyoming": "미국 와이오밍주",
    "Puerto Rico": "미국 푸에르토리코", "Guam": "미국 괌", "Washington DC": "미국 워싱턴DC",
}
COUNTRY_KR.update(US_STATES_KR)
# 나라정보의 30개국 영어명도 흡수 (말레이시아 등 빠진 나라 보완). 대문자 표기로도 잡히게.
for _en, _kr in ENGLISH_TO_KR.items():
    COUNTRY_KR.setdefault(_en.title(), _kr)   # 'malaysia'→'Malaysia', 'united states'→'United States'


# USGS가 나라 이름 대신 쓰는 지역 이름 → 나라(영어). 예: 'Philippine Islands region' → 필리핀
REGION_ALIAS = {
    "philippine islands": "Philippines", "kuril islands": "Russia", "fiji islands": "Fiji",
    "western australia": "Australia", "xizang": "China", "tibet": "China",
    "honshu": "Japan", "hokkaido": "Japan", "kyushu": "Japan", "shikoku": "Japan", "ryukyu islands": "Japan",
    "izu islands": "Japan", "bonin islands": "Japan", "volcano islands": "Japan",
    "mindanao": "Philippines", "luzon": "Philippines", "mindoro": "Philippines", "samar": "Philippines",
    "sumatra": "Indonesia", "java": "Indonesia", "sulawesi": "Indonesia", "banda sea": "Indonesia",
    "molucca sea": "Indonesia", "kamchatka": "Russia", "sakhalin": "Russia",
    "north island of new zealand": "New Zealand", "south island of new zealand": "New Zealand",
    "kermadec islands": "Kermadec Islands", "mariana islands": "Northern Mariana Islands",
}
# 앞에 붙는 꾸밈말: 'off the east coast of', 'near the coast of', 'south of the', 'western', 'northwest of the' …
_LEAD = re.compile(r"^(?:(?:off|near)\s+(?:the\s+)?(?:\w+\s+)?coast\s+of|"
                   r"(?:north|south|east|west|northeast|northwest|southeast|southwest)\s+of|"
                   r"(?:northern|southern|eastern|western|central))\s+(?:the\s+)?", re.IGNORECASE)


def _lookup_country(name):
    """정확히 아는 이름만 한국어로. 모르면 None."""
    n = (name or "").strip()
    if not n:
        return None
    if n in COUNTRY_KR:
        return COUNTRY_KR[n]
    if n in US_STATES:                                  # 주 약자('CA') → '미국 캘리포니아'. 멕시코는 'MX'.
        en, ko = US_STATES[n]
        return US_STATES_KR.get(en, "미국 " + ko)
    if n == "MX":
        return "멕시코"
    if n.title() in COUNTRY_KR:
        return COUNTRY_KR[n.title()]
    return country_ko(n)


def kr_country(country):
    """USGS 나라/주/지역 이름을 한글로. 대소문자 상관없이.
    'Japan region'·'Philippine Islands region'·'off the coast of Oregon'·'south of the Fiji Islands' 처럼
    'region'이 붙거나 꾸밈말이 있어도 나라를 찾음. 끝내 모르면 원문 그대로.
    ※ 전엔 'Izu Islands, Japan region' 이 '[Japan region]'으로 찍혀 일본 지진으로 인식되지 않았음(2026-10-12 수정)."""
    raw = (country or "").strip()
    cands = [raw]
    base = re.sub(r"\s+region$", "", raw, flags=re.IGNORECASE).strip()
    cands.append(base)
    stripped = _LEAD.sub("", base).strip()
    cands.append(stripped)
    for c in list(cands):
        alias = REGION_ALIAS.get(c.lower())
        if alias:
            cands.append(alias)
    for c in cands:
        ko = _lookup_country(c)
        if ko:
            return ko
    return country

# 방위 약자 → 한글 (N=북, E=동, S=남, W=서)
DIR_LETTER = {"N": "북", "E": "동", "S": "남", "W": "서"}


def to_korean_place(place):
    """USGS의 영어 지역명을 최대한 한국어로 다듬습니다.
    예) '148 km E of Miyako, Japan' -> '일본 Miyako 동쪽 148km 지점'"""
    if not place:
        return "(위치 정보 없음)"

    # 나라 부분 분리 (마지막 쉼표 기준)
    if ", " in place:
        detail, country = place.rsplit(", ", 1)
        country_kr = kr_country(country)
    else:
        detail, country_kr = place, ""

    # 'N km DIR of 지점' 형태를 한국어로
    m = re.match(r"^(\d+)\s*km\s+([NSEW]+)\s+of\s+(.+)$", detail)
    if m:
        dist, dircode, spot = m.groups()
        dir_kr = "".join(DIR_LETTER.get(ch, ch) for ch in dircode)
        detail_kr = f"{spot} {dir_kr}쪽 {dist}km 지점"
    else:
        detail_kr = detail  # 형태가 다르면 원문 유지

    return f"{country_kr} {detail_kr}".strip()


def magnitude_mark(mag):
    """규모에 따라 눈에 띄는 표시를 붙입니다."""
    if mag >= 6.0:
        return "🔴 강진"
    if mag >= 5.0:
        return "🟠 중강진"
    return "🟡"


def to_korean_time(epoch_ms):
    """USGS의 시각(UTC 밀리초)을 한국시각(KST) 한국어 문자열로 변환합니다."""
    kst = timezone(timedelta(hours=9))
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(kst)
    return dt.strftime("%Y년 %m월 %d일 %H시 %M분 (한국시각)")


def to_time_short(epoch_ms):
    """발송용 짧은 시각. 예) '8월 8일 02:50'"""
    kst = timezone(timedelta(hours=9))
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(kst)
    return f"{dt.month}월 {dt.day}일 {dt:%H:%M}"


def place_parts(place):
    """장소를 (나라, 지역) 으로 나눠서 돌려줍니다. 나라가 없으면 지역만."""
    if not place:
        return "", ""
    if ", " in place:
        detail, country = place.rsplit(", ", 1)
        country_kr = kr_country(country)
    else:
        # 'Japan region'·'Kermadec Islands region'처럼 쉼표 없는 지역명 → 나라로 번역
        #  ※ 전엔 '[Japan region]'으로 찍혀 '일본'으로 인식이 안 돼 발송되지 않았음.
        mapped = kr_country(place)
        if mapped != place:
            return mapped, ""
        detail, country_kr = place, ""

    m = re.match(r"^(\d+)\s*km\s+([NSEW]+)\s+of\s+(.+)$", detail)
    if m:
        dist, dircode, spot = m.groups()
        dir_kr = "".join(DIR_LETTER.get(ch, ch) for ch in dircode)
        region = f"{spot} {dir_kr}쪽 {dist}km"
    else:
        region = detail
    return country_kr, region


def min_mag_for(place):
    """그 지진 위치에 맞는 알림 임계값을 돌려줍니다.
    우선순위: 지역별(알래스카 5.0) → 나라별(일본 4.5) → 기본(4.0)."""
    country_kr, _ = place_parts(place)      # 예: '일본', '미국 알래스카'
    pl = place or ""
    # 1) 지역별(가장 먼저). 예: 알래스카는 지진 잦아 5.0
    for region_en, thr in REGION_MIN_MAG.items():
        if region_en in pl:
            return thr
    # 2) 나라별. 일본 판별: 한글 변환이 '일본'이거나 원문에 'Japan'(예: 'Japan region')
    if "일본" in country_kr or "Japan" in pl:
        return COUNTRY_MIN_MAG.get("일본", MIN_MAGNITUDE)
    for name, thr in COUNTRY_MIN_MAG.items():   # 다른 나라도 설정하면 여기서 적용
        if name in country_kr:
            return thr
    return MIN_MAGNITUDE


# 예비 주소(2026-10-20): USGS 정적 파일 피드 — 지난 24시간 규모 2.5 이상(아래에서 4.0·나라별 기준으로 다시 거름).
#  10/20 하루 동안 질의 주소(fdsnws)가 서버 오류(502)·응답 시간 초과로 43번 실패해 '지진감시 멈춤' 경고가 4번 나갔음.
#  같은 시각 정적 피드는 정상이라, 질의 주소가 두 번 실패하면 이 주소로 받음.
FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
RETRY_WAIT = 25           # 질의 주소가 실패하면 이만큼(초) 기다렸다가 한 번 더


def _fetch_json(url, timeout=20):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _usgs_data():
    """① 질의 주소 → 실패하면 25초 뒤 한 번 더 → ② 그래도 안 되면 예비 정적 피드. 셋 다 실패하면 오류를 올림."""
    start = (datetime.now(timezone.utc) - timedelta(hours=TIME_WINDOW_HOURS)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    url = API_URL.format(mag=MIN_MAGNITUDE, start=start)
    errs = []
    for attempt in range(2):
        try:
            return _fetch_json(url)
        except Exception as e:
            errs.append(f"{type(e).__name__}: {str(e)[:60]}")
            if attempt == 0:
                time.sleep(RETRY_WAIT)
    try:
        data = _fetch_json(FEED_URL, timeout=30)
        print(f"   (USGS 질의 주소가 2번 응답하지 않아 예비 주소로 받음 — {errs[-1]})")
        return data
    except Exception as e:
        raise RuntimeError(f"질의 주소 2번·예비 주소 1번 모두 실패 — {errs[-1]} / 예비: {type(e).__name__}")


def get_recent_quakes():
    """USGS에서 최근 지진을 가져옵니다.
    USGS에는 4.0으로 요청하되, 나라별 임계값(일본 4.5)으로 한 번 더 거릅니다."""
    data = _usgs_data()

    quakes = []
    for feat in data.get("features", []):
        p = feat.get("properties", {})
        mag = p.get("mag")
        place = p.get("place", "")
        if mag is None or mag < min_mag_for(place):
            continue  # 나라별 임계값 미달은 무시 (일본 4.5, 그 외 4.0)
        coords = (feat.get("geometry") or {}).get("coordinates") or [None, None]
        # 같은 지진이 기관마다 다른 번호(us…, pt… 등)로 올 수 있어 USGS 가 주는 번호 목록(ids)을 함께 보관(2026-10-20)
        aliases = [x for x in str(p.get("ids") or "").split(",") if x] + [feat.get("id")]
        quakes.append(
            {
                "id": feat.get("id"),
                "ids": sorted(set(a for a in aliases if a)),
                "mag": mag,
                "place": p.get("place", ""),
                "time": p.get("time", 0),
                "lon": coords[0],     # 지역 필터용 좌표 (USGS는 [경도, 위도] 순서)
                "lat": coords[1],
            }
        )
    # 최신순 정렬
    quakes.sort(key=lambda q: q["time"], reverse=True)
    return quakes


def load_seen_ids():
    """이미 알린 지진 id 목록을 불러옵니다."""
    if not os.path.exists(SAVE_FILE):
        return set()
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("seen_ids", []))
    except Exception:
        return set()


def save_seen_ids(seen_ids):
    """알린 지진 id 목록을 저장합니다 (들어온 순서대로, 오래된 것부터 버려 최근 5000개 보관).
    (2026-10-20 — 전에는 순서 없는 묶음에서 1000개를 잘라 최근 번호가 임의로 빠질 수 있었음. 이미 1000개가 차 있었음)"""
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            old = json.load(f).get("seen_ids", [])
    except Exception:
        old = []
    old_set = set(old)
    ids = ([i for i in old if i in seen_ids] + [i for i in seen_ids if i not in old_set])[-5000:]
    payload = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "seen_ids": ids,
    }
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def make_quake_block(q):
    """지진 1건을 발송용 메시지 블록으로 만듭니다."""
    country, region = place_parts(q["place"])
    head_loc = country if country else (region or "해외")
    mag = q["mag"]
    dt_utc = datetime.fromtimestamp(q["time"] / 1000, tz=timezone.utc)
    when = local_kst_str(country, dt_utc)   # 현지 X (한국 Y)

    # 지역·규모 줄 — 광역(주·도·현)은 진앙 '좌표'로 정함. 영어 마을 이름은 광역을 못 정할 때만 그대로 둠.
    base = (country or "").split()[0] if country else ""      # '미국 캘리포니아' → '미국'
    try:
        ko = quake_place_ko(base, q.get("lat"), q.get("lon"), region) if base else ""
    except Exception:
        ko = ""
    if ko:
        line2 = f"{ko} · 규모 {mag:.1f}"
    elif country and region:
        line2 = f"{region} · 규모 {mag:.1f}"
    else:
        line2 = f"규모 {mag:.1f}"

    # 알림 끝 좌표 한 줄(지역 필터용 · 카톡엔 안 보임)
    tag = f"\n{geo_tag(q['lat'], q['lon'])}" if q.get("lat") is not None else ""

    if mag >= 6.0:   # 🔴 큰 지진 — 규모(실제 데이터)에 맞춰 피해 우려를 명시
        warn = ("규모 7 이상 대지진 — 광범위한 피해가 우려됩니다."
                if mag >= 7.0 else
                "규모 6 이상 강진 — 큰 피해가 우려됩니다.")
        return (
            f"🚨🚨 [{head_loc}] 지진 발생\n"
            f"━━━━━━━━━━━━\n"
            f"{line2}\n"
            f"{warn}\n"
            f"현지 가족 안부 확인해보세요.\n"
            f"출처: USGS · {when}"
        ) + tag
    else:            # 🟡 일반 (4~6)
        return (
            f"🚨 [{head_loc}] 지진 발생\n"
            f"{line2}\n"
            f"현지 가족 안부 확인해보세요.\n"
            f"출처: USGS · {when}"
        ) + tag


def main():
    # 1) 최근 지진 가져오기
    try:
        quakes = get_recent_quakes()
    except Exception as e:
        print(f"⚠️ 지진 정보를 가져오지 못했습니다. ({e})")
        return

    # 2) 이미 알린 지진 제외 (중복 방지)
    seen = load_seen_ids()
    new_quakes = [q for q in quakes if not (set(q.get("ids") or [q["id"]]) & seen)]   # 번호 중 하나라도 본 것이면 이미 알린 지진

    # 3) 결과 표시 (발송용: 큰 지진이 위로 오도록 규모 내림차순)
    if not new_quakes:
        print("🟢 지진: 새로 알릴 소식 없음")
    else:
        new_quakes.sort(key=lambda q: q["mag"], reverse=True)
        blocks = [make_quake_block(q) for q in new_quakes]
        print("\n\n".join(blocks))

    # 4) 이번에 본 지진들을 기록에 추가 (다음 실행 때 중복 방지)
    for q in quakes:
        seen.update(q.get("ids") or [q["id"]])
    save_seen_ids(seen)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
