# -*- coding: utf-8 -*-
"""
다낭_폭우기록.py — (2026-10-06~10 시험) 베트남 다낭·호이안 강수량을 30분마다 재서 '기록만' 남깁니다.

■ 목적 : 우리 폭우 감시가 실제 폭우를 '언제' 잡는지 확인.
■ 카톡은 절대 안 보냅니다. 기존 감시(호우감시.py)·알림·지역필터·AI 판단은 건드리지 않습니다.
   - 호우감시.py 와 '같은 데이터(open-meteo)·같은 기준(30/50/100mm)·같은 계산'을 쓰되,
     결과를 알림 로그가 아니라 별도 파일에만 적습니다.
■ 기록 파일 : 다낭폭우기록.csv (엑셀로 열림)
■ 사용법
     py 다낭_폭우기록.py        ← 지금 한 번 재서 기록 (자동 실행이 30분마다 이걸 실행)
     py 다낭_폭우기록.py 표     ← '처음 감지한 시각·그때 강수량' 표 보기
■ 기간 : RECORD_UNTIL 이 지나면 아무것도 안 하고 끝납니다(자동 실행도 그날 끝나게 등록).
"""
import csv
import json
import os
import sys
import time
from datetime import datetime
from urllib.request import Request, urlopen

from 호우감시 import level_for          # 기준(30/50/100mm)은 실제 감시와 똑같이

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE, "다낭폭우기록.csv")
STATE_FILE = os.path.join(BASE, "다낭폭우_state.json")
RECORD_UNTIL = "2026-10-11"             # 이 날짜(한국)까지 기록. 10/10 밤 비가 다음 날 잡히는 것까지 보려고 하루 여유.
TZ = "Asia%2FHo_Chi_Minh"
UA = {"User-Agent": "Mozilla/5.0 (nochijima rain test)"}

# 좌표 출처: open-meteo 지오코딩(GeoNames) — 2026-10-05 조회
POINTS = [("다낭", 16.0678, 108.2208), ("호이안", 15.8794, 108.3350)]

COLS = ["기록시각(한국)", "현지시각", "도시", "감시기준값(mm)", "기준값 날짜", "등급", "기준 넘음",
        "처음 감지", "오늘 하루합계(예보 포함, mm)", "오늘 지금까지 실제(mm)", "최근 1시간(mm)", "어제 합계(mm)", "비고"]


def fetch():
    lats = ",".join(str(p[1]) for p in POINTS)
    lons = ",".join(str(p[2]) for p in POINTS)
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lats}&longitude={lons}"
           f"&daily=precipitation_sum&hourly=precipitation&current=precipitation"
           f"&past_days=1&forecast_days=1&timezone={TZ}")
    last = None
    for _ in range(3):                                  # 서버가 잠깐 안 받을 때 대비
        try:
            with urlopen(Request(url, headers=UA), timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data if isinstance(data, list) else [data]
        except Exception as e:
            last = e
            time.sleep(5)
    raise last


def measure(loc):
    """한 도시 → 감시와 같은 값 + 참고값."""
    daily = loc.get("daily", {})
    days, sums = daily.get("time", []), daily.get("precipitation_sum", [])
    # ★ 호우감시.py 와 같은 계산: 어제·오늘 '하루 합계' 중 큰 값 (오늘 값은 남은 시간 예보가 포함됨)
    best_mm, best_day = -1.0, ""
    for day, s in zip(days, sums):
        if s is not None and s > best_mm:
            best_mm, best_day = s, day
    today = days[-1] if days else ""
    today_sum = sums[-1] if sums else None
    yday_sum = sums[0] if len(sums) > 1 else None
    now_local = loc.get("current", {}).get("time", "")           # 예: 2026-10-06T14:30
    hourly = loc.get("hourly", {})
    so_far, last_hour = 0.0, None
    for t, v in zip(hourly.get("time", []), hourly.get("precipitation", [])):
        if v is None or not t.startswith(today):
            continue
        if t <= now_local:                                       # 이미 지난 시간 = 실제(관측·분석) 값
            so_far += v
            last_hour = v
    return {"mm": best_mm, "day": best_day, "today_sum": today_sum, "yday_sum": yday_sum,
            "so_far": round(so_far, 1), "last_hour": last_hour, "local": now_local.replace("T", " ")}


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": []}


def append_rows(rows):
    new = not os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", encoding="utf-8-sig", newline="") as f:     # utf-8-sig = 엑셀에서 한글 안 깨짐
        w = csv.writer(f)
        if new:
            w.writerow(COLS)
        w.writerows(rows)


def record():
    now = datetime.now()
    if now.strftime("%Y-%m-%d") > RECORD_UNTIL:
        return                                                    # 시험 기간 끝 → 아무것도 안 함
    stamp = now.strftime("%Y-%m-%d %H:%M")
    try:
        locs = fetch()
    except Exception as e:
        append_rows([[stamp, "", name, "", "", "", "", "", "", "", "", "", f"조회 실패({type(e).__name__})"] for name, _, _ in POINTS])
        return
    state = load_state()
    seen = set(state.get("seen", []))
    rows = []
    for (name, _, _), loc in zip(POINTS, locs):
        m = measure(loc)
        lv, word = level_for(m["mm"])
        first = ""
        if lv:
            uid = f"{name}-{m['day']}-{lv}"                       # 호우감시와 같은 방식: 도시+날짜+등급당 한 번
            if uid not in seen:
                seen.add(uid)
                first = "★처음"
        rows.append([stamp, m["local"], name, m["mm"], m["day"], word or "기준 미만", "예" if lv else "아니오", first,
                     m["today_sum"], m["so_far"], m["last_hour"], m["yday_sum"], ""])
    append_rows(rows)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"seen": sorted(seen)}, f, ensure_ascii=False, indent=1)


def table():
    if not os.path.exists(CSV_FILE):
        print("아직 기록이 없습니다.")
        return
    with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    ok = [r for r in rows if not r["비고"]]
    print(f"기록 {len(rows)}줄 (조회 실패 {len(rows) - len(ok)}줄) · {rows[0]['기록시각(한국)']} ~ {rows[-1]['기록시각(한국)']}\n")
    firsts = [r for r in rows if r["처음 감지"]]
    print("## 기준을 처음 넘은 순간")
    if not firsts:
        print("아직 기준(하루 30mm)을 넘은 적이 없습니다.")
    else:
        print("| 도시 | 처음 감지(한국시각) | 현지시각 | 등급 | 그때 감시기준값 | 어느 날 비 | 그때까지 실제로 온 비 | 최근 1시간 |")
        print("|---|---|---|---|---|---|---|---|")
        for r in firsts:
            print(f"| {r['도시']} | {r['기록시각(한국)']} | {r['현지시각']} | {r['등급']} | {r['감시기준값(mm)']}mm | {r['기준값 날짜']} "
                  f"| {r['오늘 지금까지 실제(mm)']}mm | {r['최근 1시간(mm)']}mm |")
    print("\n## 날짜별 최고값 (현지 날짜 기준)")
    print("| 도시 | 현지 날짜 | 감시기준값 최고 | 실제 누적 최고 | 1시간 최고 |")
    print("|---|---|---|---|---|")
    best = {}
    for r in ok:
        k = (r["도시"], r["현지시각"][:10])
        b = best.setdefault(k, [0.0, 0.0, 0.0])
        for i, c in enumerate(("오늘 하루합계(예보 포함, mm)", "오늘 지금까지 실제(mm)", "최근 1시간(mm)")):
            try:
                b[i] = max(b[i], float(r[c]))
            except Exception:
                pass
    for (city, day), b in sorted(best.items(), key=lambda x: (x[0][1], x[0][0])):
        print(f"| {city} | {day} | {b[0]}mm | {b[1]}mm | {b[2]}mm |")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "표":
        table()
    else:
        record()
