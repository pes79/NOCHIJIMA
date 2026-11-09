# -*- coding: utf-8 -*-
"""
일본 기상청(JMA) 폭우·홍수 특보 감시
- GDACS(전세계 큰 재난)는 일본의 '국지성 폭우'를 못 잡습니다.
  그래서 일본 기상청 특보를 직접 읽어 호우·홍수 경보를 잡습니다.
- 대상: 大雨警報(호우경보)·洪水警報(홍수경보)·大雨特別警報(호우 특별경보=최고위험)
  ※ 注意報(주의보)는 너무 자주 떠서 스팸이라 제외. 警報(경보)급부터 알림.
- 소스: 일본 기상청 공식 XML 피드 https://www.data.jma.go.jp/developer/xml/feed/extra.xml (무료 · 키 불필요)
  ※ 2026-10-13 교체. 전에 쓰던 www.jma.go.jp/bosai/warning/data/warning/○○.json 은 2026-05-28(기상청이 경보 체계를
     바꾼 날) 이후 갱신이 멈춘 자료였음 → 다섯 달 동안 옛 자료만 읽고 "새 경보 없음"으로 찍혔음.
- 받은 자료의 발표 시각이 24시간보다 오래되면 "자료가 오래됨" 시스템 알림(하루 1번).
- py 일본기상청감시.py 미리  ← 화면에만 보고, 알린 기록·시스템 알림은 남기지 않음
- 이미 알린 특보는 jma_seen.json에 기록해 다시 알리지 않습니다.
  (같은 날 새 경보가 뜨면 알림, 날짜가 바뀌면 다시 알림)
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

from 나라정보 import local_kst_str
from 지역필터 import iso_tag   # 지역 필터용 숨은 줄(도도부현 코드)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
AREA_URL = "https://www.jma.go.jp/bosai/common/const/area.json"
WARN_URL = "https://www.jma.go.jp/bosai/warning/data/warning/{code}.json"
SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jma_seen.json")

# 감시할 특보 코드 (JMA 공식 코드) → (한국어, 위험도)
#  33=大雨特別警報(최고위험), 03=大雨警報(호우), 04=洪水警報(홍수)
WATCH = {
    "R6": ("위험경보·범람 특별경보(최고위험)", "red"),   # 2026-05 새 체계의 최고 단계(전문 '알림' 칸에 적힘)
    "33": ("호우 특별경보(최고위험)", "red"),
    "03": ("호우 경보", "orange"),
    "04": ("홍수 경보", "orange"),
    # 주의보(경보 아래 단계) — 비가 심해지기 시작하는 초기 단계도 놓치지 않게.
    #  ※ 비/홍수 주의보만 감시(안개·뇌우 등 다른 주의보는 안 봄 → 스팸 방지)
    "10": ("호우 주의보", "yellow"),
    "18": ("홍수 주의보", "yellow"),
}
ACTIVE_STATUS = ("発表", "継続")   # 발표/계속 = 현재 유효. 解除(해제)는 제외.

# JMA 예보구역 코드 → 한국어 지역명 (58개 지역)
PREF_KR = {
    "011000": "홋카이도 소야", "012000": "홋카이도 가미카와·루모이",
    "013000": "홋카이도 아바시리·기타미", "014030": "홋카이도 도카치",
    "014100": "홋카이도 구시로·네무로", "015000": "홋카이도 이부리·히다카",
    "016000": "홋카이도 삿포로권", "017000": "홋카이도 오시마·히야마",
    "020000": "아오모리현", "030000": "이와테현", "040000": "미야기현(센다이)",
    "050000": "아키타현", "060000": "야마가타현", "070000": "후쿠시마현",
    "080000": "이바라키현", "090000": "도치기현", "100000": "군마현",
    "110000": "사이타마현", "120000": "지바현", "130000": "도쿄도",
    "140000": "가나가와현(요코하마)", "150000": "니가타현", "160000": "도야마현",
    "170000": "이시카와현(가나자와)", "180000": "후쿠이현", "190000": "야마나시현",
    "200000": "나가노현", "210000": "기후현", "220000": "시즈오카현",
    "230000": "아이치현(나고야)", "240000": "미에현", "250000": "시가현",
    "260000": "교토부", "270000": "오사카부", "280000": "효고현(고베)",
    "290000": "나라현", "300000": "와카야마현", "310000": "돗토리현",
    "320000": "시마네현", "330000": "오카야마현", "340000": "히로시마현",
    "350000": "야마구치현", "360000": "도쿠시마현", "370000": "가가와현",
    "380000": "에히메현", "390000": "고치현", "400000": "후쿠오카현",
    "410000": "사가현", "420000": "나가사키현", "430000": "구마모토현",
    "440000": "오이타현", "450000": "미야자키현", "460040": "가고시마 아마미",
    "460100": "가고시마현", "471000": "오키나와 본섬", "472000": "다이토섬",
    "473000": "미야코섬", "474000": "야에야마",
}


def get_json(url):
    with urlopen(Request(url, headers=UA), timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


FEED_URL = "https://www.data.jma.go.jp/developer/xml/feed/extra.xml"        # 최근 몇 시간(매분 갱신)
FEED_LONG_URL = "https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml"   # 최근 약 7일
TELEGRAM = "VPWW54"          # 気象警報・注意報（Ｈ２７） — 예보구역별 '지금 유효한 경보·주의보 전체'가 담긴 전문
STALE_HOURS = 24


def get_text(url):
    with urlopen(Request(url, headers=UA), timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_feed(xml):
    """피드 → (피드 갱신 시각(UTC), [(전문 발표 시각 'YYYY-MM-DDTHH:MM:SSZ', 예보구역 코드, 전문 주소)])"""
    m = re.search(r"<updated>(.*?)</updated>", xml)
    try:
        feed_time = datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        feed_time = None
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        href = re.search(r'href="([^"]+)"', e)
        up = re.search(r"<updated>(.*?)</updated>", e)
        if not href or not up or f"_{TELEGRAM}_" not in href.group(1):
            continue
        code = re.search(rf"_{TELEGRAM}_(\d+)\.xml", href.group(1))
        if code:
            out.append((up.group(1), code.group(1), href.group(1)))
    return feed_time, out


def parse_telegram(xml):
    """전문 1건 → (유효한 호우·홍수 특보 코드 집합, 발표 시각). 상태가 発表·継続 인 것만(解除=해제는 제외)."""
    rt = re.search(r"<ReportDateTime>(.*?)</ReportDateTime>", xml)
    active = set()
    body = xml.split("<Body", 1)[-1]
    for kind in re.findall(r"<Kind>(.*?)</Kind>", body, re.S):
        code = re.search(r"<Code>(.*?)</Code>", kind)
        status = re.search(r"<Status>(.*?)</Status>", kind)
        if code and status and code.group(1) in WATCH and status.group(1) in ACTIVE_STATUS:
            active.add(code.group(1))
    # 새 체계의 최고 단계(위험경보·범람 특별경보)는 이 전문의 '알림' 칸에 글로 적힘: "…の発表状況］\nなし" 가 아니면 발표 중
    m = re.search(r"［危険警報・氾濫特別警報の発表状況］\s*([^\n［<]*)", body)
    if m and m.group(1).strip() and not m.group(1).strip().startswith("なし"):
        active.add("R6")
    return active, (rt.group(1) if rt else "")


def latest_per_office(entries, after=""):
    """예보구역마다 가장 최근 전문 1건만. after(지난번에 처리한 시각)보다 새것만."""
    best = {}
    for up, code, href in entries:
        if after and up <= after:
            continue
        if code not in best or up > best[code][0]:
            best[code] = (up, href)
    return best


RANK = {"yellow": 1, "orange": 2, "red": 3}      # 주의보 < 경보 < 특별경보(최고위험)


def level_rank(codes):
    return max((RANK[WATCH[c][1]] for c in codes if c in WATCH), default=0)


def should_alert(seen, code, day, active):
    """같은 구역·같은 날에는 '등급이 올라갈 때'만 True (주의보 → 경보 → 특별경보).
    경보가 이어지는 중에 주의보가 새로 붙거나, 같은 등급의 다른 특보가 추가되는 것은 알리지 않음. 날짜가 바뀌면 다시 알림."""
    rank = level_rank(active)
    if rank == 0:
        return False
    if any(f"{code}-{day}-L{r}" in seen for r in range(rank, 4)):
        return False                              # 오늘 이미 같은(또는 더 높은) 등급을 알림
    seen.add(f"{code}-{day}-L{rank}")
    return True


def load_state():
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return set(d.get("seen_ids", [])), d.get("last_entry", "")
    except Exception:
        return set(), ""


def save_state(seen, last_entry):
    ids = sorted(seen, key=lambda u: re.search(r"\d{4}-\d\d-\d\d", u).group(0) if re.search(r"\d{4}-\d\d-\d\d", u) else "")[-2000:]
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "last_entry": last_entry, "seen_ids": ids},
                  f, ensure_ascii=False, indent=2)


def stale_alert(feed_time, preview):
    """받은 자료가 24시간보다 오래됐으면 True + 시스템 알림(같은 내용은 하루 1번)."""
    if feed_time is not None and datetime.now(timezone.utc) - feed_time <= timedelta(hours=STALE_HOURS):
        return False
    when = feed_time.astimezone(timezone(timedelta(hours=9))).strftime("%m/%d %H:%M") if feed_time else "알 수 없음"
    text = (f"⚠️ [놓치지마] 일본 기상청 자료가 오래됨\n받은 자료의 발표 시각: {when} (한국 시각)\n"
            f"{STALE_HOURS}시간 넘게 새 자료가 없습니다. 일본 호우·홍수 경보 감시가 멈췄을 수 있어 확인이 필요합니다.")
    print(f"⚠️ 일본 기상청 자료가 오래됨 (발표 시각 {when})")
    if not preview:
        try:
            import 시스템알림
            시스템알림.send("일본 기상청 자료 오래됨", text, cooldown_min=24 * 60)
        except Exception:
            pass
    return True


def make_block(pref_kr, codes, rt, area=""):
    """한 지역의 특보를 발송용 블록으로. area=JMA 예보구역 코드(앞 2자리=도도부현 번호)"""
    tag = ("\n" + iso_tag("JP-" + area[:2])) if area[:2].isdigit() else ""
    names = [WATCH[c][0] for c in sorted(codes)]
    levels = {WATCH[c][1] for c in codes}
    detail = ", ".join(names)
    try:
        dt = datetime.fromisoformat(rt)
        when = local_kst_str("일본", dt)
    except Exception:
        when = ""
    if "red" in levels:      # 🔴 특별경보(최고위험)
        return (
            f"🚨🚨 [일본 {pref_kr}] {detail}\n"
            f"━━━━━━━━━━━━\n"
            f"기상청 최고위험 경보입니다.\n"
            f"현지 가족 안부·안전 꼭 확인해보세요.\n"
            f"출처: 일본 기상청(JMA) · {when}{tag}"
        )
    if "orange" in levels:   # 🚨 경보
        return (
            f"🚨 [일본 {pref_kr}] {detail}\n"
            f"현지 가족 안부·외출 확인해보세요.\n"
            f"출처: 일본 기상청(JMA) · {when}{tag}"
        )
    # 🌧️ 주의보(초기 단계) — 경보보다 낮음. 참고용으로 알림.
    return (
        f"🌧️ [일본 {pref_kr}] {detail}\n"
        f"비가 심해질 수 있어요. 외출·일정 참고하세요.\n"
        f"출처: 일본 기상청(JMA) · {when}{tag}"
    )


def main():
    preview = len(sys.argv) > 1 and sys.argv[1] == "미리"
    seen, last_entry = load_state()
    try:
        feed_time, entries = parse_feed(get_text(FEED_URL))
        # 처음이거나, 지난번에 처리한 전문이 짧은 피드 범위보다 오래됐으면(컴퓨터가 꺼져 있었던 경우 등) 7일치 피드로 빈틈을 메움
        oldest = min((e[0] for e in entries), default="")
        if not last_entry or (oldest and last_entry < oldest):
            feed_time2, long_entries = parse_feed(get_text(FEED_LONG_URL))
            entries = long_entries or entries
            feed_time = max([t for t in (feed_time, feed_time2) if t], default=None)
    except Exception as e:
        print(f"⚠️ 일본 기상청 정보를 가져오지 못했습니다. ({e})")
        return
    if stale_alert(feed_time, preview):
        return

    new_blocks = []
    today = datetime.now().strftime("%Y-%m-%d")
    newest = last_entry
    for code, (up, href) in sorted(latest_per_office(entries, last_entry).items()):
        try:
            active, rt = parse_telegram(get_text(href))
        except Exception:
            continue   # 이 지역만 건너뜀(다음 실행 때 다시 읽도록 newest 를 올리지 않음)
        newest = max(newest, up)
        if not active:
            continue
        pref = PREF_KR.get(code, code)
        rt_day = (rt or today)[:10]
        if not should_alert(seen, code, rt_day, active):
            continue
        new_blocks.append(make_block(pref, active, rt, code))

    if new_blocks:
        # 특별경보(🚨🚨) 먼저
        new_blocks.sort(key=lambda b: 0 if b.startswith("🚨🚨") else 1)
        print("\n\n".join(new_blocks))
    else:
        print("🟢 일본 기상청: 새 호우·홍수 경보 없음")

    if not preview:
        save_state(seen, newest)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
