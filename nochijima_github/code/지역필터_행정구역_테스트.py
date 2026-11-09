# -*- coding: utf-8 -*-
"""
지역필터_행정구역_테스트.py — 주·도·현(홋카이도·오키나와·페낭·발리·캘리포니아 등)을 골랐을 때
지역 전체가 빠짐없이 걸리고, 바깥은 걸러지는지 확인. (발송·설정·기록 안 건드림)

  py 지역필터_행정구역_테스트.py
"""
import json
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

import 지역필터 as rf

CFG = {"enabled": True, "regions": {
    "일본": ["홋카이도", "오키나와현"],
    "말레이시아": ["페낭"],
    "인도네시아": ["발리"],
    "미국": ["캘리포니아주(전역)", "하와이"],
}}
OK = BAD = 0


def blk(country, place, lat=None, lon=None, kind="폭우", mag=None, iso=None):
    head = {"폭우": f"🚨🚨 [{country} {place}] 극심한 폭우 (하루 120mm)",
            "지진": f"🚨 [{country}] 지진 발생",
            "재난": f"🚨 [{country}] 홍수 경보 🟠 중간규모",
            "태풍": f"🚨 [{country}] 태풍 경보 🔴 대규모"}[kind]
    body = f"{place} 부근" + (f" · 규모 {mag:.1f}" if mag else "")
    tail = (f"\n{rf.geo_tag(lat, lon)}" if lat is not None else "") + (f"\n{rf.iso_tag(iso)}" if iso else "")
    return f"{head}\n{body}\n출처: 테스트{tail}"


def check(label, block, prefix, expect):
    global OK, BAD
    got = rf.block_passes(block, prefix, CFG)
    good = got == expect
    OK += good
    BAD += not good
    print(f"| {label} | {'옴' if expect else '걸러짐'} | {'옴' if got else '걸러짐'} | {'✅' if good else '❌'} |")


def main():
    print("## 설정이 어떻게 해석됐나")
    for c in CFG["regions"]:
        for r in rf.rules_for(c, CFG) or []:
            extra = f"마을 {len(r.get('points', []))}점 · 폭우감시 {[x[0] for x in r.get('rain', [])]}" if r["type"] == "admin" \
                else f"({r['lat']},{r['lon']}) 인구 {r.get('population')}"
            print(f"- {c}: {r['type']} {r['name']} {r.get('iso','')} {extra}")

    # ── 1. 홋카이도: 모든 마을 + 섬 + 실제 과거 지진 ──
    hk = next(a for a in rf.admins("일본") if a["iso"] == "JP-01")
    miss = [p for p in hk["points"] if not rf.block_passes(blk("일본", "홋카이도 마을", p[0], p[1]), "호우감시", CFG)]
    print(f"\n## 홋카이도 마을 {len(hk['points'])}곳 전부 폭우(30km) 검사 → 빠진 곳 {len(miss)}곳 {'✅' if not miss else '❌ ' + str(miss[:5])}")
    global OK, BAD
    OK += not miss
    BAD += bool(miss)

    st = (datetime.utcnow() - timedelta(days=365 * 5)).strftime("%Y-%m-%d")
    url = ("https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&minmagnitude=4.5"
           f"&starttime={st}&minlatitude=41.3&maxlatitude=45.6&minlongitude=139.3&maxlongitude=145.9")
    feats = json.loads(urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read())["features"]
    def dist(f):
        lon, lat = f["geometry"]["coordinates"][:2]
        return min(rf.haversine_km(lat, lon, p[0], p[1]) for p in hk["points"])

    def passed(f):
        lon, lat = f["geometry"]["coordinates"][:2]
        return rf.block_passes(blk("일본", "홋카이도", lat, lon, "지진", f["properties"]["mag"]), "지진감시", CFG)
    land = [f for f in feats if dist(f) <= 30]            # 홋카이도 육지·연안(마을 30km 안)
    sea = [f for f in feats if dist(f) > 30]
    lmiss = [f["properties"]["place"] for f in land if not passed(f)]
    wrong = [f["properties"]["place"] for f in sea if passed(f) != (dist(f) <= rf.radius_km("지진", f["properties"]["mag"]))]
    print(f"## 최근 5년 홋카이도 육지·연안 실제 지진(규모 4.5↑) {len(land)}건 → 빠진 것 {len(lmiss)}건 {'✅' if not lmiss else '❌ ' + str(lmiss[:5])}")
    print(f"## 먼바다 지진 {len(sea)}건 → 반경 규칙(규모5↓ 100km·6↑ 300km·7↑ 500km)대로 판정 {'✅' if not wrong else '❌ ' + str(wrong[:5])}"
          f" (통과 {sum(passed(f) for f in sea)} · 걸러짐 {sum(not passed(f) for f in sea)})")
    OK += (not lmiss) + (not wrong)
    BAD += bool(lmiss) + bool(wrong)

    print("\n| 경우 | 기대 | 결과 | 맞음 |\n|---|---|---|---|")
    J = "일본"
    check("홋카이도 삿포로 폭우", blk(J, "삿포로", 43.0621, 141.3544), "호우감시", True)
    check("홋카이도 끝 네무로 폭우", blk(J, "네무로", 43.3301, 145.5828), "호우감시", True)
    check("홋카이도 섬 리시리 폭우", blk(J, "리시리", 45.1807, 141.2424), "호우감시", True)
    check("홋카이도 섬 오쿠시리 폭우", blk(J, "오쿠시리", 42.1722, 139.5133), "호우감시", True)
    check("홋카이도 산속 다이세쓰산 폭우", blk(J, "다이세쓰산", 43.6633, 142.8544), "호우감시", True)
    check("아오모리(홋카이도 밖) 폭우", blk(J, "아오모리", 40.8244, 140.74), "호우감시", False)
    check("도쿄 폭우(설정 안 함)", blk(J, "도쿄", 35.6895, 139.6917), "호우감시", False)
    check("홋카이도 앞바다 규모5 지진(130km)", blk(J, "쿠릴 해구", 42.2, 146.8, "지진", 5.0), "지진감시", False)
    check("같은 곳 규모7.2 지진", blk(J, "쿠릴 해구", 42.2, 146.8, "지진", 7.2), "지진감시", True)
    check("오키나와 나하 폭우", blk(J, "나하", 26.2124, 127.6809), "호우감시", True)
    check("오키나와 이시가키섬 폭우", blk(J, "이시가키", 24.3448, 124.1572), "호우감시", True)
    check("오키나와 요나구니섬 폭우", blk(J, "요나구니", 24.4671, 123.0044), "호우감시", True)
    check("오키나와 미나미다이토섬 폭우", blk(J, "미나미다이토", 25.8286, 131.2317), "호우감시", True)
    check("아마미(가고시마현) 폭우", blk(J, "아마미", 28.3775, 129.4938), "호우감시", False)
    check("기상청 특보 '다이토섬'(JP-47)", blk(J, "다이토섬", iso="JP-47"), "일본기상청감시", True)
    check("기상청 특보 '홋카이도 소야'(JP-01)", blk(J, "홋카이도 소야", iso="JP-01"), "일본기상청감시", True)
    check("기상청 특보 '가고시마 아마미'(JP-46)", blk(J, "가고시마 아마미", iso="JP-46"), "일본기상청감시", False)
    M = "말레이시아"
    check("페낭 조지타운 폭우", blk(M, "조지타운", 5.4141, 100.3288), "호우감시", True)
    check("페낭 버터워스(본토 쪽) 폭우", blk(M, "버터워스", 5.3991, 100.3638), "호우감시", True)
    check("쿠알라룸푸르 폭우", blk(M, "쿠알라룸푸르", 3.1412, 101.6865), "호우감시", False)
    check("크다주 홍수(80km, 재난 300km)", blk(M, "알로르스타르", 6.1248, 100.3678, "재난"), "재난감시", True)
    I = "인도네시아"
    check("발리 덴파사르 폭우", blk(I, "덴파사르", -8.65, 115.2167), "호우감시", True)
    check("발리 북쪽 싱아라자 폭우", blk(I, "싱아라자", -8.112, 115.0882), "호우감시", True)
    check("롬복 마타람(발리 밖) 폭우", blk(I, "마타람", -8.5833, 116.1167), "호우감시", False)
    check("수라바야 폭우", blk(I, "수라바야", -7.2492, 112.7508), "호우감시", False)
    check("롬복 규모5 지진(발리에서 100km 안)", blk(I, "롬복", -8.4, 116.3, "지진", 5.0), "지진감시", True)
    U = "미국"
    check("캘리포니아 LA 폭우", blk(U, "로스앤젤레스", 34.0522, -118.2437), "호우감시", True)
    check("캘리포니아 북쪽 끝 크레센트시티 폭우", blk(U, "크레센트시티", 41.7557, -124.2026), "호우감시", True)
    check("네바다 리노 폭우", blk(U, "리노", 39.5296, -119.8138), "호우감시", False)
    check("뉴욕 폭우", blk(U, "뉴욕", 40.7143, -74.006), "호우감시", False)
    check("하와이 힐로 폭우", blk(U, "힐로", 19.7297, -155.09), "호우감시", True)
    check("하와이 카우아이섬 리후에 폭우", blk(U, "리후에", 21.9811, -159.3711), "호우감시", True)
    check("정책 뉴스(지역 필터 대상 아님)", "📋 [일본] 비자 제도 변경\n관련 있다면 미리 확인해보세요.", "정책뉴스감시", True)
    print(f"\n미국 기상청 통과 주: {rf.us_allowed_states(CFG)}")
    print(f"폭우 감시 추가 지점: {[(p[3], p[0]) for p in rf.extra_rain_points(list(CFG['regions']), CFG)]}")
    print(f"\n→ 전체 {OK}개 맞음 / {BAD}개 틀림")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
