# -*- coding: utf-8 -*-
"""
호우감시.py — open-meteo(최신 강수 데이터)로 '폭우'를 직접 잡습니다.

왜 필요한가:
- 일본 기상청(JMA) 특보가 지연·정지될 때가 있어(예: 토야마 8/27 104mm 폭우를
  JMA 데이터가 오래돼서 놓침), '실제 강수량(mm)'으로 이중 감시합니다.
- 하루 강수량 기준: 100mm↑ 극심한 폭우 / 50mm↑ 호우 / 30mm↑ 비 많음
- 일본은 지역 폭우까지 잡게 여러 도시(도야마 등) 촘촘히, 그 외 구독 나라는 대표 도시.
- 소스: open-meteo (무료 · API키 불필요 · 최신)
- 이미 알린 것은 rain_seen.json 에 기록해 다시 알리지 않음(도시+날짜+등급).
- (2026-10-11) 예보와 실제를 구분: 실제로 온 비가 기준을 넘어야 "폭우/호우" 알림.
  오늘 예보만 기준을 넘으면 "많은 비 예보" 알림(하루 한 번, 한국 23~07시에는 보류했다가 07시 이후).
  다낭 시험(10/5~11)에서 예보만 믿고 울린 헛알림(10/7 예보 77mm → 실제 25mm)이 있어서 바꿈.
- py 호우감시.py 미리  ← 화면에만 보고, 알린 기록은 남기지 않음
"""

import json
import os
import sys
from datetime import datetime
from urllib.request import Request, urlopen

from 나라정보 import COUNTRY_INFO, local_kst_str
from 지역필터 import geo_tag, extra_rain_points, haversine_km   # 좌표 한 줄 · 내가 고른 도시

try:
    from 카톡발송 import MY_COUNTRIES
except Exception:
    MY_COUNTRIES = ["일본", "미국", "말레이시아"]

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rain_seen.json")

# 전 세계 30개국 주요 도시 (지역 폭우까지 잡게 나라마다 여러 도시).
#  일본만이 아니라 모든 나라를 똑같이 촘촘하게 감시합니다.
WORLD_CITIES = {
    "일본": [("도쿄", 35.68, 139.69), ("오사카", 34.69, 135.50), ("후쿠오카", 33.59, 130.40),
           ("삿포로", 43.06, 141.35), ("나고야", 35.18, 136.91), ("센다이", 38.27, 140.87),
           ("히로시마", 34.39, 132.46), ("도야마", 36.70, 137.21), ("가나자와", 36.56, 136.66),
           ("니가타", 37.90, 139.02), ("나가노", 36.65, 138.18), ("가고시마", 31.60, 130.56),
           ("나하", 26.21, 127.68), ("다카마쓰", 34.34, 134.05), ("아오모리", 40.82, 140.74)],
    "베트남": [("하노이", 21.03, 105.85), ("호치민", 10.82, 106.63), ("다낭", 16.05, 108.22),
            ("하이퐁", 20.86, 106.68), ("껀터", 10.03, 105.78)],
    "대만": [("타이베이", 25.03, 121.57), ("가오슝", 22.63, 120.30), ("타이중", 24.15, 120.68),
           ("타이난", 22.99, 120.21), ("화롄", 23.98, 121.60)],
    "태국": [("방콕", 13.75, 100.50), ("치앙마이", 18.79, 98.98), ("푸껫", 7.88, 98.39),
           ("나콘랏차시마", 14.97, 102.10), ("핫야이", 7.01, 100.47)],
    "필리핀": [("마닐라", 14.60, 120.98), ("세부", 10.32, 123.90), ("다바오", 7.19, 125.46),
            ("바기오", 16.41, 120.60), ("일로일로", 10.72, 122.56)],
    "말레이시아": [("쿠알라룸푸르", 3.14, 101.69), ("조호바루", 1.49, 103.74), ("페낭", 5.41, 100.33),
              ("코타키나발루", 5.98, 116.07), ("쿠칭", 1.55, 110.35), ("이포", 4.60, 101.07)],
    "싱가포르": [("싱가포르", 1.35, 103.82), ("주롱", 1.33, 103.74)],
    "인도네시아": [("자카르타", -6.21, 106.85), ("수라바야", -7.25, 112.75), ("반둥", -6.91, 107.61),
              ("메단", 3.59, 98.67), ("마카사르", -5.15, 119.43), ("덴파사르", -8.65, 115.22)],
    "미국": [("뉴욕", 40.71, -74.01), ("로스앤젤레스", 34.05, -118.24), ("시카고", 41.88, -87.63),
           ("휴스턴", 29.76, -95.37), ("마이애미", 25.76, -80.19), ("시애틀", 47.61, -122.33),
           ("덴버", 39.74, -104.99), ("뉴올리언스", 29.95, -90.07)],
    "캐나다": [("토론토", 43.65, -79.38), ("밴쿠버", 49.28, -123.12), ("몬트리올", 45.50, -73.57),
            ("캘거리", 51.05, -114.07), ("오타와", 45.42, -75.70)],
    "호주": [("시드니", -33.87, 151.21), ("멜버른", -37.81, 144.96), ("브리즈번", -27.47, 153.03),
           ("퍼스", -31.95, 115.86), ("애들레이드", -34.93, 138.60), ("다윈", -12.46, 130.84)],
    "영국": [("런던", 51.51, -0.13), ("맨체스터", 53.48, -2.24), ("버밍엄", 52.49, -1.89),
           ("글래스고", 55.86, -4.25), ("카디프", 51.48, -3.18), ("벨파스트", 54.60, -5.93)],
    "독일": [("베를린", 52.52, 13.40), ("뮌헨", 48.14, 11.58), ("함부르크", 53.55, 9.99),
           ("쾰른", 50.94, 6.96), ("프랑크푸르트", 50.11, 8.68)],
    "프랑스": [("파리", 48.85, 2.35), ("마르세유", 43.30, 5.37), ("리옹", 45.76, 4.84),
            ("툴루즈", 43.60, 1.44), ("니스", 43.70, 7.27), ("보르도", 44.84, -0.58)],
    "인도": [("뉴델리", 28.61, 77.21), ("뭄바이", 19.08, 72.88), ("벵갈루루", 12.97, 77.59),
           ("콜카타", 22.57, 88.36), ("첸나이", 13.08, 80.27), ("하이데라바드", 17.38, 78.49)],
    "이탈리아": [("로마", 41.90, 12.50), ("밀라노", 45.46, 9.19), ("나폴리", 40.85, 14.27),
             ("토리노", 45.07, 7.69), ("팔레르모", 38.12, 13.36), ("베네치아", 45.44, 12.32)],
    "스페인": [("마드리드", 40.42, -3.70), ("바르셀로나", 41.39, 2.17), ("발렌시아", 39.47, -0.38),
            ("세비야", 37.39, -5.99), ("빌바오", 43.26, -2.93), ("말라가", 36.72, -4.42)],
    "네덜란드": [("암스테르담", 52.37, 4.90), ("로테르담", 51.92, 4.48), ("헤이그", 52.08, 4.31),
             ("위트레흐트", 52.09, 5.12)],
    "스위스": [("취리히", 47.38, 8.54), ("제네바", 46.20, 6.14), ("바젤", 47.56, 7.59),
            ("베른", 46.95, 7.45), ("루가노", 46.00, 8.95)],
    "브라질": [("상파울루", -23.55, -46.63), ("리우데자네이루", -22.91, -43.17), ("브라질리아", -15.79, -47.88),
            ("살바도르", -12.97, -38.51), ("마나우스", -3.12, -60.02), ("포르투알레그리", -30.03, -51.23)],
    "멕시코": [("멕시코시티", 19.43, -99.13), ("과달라하라", 20.67, -103.35), ("몬테레이", 25.69, -100.32),
            ("칸쿤", 21.16, -86.85), ("티후아나", 32.51, -117.04)],
    "뉴질랜드": [("오클랜드", -36.85, 174.76), ("웰링턴", -41.29, 174.78), ("크라이스트처치", -43.53, 172.64),
             ("더니든", -45.87, 170.50)],
    "러시아": [("모스크바", 55.76, 37.62), ("상트페테르부르크", 59.94, 30.31), ("노보시비르스크", 55.01, 82.93),
            ("블라디보스토크", 43.12, 131.89), ("소치", 43.60, 39.73)],
    "스웨덴": [("스톡홀름", 59.33, 18.07), ("예테보리", 57.71, 11.97), ("말뫼", 55.60, 13.00),
            ("우메오", 63.83, 20.26)],
    "튀르키예": [("이스탄불", 41.01, 28.98), ("앙카라", 39.93, 32.87), ("이즈미르", 38.42, 27.14),
             ("안탈리아", 36.90, 30.71), ("부르사", 40.19, 29.06)],
    "포르투갈": [("리스본", 38.72, -9.14), ("포르투", 41.15, -8.61), ("파루", 37.02, -7.93),
             ("코임브라", 40.21, -8.43)],
    "폴란드": [("바르샤바", 52.23, 21.01), ("크라쿠프", 50.06, 19.94), ("브로츠와프", 51.11, 17.04),
            ("그단스크", 54.35, 18.65), ("포즈난", 52.41, 16.93)],
    "오스트리아": [("빈", 48.21, 16.37), ("그라츠", 47.07, 15.44), ("잘츠부르크", 47.81, 13.05),
              ("인스브루크", 47.27, 11.39)],
    "벨기에": [("브뤼셀", 50.85, 4.35), ("안트베르펜", 51.22, 4.40), ("겐트", 51.05, 3.72),
            ("리에주", 50.63, 5.57)],
    "아르헨티나": [("부에노스아이레스", -34.60, -58.38), ("코르도바", -31.42, -64.18), ("로사리오", -32.95, -60.64),
              ("멘도사", -32.89, -68.84), ("바릴로체", -41.13, -71.31)],
}

# 하루 강수량(mm) → (등급, 한국어)
def level_for(mm):
    if mm >= 100:
        return "red", "극심한 폭우"
    if mm >= 50:
        return "orange", "호우"
    if mm >= 30:
        return "yellow", "비 많음"
    return None, None


def _get(url):
    with urlopen(Request(url, headers=UA), timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_points(points, tz):
    """points=[(name,lat,lon,country)] 를 한 번에 조회해 도시별 관측값(dict) 목록.
       today_total  = 오늘 하루 합계(남은 시간 '예보' 포함)
       today_actual = 오늘 지금까지 실제로 온 비(시간별 값 중 이미 지난 시간만 더함)
       yday_sum     = 어제 하루 합계(끝난 하루 = 실제)"""
    if not points:
        return []
    lats = ",".join(str(p[1]) for p in points)
    lons = ",".join(str(p[2]) for p in points)
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lats}&longitude={lons}"
           f"&daily=precipitation_sum&hourly=precipitation,weather_code&current=precipitation,weather_code"
           f"&past_days=1&forecast_days=1&timezone={tz}")
    data = _get(url)
    locs = data if isinstance(data, list) else [data]
    out = []
    for p, loc in zip(points, locs):
        daily = loc.get("daily", {})
        days = daily.get("time", [])
        sums = daily.get("precipitation_sum", [])
        today = days[-1] if days else ""
        yday = days[0] if len(days) > 1 else ""
        now_local = loc.get("current", {}).get("time", "")           # 예: 2026-10-06T14:30
        h = loc.get("hourly", {})
        so_far = 0.0
        for t, v in zip(h.get("time", []), h.get("precipitation", [])):
            if v is not None and today and t.startswith(today) and t <= now_local:
                so_far += v
        # 우박 감지: 날씨코드 96·99(뇌우+우박)가 '지금' 또는 '오늘' 있으면
        hail_day = ""
        if loc.get("current", {}).get("weather_code") in (96, 99):
            hail_day = today
        else:
            for t, code in zip(h.get("time", []), h.get("weather_code", [])):
                if code in (96, 99) and t[:10] == today:
                    hail_day = today
                    break
        out.append({"city": p[0], "country": p[3], "lat": p[1], "lon": p[2],
                    "today": today, "yday": yday,
                    "today_total": (sums[-1] if sums else None) or 0.0,
                    "today_actual": round(so_far, 1),
                    "yday_sum": (sums[0] if len(sums) > 1 else None) or 0.0,
                    "hail_day": hail_day})
    return out


def load_state():
    """(이미 알린 것, 밤사이 보류 중인 예보) 읽기."""
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return set(d.get("seen", [])), dict(d.get("pending", {}))
    except Exception:
        return set(), {}


def save_state(seen, pending):
    ids = sorted(seen, key=lambda u: u.rsplit("-", 4)[-4:])[-2000:] if len(seen) > 2000 else list(seen)
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "seen": ids, "pending": pending},
                  f, ensure_ascii=False, indent=2)


def make_block(country, city, mm, day, lat=None, lon=None):
    """폭우 1건 → 발송용 블록. (끝에 좌표 한 줄 — 지역 필터용, 카톡엔 안 보임)"""
    tag = f"\n{geo_tag(lat, lon)}" if lat is not None else ""
    return _rain_body(country, city, mm, day) + tag


def _rain_body(country, city, mm, day):
    lv, word = level_for(mm)
    # 날짜를 '8월 27일'로
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
        when = f"{d.month}월 {d.day}일"
    except Exception:
        when = day
    head = f"{country} {city}"
    if lv == "red":
        # 200mm↑는 기록적 폭우로 한 단계 더 강조(실제 강수량 기반)
        extra = "⚠️ 하루 200mm 넘는 기록적 폭우입니다.\n" if mm >= 200 else ""
        return (
            f"🚨🚨 [{head}] {word} (하루 {mm:.0f}mm)\n"
            f"━━━━━━━━━━━━\n"
            f"{extra}"
            f"침수·산사태 위험 — 현지 안전 꼭 확인해보세요.\n"
            f"출처: open-meteo · {when}"
        )
    if lv == "orange":
        return (
            f"🚨 [{head}] {word} (하루 {mm:.0f}mm)\n"
            f"현지 침수·외출 주의하세요.\n"
            f"출처: open-meteo · {when}"
        )
    return (   # yellow → 브리핑용(🌧️)
        f"🌧️ [{head}] {word} (하루 {mm:.0f}mm)\n"
        f"비가 많이 와요. 외출·일정 참고하세요.\n"
        f"출처: open-meteo · {when}"
    )


def make_hail_block(country, city, day, lat=None, lon=None):
    """우박(뇌우+우박) 1건 → 발송용 블록."""
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
        when = f"{d.month}월 {d.day}일"
    except Exception:
        when = day
    tag = f"\n{geo_tag(lat, lon)}" if lat is not None else ""
    return (
        f"🚨 [{country} {city}] 우박 예보 (뇌우 동반)\n"
        f"차량·유리창 파손 주의, 야외활동 자제하세요.\n"
        f"출처: open-meteo · {when}"
    ) + tag


def build_points():
    """구독 나라별 조회 지점을 '나라 시간대'별로 묶어 돌려줍니다. {tz: [(city,lat,lon,country)]}
    모든 나라를 똑같이 여러 도시로 감시(목록 없으면 대표 도시 1개)."""
    groups = {}
    for country in MY_COUNTRIES:
        info = COUNTRY_INFO.get(country)
        tz = info[4] if info else "UTC"
        cities = WORLD_CITIES.get(country)
        if not cities and info:
            cities = [(info[1], info[2], info[3])]   # 목록에 없으면 대표 도시만
        for name, la, lo in (cities or []):
            groups.setdefault(tz, []).append((name, la, lo, country))
    # 내가 고른 도시(지역설정.json)가 목록에 없으면 추가 → 그 도시 비를 직접 감시
    for name, la, lo, country in extra_rain_points(MY_COUNTRIES):
        info = COUNTRY_INFO.get(country)
        tz = info[4] if info else "UTC"
        pts = groups.setdefault(tz, [])
        if not any(p[3] == country and haversine_km(la, lo, p[1], p[2]) <= 30 for p in pts):
            pts.append((name, la, lo, country))
    return groups


def _when(day):
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
        return f"{d.month}월 {d.day}일"
    except Exception:
        return day


def make_forecast_block(country, city, mm, day, lat=None, lon=None):
    """'폭우 예보' 1건 — 아직 실제로는 기준만큼 안 왔고, 오늘 예보만 기준을 넘은 경우.
       (안부 문구는 붙지 않음 — 카톡발송.is_disaster_block 이 '비 예보'를 재난에서 뺌)"""
    tag = f"\n{geo_tag(lat, lon)}" if lat is not None else ""
    return (
        f"🌧️ [{country} {city}] 오늘 많은 비 예보 (예상 약 {mm:.0f}mm)\n"
        f"실제로 많이 내리면 다시 알려드릴게요.\n"
        f"출처: open-meteo 예보 · {_when(day)}"
    ) + tag


LEVELS = ["yellow", "orange", "red"]
QUIET_FROM, QUIET_TO = 23, 7       # 한국 시간 23:00~07:00 에는 '예보' 알림을 내지 않고 보류


def decide(obs_list, seen, pending, now):
    """관측값 → 이번에 낼 알림 [(정렬용 mm, 블록)]. seen·pending 을 직접 고칩니다.
    ● 실제(어제 합계 / 오늘 지금까지)가 기준을 넘으면 → 폭우 알림. 같은 날 같은 도시는 등급이 오를 때만 다시.
    ● 오늘 예보 포함 값만 넘고 실제는 아직 아래면 → '폭우 예보' 알림, 하루 한 번.
      밤(23~07시)에는 내지 않고 보류 → 07시 이후 첫 실행 때, 그때도 예보가 기준 이상이고 실제 알림이 안 나갔으면 냄.
      그 사이 실제가 기준을 넘었으면 예보는 버리고 실제 알림만."""
    quiet = now.hour >= QUIET_FROM or now.hour < QUIET_TO
    out = []
    for o in obs_list:
        base = f"{o['country']}-{o['city']}"
        today = o["today"]
        fuid = f"{base}-{today}-예보"
        # 1) 실제
        for day, mm in ((o["yday"], o["yday_sum"]), (today, o["today_actual"])):
            lv, _ = level_for(mm or 0)
            if not lv or not day:
                continue
            idx = LEVELS.index(lv)
            if any(f"{base}-{day}-{l}" in seen for l in LEVELS[idx:]):
                continue                                   # 같은 등급(또는 더 높은 등급)을 이미 알림
            for l in LEVELS[:idx + 1]:
                seen.add(f"{base}-{day}-{l}")
            pending.pop(f"{base}-{day}-예보", None)        # 보류 중이던 예보는 버림
            out.append((mm, make_block(o["country"], o["city"], mm, day, o["lat"], o["lon"])))
        # 2) 예보
        for key in [k for k in pending if k.startswith(base + "-") and k != fuid]:
            pending.pop(key)                               # 지난 날짜의 보류는 정리
        actual_sent = any(f"{base}-{today}-{l}" in seen for l in LEVELS)
        lv_f, _ = level_for(o["today_total"] or 0)
        if today and lv_f and not actual_sent and fuid not in seen:
            if quiet:
                pending.setdefault(fuid, now.strftime("%Y-%m-%d %H:%M"))
            else:
                seen.add(fuid)
                pending.pop(fuid, None)
                out.append((o["today_total"], make_forecast_block(o["country"], o["city"], o["today_total"], today, o["lat"], o["lon"])))
        elif fuid in pending:
            pending.pop(fuid)                              # 예보가 기준 아래로 내려갔거나 실제 알림이 나감
        # 3) 우박 (뇌우+우박). 도시·날짜당 한 번만.
        if o.get("hail_day"):
            huid = f"{base}-{o['hail_day']}-우박"
            if huid not in seen:
                seen.add(huid)
                out.append((500, make_hail_block(o["country"], o["city"], o["hail_day"], o["lat"], o["lon"])))
    return out


def main():
    preview = len(sys.argv) > 1 and sys.argv[1] == "미리"   # 미리 = 화면에만 보고, 알린 기록을 남기지 않음
    groups = build_points()
    results = []
    try:
        for tz, pts in groups.items():
            results += fetch_points(pts, tz)
    except Exception as e:
        print(f"⚠️ 강수 정보를 가져오지 못했습니다. ({e})")
        return

    seen, pending = load_state()
    new_blocks = decide(results, seen, pending, datetime.now())

    if new_blocks:
        new_blocks.sort(key=lambda x: -x[0])   # 많은 비부터
        print("\n\n".join(b for _, b in new_blocks))
    else:
        print("🟢 호우: 새 폭우 소식 없음")
    if pending:
        print(f"\n🟢 폭우 예보 {len(pending)}건 보류 중(밤 23~07시에는 보내지 않음): " + ", ".join(sorted(pending)))

    if not preview:
        save_state(seen, pending)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
