# -*- coding: utf-8 -*-
"""
재난 감시 프로그램 (전 세계 · GDACS 유엔+EU 글로벌 재난 경보)
- 감시 대상: 산불 · 홍수 · 태풍 · 화산   (지진은 USGS의 지진감시.py가 담당 → 제외)
- Orange(중대) 이상만 알림. Green(소규모)은 버려서 스팸 방지.
- 각 재난의 나라·종류·시각을 한국어로 정리합니다.
- 이미 알린 재난은 파일에 기록해 다시 알리지 않습니다.
  (등급이 Orange→Red로 올라가면 다시 알립니다.)
- 소스: https://www.gdacs.org/xml/rss.xml  (무료 · API키 불필요)
  ※ 출처 표기: "Global Disaster Alert and Coordination System, GDACS"
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from urllib.request import Request, urlopen

from 나라정보 import local_kst_str, ENGLISH_TO_KR, country_ko   # 현지시각·영어나라명 매핑
from 지역필터 import geo_tag                        # 알림 끝 좌표 한 줄(지역 필터용)

# 우리 30개국의 영어 이름들 (이 나라 관련 재난만 한국어로 번역 → 번역 부담↓)
SUPPORTED_EN = list(ENGLISH_TO_KR.keys())


def _relevant(d):
    """이 재난이 우리 30개국과 관련 있으면 True (번역 대상)."""
    text = (d.get("country", "") + " " + d.get("title", "")).lower()
    return any(e in text for e in SUPPORTED_EN)

# ── 설정 ─────────────────────────────────────────────
GDACS_URL = "https://www.gdacs.org/xml/rss.xml"

# 감시할 재난 종류 (GDACS 코드 → 한국어). 지진(EQ)·가뭄(DR)은 일부러 뺌.
WATCH_TYPES = {
    "WF": "산불",
    "FL": "홍수",
    "TC": "태풍",
    "VO": "화산",
}

# 감시할 등급 — 전 세계 Green 이상 '모두' 수집(지역 재난도 놓치지 않게).
#  ※ 스팸은 카톡발송.py가 MY_COUNTRIES(구독 나라)로 걸러서 방지함.
#     (구독 안 한 나라의 Green 재난은 로그에만 있고 카톡엔 안 감)
ALERT_PASS = {"Green", "Orange", "Red"}

SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "disaster_seen.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _field(block, name):
    """<name>...</name> 내용을 꺼냅니다."""
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.DOTALL)
    if not m:
        return ""
    v = m.group(1)
    v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", v, flags=re.DOTALL)
    return (v.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")).strip()


def translate_to_ko(text, src="en"):
    """영어 등을 한국어로 번역. 실패하면 원문 유지. (간격·저장·차단 감지는 번역도우미가 함)"""
    if not text:
        return ""
    import 번역도우미 as tr
    ko = tr.translate(text, src)
    tr.finish(alert=False)
    return text if ko is None else ko


def to_time_short(pub):
    """GDACS 시각(GMT)을 한국시각 '8월 22일'로."""
    try:
        dt = parsedate_to_datetime(pub).astimezone(timezone(timedelta(hours=9)))
        return f"{dt.month}월 {dt.day}일"
    except Exception:
        return ""


def fetch_disasters():
    """GDACS에서 감시 대상 재난 중 Orange 이상만 가져옵니다."""
    req = Request(GDACS_URL, headers=UA)
    with urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", "replace")

    result = []
    for b in re.findall(r"<item>(.*?)</item>", raw, re.DOTALL):
        etype = _field(b, "gdacs:eventtype")
        alert = _field(b, "gdacs:alertlevel")
        if etype not in WATCH_TYPES or alert not in ALERT_PASS:
            continue
        result.append({
            "id": f"{etype}-{_field(b, 'gdacs:eventid')}-{alert}",  # 등급 오르면 새로 알림
            "etype": etype,
            "alert": alert,
            "country": _field(b, "gdacs:country"),
            "ename": _field(b, "gdacs:eventname"),
            "title": _field(b, "title"),
            "desc": _field(b, "description"),        # 상세(최대풍속·사망자 등)
            "fromdate": _field(b, "gdacs:fromdate"),  # 재난 시작일
            "point": _field(b, "georss:point"),       # "위도 경도" (지역명 변환용)
            "when": to_time_short(_field(b, "pubDate")),
            "pub": _field(b, "pubDate"),   # 원본 시각(현지시각 변환용)
            "link": _field(b, "link"),
        })
    return result


def load_seen():
    if not os.path.exists(SAVE_FILE):
        return set()
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("seen_ids", []))
    except Exception:
        return set()


def save_seen(seen):
    ids = list(seen)[-3000:]   # Green 포함으로 재난 수가 많아져 용량 확대
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "seen_ids": ids},
                  f, ensure_ascii=False, indent=2)


# 등급 → (이모지, 한국어)
LEVEL = {"Red": ("🔴", "대형"), "Orange": ("🟠", "중대"), "Green": ("🟢", "소규모")}


def reverse_geo(lat, lon):
    """좌표 → (한글 '주/지역', 도시). 무료·키불필요. 실패하면 빈 문자열."""
    try:
        u = ("https://api.bigdatacloud.net/data/reverse-geocode-client"
             f"?latitude={lat}&longitude={lon}&localityLanguage=ko")
        with urlopen(Request(u, headers=UA), timeout=12) as r:
            g = json.loads(r.read().decode("utf-8"))
        return (g.get("principalSubdivision") or ""), (g.get("city") or g.get("locality") or "")
    except Exception:
        return "", ""


def ko_country(name):
    """영어 나라명 → 한글. 우리 표(ENGLISH_TO_KR) 우선(즉시·정확), 없으면 구글번역."""
    if not name:
        return name
    kr = country_ko(name)
    return kr if kr else translate_to_ko(name, "en")


def ko_country_table(name):
    """표에 있는 나라만 한국어로(번역기 호출 없음). 없으면 원문 그대로 — 지원 나라 밖 재난용."""
    return country_ko(name) or name


def _point(d):
    """'위도 경도' 문자열 → (위도, 경도) 숫자. 없으면 None."""
    try:
        lat, lon = d.get("point", "").split()
        return float(lat), float(lon)
    except Exception:
        return None


def _wind_kmh(desc):
    """설명에서 최대풍속(km/h)을 뽑습니다. (태풍용)"""
    m = re.search(r"wind speed of (\d+)\s*km/h", desc or "")
    return m.group(1) if m else ""


def _start_day(fromdate):
    """재난 시작일을 '8월 26일 발생'으로."""
    try:
        dt = parsedate_to_datetime(fromdate).astimezone(timezone(timedelta(hours=9)))
        return f"{dt.month}월 {dt.day}일 발생"
    except Exception:
        return ""


def make_block(d):
    """재난 1건을 발송용 메시지 블록으로 만듭니다."""
    type_kr = WATCH_TYPES[d["etype"]]
    relevant = _relevant(d)   # 우리 30개국 관련일 때만 번역·지역조회(부담 줄이기)
    emoji, level_word = LEVEL.get(d["alert"], ("🟠", "중대"))
    multi = "," in d["country"]   # 여러 나라 걸친 재난(태풍 등)

    # 시간대용 대표 나라(첫 나라). 관련이면 한국어로(우리 표 우선).
    first_country = (d["country"].split(",")[0].strip()
                     if d["country"] else (d["ename"] or "해외"))
    tz_country = ko_country(first_country) if relevant else ko_country_table(first_country)

    # 머리글 위치: 관련·단일 나라면 좌표로 '나라 주'까지, 아니면 나라명
    sub, city = "", ""
    if relevant and not multi:
        pt = _point(d)
        if pt:
            sub, city = reverse_geo(*pt)
    if multi:
        # 여러 나라: 각 나라를 한글로 (예: 일본, 중국, 북마리아나)
        head = (", ".join(ko_country(c.strip()) for c in d["country"].split(","))
                if relevant else ", ".join(ko_country_table(c.strip()) for c in d["country"].split(",")))
    else:
        head = f"{tz_country} {sub}".strip()

    # 상세 한 줄 (지진의 '규모'처럼 구체 정보)
    parts = []
    if d["etype"] == "TC":                       # 태풍: 이름·최대풍속
        tc = f"태풍 {d['ename']}" if d.get("ename") else "태풍"
        w = _wind_kmh(d.get("desc", ""))
        if w:
            tc += f" · 최대풍속 {w}km/h"
        parts.append(tc)
    if city:                                     # 도시(있으면)
        parts.append(f"{city} 인근")
    day = _start_day(d.get("fromdate", ""))
    if day:
        parts.append(day)
    detail = " · ".join(parts)
    detail_line = f"{detail}\n" if detail else ""

    # 현지시각 (그 나라 시간대로). 나라를 모르면 한국시각만.
    try:
        when = local_kst_str(tz_country, parsedate_to_datetime(d["pub"]))
    except Exception:
        when = "한국 " + d["when"]

    # 알림 끝 좌표 한 줄(지역 필터용 · 카톡엔 안 보임)
    pt = _point(d)
    tag = f"\n{geo_tag(*pt)}" if pt else ""

    if d["alert"] == "Red":   # 🔴 대형 — GDACS 최고 등급(적색)임을 명시
        return (
            f"🚨🚨 [{head}] {type_kr} 경보 {emoji} {level_word}\n"
            f"━━━━━━━━━━━━\n"
            f"🔴 최고 등급(적색) 경보 — 큰 피해가 우려됩니다.\n"
            f"{detail_line}"
            f"현지 가족 안부 확인해보세요.\n"
            f"출처: GDACS · {when}"
        ) + tag
    else:                     # 🟠 중대 / 🟢 소규모
        return (
            f"🚨 [{head}] {type_kr} 경보 {emoji} {level_word}\n"
            f"{detail_line}"
            f"현지 가족 안부 확인해보세요.\n"
            f"출처: GDACS · {when}"
        ) + tag


def main():
    # 1) 재난 가져오기 (Orange 이상만)
    try:
        disasters = fetch_disasters()
    except Exception as e:
        print(f"⚠️ 재난 정보를 가져오지 못했습니다. ({e})")
        return

    # 2) 이미 알린 것 제외
    seen = load_seen()
    new = [d for d in disasters if d["id"] not in seen]

    # 3) 표시 (Red 먼저)
    if not new:
        print("🟢 재난: 새로 알릴 소식 없음 (Orange 이상)")
    else:
        order = {"Red": 0, "Orange": 1}
        new.sort(key=lambda d: order.get(d["alert"], 9))
        blocks = [make_block(d) for d in new]
        print("\n\n".join(blocks))

    # 4) 기록 저장
    for d in disasters:
        seen.add(d["id"])
    save_seen(seen)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
