# -*- coding: utf-8 -*-
"""
지역필터_테스트.py — 지역 필터가 '최신 실제 데이터'로 제대로 거르는지 확인(발송·기록 안 건드림).

  py 지역필터_테스트.py

- 감시 프로그램의 '이미 본 것' 기록(seen 파일)은 절대 바꾸지 않습니다.
- USGS(최근 7일)·GDACS(현재)·open-meteo(현재)·미국 기상청(현재)을 새로 받아와
  "필터 전(나라만) → 필터 후(내 지역)"를 보여줍니다.
"""
import datetime
import json
import sys
from urllib.request import Request, urlopen

import 지역필터 as rf
import 지진감시 as eq
import 재난감시 as dz
import 호우감시 as rain
import 미국기상청감시 as nws
from 카톡발송 import MY_COUNTRIES, passes_filter, to_kakao_text

UA = {"User-Agent": "Mozilla/5.0"}
CFG = rf.load_config()


def show(title, blocks, prefix, limit=8):
    ok = [b for b in blocks if rf.block_passes(b, prefix, CFG)]
    no = [b for b in blocks if b not in ok]
    print(f"\n===== {title} : 나라 기준 {len(blocks)}건 → 내 지역 {len(ok)}건 (걸러짐 {len(no)}건) =====")
    for b in ok[:limit]:
        print("  ✅ 옴   ", b.splitlines()[0][:60], _dist(b))
    for b in no[:limit]:
        print("  ❌ 걸러짐", b.splitlines()[0][:60], _dist(b))


def _dist(b):
    lat, lon = rf.parse_geo(b)
    c = rf.country_of(b)
    rules = rf.rules_for(c, CFG) or []
    for r in rules:
        if r["type"] == "city" and lat is not None:
            return f"({r['name']}에서 {rf.haversine_km(r['lat'], r['lon'], lat, lon):.0f}km)"
    return ""


def main():
    print("설정:", "켜짐" if CFG.get("enabled") else "꺼짐", "| 받는 나라:", "·".join(MY_COUNTRIES))
    for c in MY_COUNTRIES:
        print(f"  - {c}: {rf.region_label(c, CFG)}")
        for r in rf.rules_for(c, CFG) or []:
            print("      →", {k: r[k] for k in r if k in ("type", "name", "lat", "lon", "admin1", "abbr")})

    # 1) 지진 — USGS 최근 7일 (seen 기록 안 씀)
    st = (datetime.datetime.utcnow() - datetime.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    url = eq.API_URL.format(mag=eq.MIN_MAGNITUDE, start=st)
    data = json.loads(urlopen(Request(url, headers=UA), timeout=30).read().decode("utf-8"))
    qblocks = []
    for f in data.get("features", []):
        p = f["properties"]; g = f["geometry"]["coordinates"]
        if p.get("mag") is None or p["mag"] < eq.min_mag_for(p.get("place", "")):
            continue
        q = {"id": f["id"], "mag": p["mag"], "place": p.get("place", ""), "time": p.get("time", 0),
             "lat": g[1], "lon": g[0]}
        b = eq.make_quake_block(q)
        if passes_filter(b):
            qblocks.append(b)
    show("지진 (최근 7일)", qblocks, "지진감시")

    # 2) 재난 — GDACS 현재
    dblocks = [b for b in (dz.make_block(d) for d in dz.fetch_disasters()) if passes_filter(b)]
    show("재난 (GDACS 현재)", dblocks, "재난감시")

    # 3) 폭우 — 감시 도시가 내 지역에 드는지 (가상 120mm로 모든 도시 점검)
    groups = rain.build_points()
    pts = [p for g in groups.values() for p in g]
    rblocks = [rain.make_block(p[3], p[0], 120, datetime.date.today().isoformat(), p[1], p[2]) for p in pts]
    show("폭우 (감시 도시 전체를 가상 120mm로 점검)", rblocks, "호우감시", limit=30)

    # 4) 미국 기상청 — 현재 특보
    alerts = nws.fetch_alerts()
    allowed = rf.us_allowed_states(CFG)
    from collections import Counter
    print(f"\n===== 미국 기상청 (현재) : 전체 {len(alerts)}건 → 내 주 {allowed} "
          f"{sum(1 for a in alerts if allowed is None or a.get('st') in allowed)}건 =====")
    print("  주별:", dict(Counter(a.get("st") or "연안/기타" for a in alerts).most_common(10)))

    # 5) 카톡 문구에 좌표 줄이 안 보이는지
    sample = qblocks[0] if qblocks else rblocks[0]
    txt = to_kakao_text(sample)
    print("\n===== 카톡에 보이는 모양 (좌표 줄 숨김 확인) =====")
    print(txt)
    print("→ 좌표 줄 노출:", "있음 ❌" if rf.GEO_PREFIX in txt else "없음 ✅")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
