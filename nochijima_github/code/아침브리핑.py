# -*- coding: utf-8 -*-
"""
아침브리핑.py  ── 매일 아침, 구독한 나라의 '오늘 브리핑'을 카톡으로 보냅니다.

내용: 현지시각 · 날씨(open-meteo) · 오늘 환율 · 특이 정책 · 긴급 사항 요약
- 급변이 없어도 '매일 정기'로 발송합니다. (긴급 즉시알림은 카톡발송.py가 따로 담당)
- 대상 나라는 카톡발송.py의 MY_COUNTRIES 설정을 그대로 씁니다. (예: 일본만)
- 카톡 200자 제한 때문에 '나라 1개 = 메시지 1개'로 보냅니다.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

from 카톡발송 import (MY_COUNTRIES, COUNTRY_TO_CURRENCY,
                   recent_logs, extract_blocks, bracket_name, block_hash, send_memo)
from 나라정보 import flag, get_weather, local_kst_str, COUNTRY_INFO, WMO
from kakao_token import get_valid_token
from 지역필터 import block_passes, rules_for, haversine_km   # 내가 고른 지역만 요약(카톡 알림과 같은 기준)

BASE = os.path.dirname(os.path.abspath(__file__))
RATES_FILE = os.path.join(BASE, "last_rates.json")

# 통화 → last_rates.json 안의 키
CUR_KEY = {"달러": "FX_USDKRW", "엔화": "FX_JPYKRW", "유로": "FX_EURKRW"}


def weather_for(country):
    """날씨: 내가 고른 지역(지역설정.json)이 있으면 그 지역, 없으면 나라 대표 도시.
    예) 미국을 캘리포니아로 골랐으면 뉴욕이 아니라 로스앤젤레스 날씨.
    (2026-10-17) 내가 고른 지역이 있는데 그 날씨를 못 받으면 한 번 더 시도하고, 그래도 안 되면 날씨를 빼고 보냄.
    전에는 실패하면 조용히 나라 대표 도시로 바뀌어 10/16 저녁 브리핑에 미국 날씨가 '뉴욕'으로 나갔음."""
    try:
        rules = rules_for(country) or []
    except Exception as e:
        print(f"  (날씨: {country} 지역 설정을 읽지 못해 날씨 생략 — {type(e).__name__})")
        return None
    if not rules:
        return get_weather(country)                        # 나라 전체 설정 → 나라 대표 도시
    r = rules[0]
    if r.get("type") == "hold":
        print(f"  (날씨: {country} 고른 지역을 인식하지 못해 날씨 생략)")
        return None
    last = ""
    for attempt in range(2):
        try:
            got = _region_weather(country, r)
            if got:
                return got
            last = "자료 없음"
        except Exception as e:
            last = type(e).__name__
        if attempt == 0:
            import time
            time.sleep(3)
    print(f"  (날씨: {country} {r.get('name', '')} 날씨를 가져오지 못해 생략 — {last})")
    return None


def _region_weather(country, r):
    """고른 지역 규칙 1개 → 그곳 날씨 dict. 좌표가 없으면 None."""
    if r["type"] == "city":
        name, lat, lon = r["name"], r["lat"], r["lon"]
    else:
        pts = r.get("rain") or []
        if not pts:
            return None
        _, lat, lon = pts[0]                       # 그 지역에서 가장 큰 도시
        name = r["name"]
        try:                                        # 한국어 도시 이름이 있으면 그걸로
            from 호우감시 import WORLD_CITIES
            for cn, cla, clo in WORLD_CITIES.get(country, []):
                if haversine_km(lat, lon, cla, clo) <= 30:
                    name = cn
                    break
        except Exception:
            pass
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&current=temperature_2m,weather_code&timezone=auto")
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as resp:
        cur = json.loads(resp.read().decode("utf-8")).get("current", {})
    desc, emoji = WMO.get(cur.get("weather_code"), ("날씨정보", "🌡️"))
    if cur.get("temperature_2m") is not None:
        return {"city": name, "temp": cur["temperature_2m"], "desc": desc, "emoji": emoji, "source": "open-meteo"}
    return None


def health_line(hours=24):
    """지난 24시간 동안 감시가 실제로 돌았는지 한 줄. (조용한 것과 고장을 구분할 수 있게)
    10분마다 도는 지진감시의 실행 기록으로 판단: 30분 넘게 비면 '멈춤'으로 셈."""
    now = datetime.now()
    start = now - timedelta(hours=hours)
    stamps = []
    for path in recent_logs("지진감시"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for m in re.finditer(r"\[실행 시작\] (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", f.read()):
                    t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    if start <= t <= now:
                        stamps.append(t)
        except Exception:
            pass
    if not stamps:
        return f"⚠️ 지난 {hours}시간 감시 기록 없음 — 그동안의 소식은 빠졌을 수 있어요"
    pts = [start] + sorted(stamps) + [now]
    gaps = [(a, b) for a, b in zip(pts, pts[1:]) if (b - a) > timedelta(minutes=30)]
    down = sum(((b - a) - timedelta(minutes=10) for a, b in gaps), timedelta())
    if down < timedelta(minutes=30):
        return f"🟢 지난 {hours}시간 감시 정상"
    a, b = max(gaps, key=lambda g: g[1] - g[0])
    h = down.total_seconds() / 3600
    return f"⚠️ 지난 {hours}시간 중 약 {h:.0f}시간 감시 멈춤({a:%H:%M}~{b:%H:%M}) — 그 사이 소식은 빠졌을 수 있어요"


def rate_line(country):
    """그 나라 통화의 오늘 환율 줄(전날 대비 + 최근 30일 중 위치). 없으면 None."""
    cur = COUNTRY_TO_CURRENCY.get(country)
    try:
        import 환율감시 as fx
        t = fx.brief(cur, compact=False) if cur else ""
        if t:
            return "💱 " + t
    except Exception:
        pass
    if not cur or cur not in CUR_KEY:
        return None
    try:
        with open(RATES_FILE, "r", encoding="utf-8") as f:
            rates = json.load(f)["rates"]
        val = rates[CUR_KEY[cur]]
        unit = "100엔" if cur == "엔화" else ("1달러" if cur == "달러" else "1유로")
        return f"💱 {cur} {unit} {val:,.0f}원"
    except Exception:
        return None


def count_matching(country, prefixes):
    """(이전 호환용) 그 나라 관련 알림 개수."""
    return len(matched_blocks([country], prefixes))


def matched_blocks(countries, prefixes):
    """오늘+어제 로그에서 해당 나라들 관련 알림 블록(중복 제거)."""
    seen, out = set(), []
    for c in countries:
        for p in prefixes:
            for path in recent_logs(p):
                for b in extract_blocks(path):
                    if c in bracket_name(b) and block_passes(b, p):
                        h = block_hash(b)
                        if h not in seen:
                            seen.add(h); out.append(b)
    return out


def short_title(block, limit=30):
    """알림 블록에서 짧은 제목 한 줄. 예) '[일본] 지진 규모 6.0'.
    limit: 최대 글자수(정책 제목은 더 길게 보이도록 크게 지정)."""
    first = block.splitlines()[0]
    first = re.sub(r"^[🚨🌏💱📋🚗⚡🌧️🟢🟠🔴━ ]+", "", first).strip()   # 앞 이모지·마커 제거
    first = re.sub(r"\s*[🟢🟠🔴]\s*(소규모|중대|대형)\s*$", "", first)   # 끝 등급표시 제거
    m = re.search(r"규모\s*(\d+\.\d)", block)                  # 지진이면 규모 붙이기
    if m and "지진" in first:
        first = first.replace("지진 발생", f"지진 규모 {m.group(1)}")
    if len(first) <= limit:                # 한 줄에 가깝게(핸드폰 가시성)
        return first
    cut = first[:limit]                     # 뜻 안 끊기게 쉼표·구절에서 자르기
    best = max(cut.rfind(s) for s in ("，", "、", ",", "·", " "))
    if best >= limit // 2:
        cut = cut[:best]
    return cut.rstrip(" ,，、·-–|") + "…"


def _rank(block):
    """브리핑 목록 정렬용: 중요한 것(큰 지진·경보)이 위로 오게."""
    s = block.lstrip()
    f = s.splitlines()[0] if s else ""
    m = re.search(r"규모\s*(\d+\.\d)", block)
    mag = float(m.group(1)) if m else 0.0
    if s.startswith("🚨🚨"):                       tier = 0   # 최고위험(Red·규모6+·특별경보)
    elif "지진 발생" in f:                          tier = 1   # 지진(규모순)
    elif s.startswith("🚨") and not ("🟢" in f or "소규모" in f):  tier = 1   # 재난 경보(중대↑)
    elif s.startswith("🌧️"):                       tier = 2   # 호우 주의보
    else:                                          tier = 3   # 소규모(Green) 등
    return (tier, -mag)


def top_titles(countries, prefixes, limit, tlimit=30):
    """주요 제목 목록과 총 건수를 돌려줍니다. (중요한 것부터 limit개)
    tlimit: 제목 최대 글자수(정책은 크게 줘서 '…' 잘림을 줄임).
    ※ 여러 언론사가 같은 뉴스를 보도해 생기는 '거의 같은 제목'은 하나로 합침."""
    blocks = matched_blocks(countries, prefixes)
    blocks.sort(key=_rank)

    def _words(t):
        t = re.sub(r"^\[[^\]]*\]\s*", "", t)            # [나라] 태그 제거
        return set(w for w in re.split(r"[\s,·…\-–|]+", t) if len(w) >= 2)

    titles, kept_words, total = [], [], 0
    for b in blocks:
        full = short_title(b, 44)
        ws = _words(full)
        # 같은 뉴스 판정: 의미 단어 3개 이상 겹치면 중복(여러 언론사·표현 변형 대응)
        if any(ws and kw and len(ws & kw) >= 3 for kw in kept_words):
            continue
        kept_words.append(ws)
        total += 1
        if len(titles) < limit:
            titles.append(short_title(b, tlimit))
    return titles, total


def summarize(countries, prefixes):
    """(구버전 호환) 제목 한 줄 요약."""
    titles, total = top_titles(countries, prefixes, 1)
    if not total:
        return "없음"
    return titles[0] if total == 1 else f"{titles[0]} 외 {total - 1}건"


DISASTER_PREFIXES = ['지진감시', '재난감시', '일본기상청감시', '호우감시', '미국기상청감시', '대형사고감시', '말레이시아기상청감시']


def disaster_digest(countries):
    """재난·지진을 '종류별'로 요약. 막연한 '외 N건' 대신 무엇이 몇 건인지 보이게.
    반환: 표시할 줄들의 리스트(없으면 빈 리스트)."""
    blocks = matched_blocks(countries, DISASTER_PREFIXES)
    quakes, alerts, rains, incidents, thunders, rivers = [], [], [], [], [], []
    for b in blocks:
        first = b.splitlines()[0]
        # GDACS 소규모(Green) 재난은 브리핑에서 제외 — 진짜 위험한 것(Orange↑)만.
        #  (지역 폭우·홍수는 호우감시·기상청이 따로 커버하므로 손실 없음)
        if "🟢" in first or "소규모" in first:
            continue
        if "지진 발생" in first:
            m = re.search(r"규모\s*(\d+\.\d)", b)
            quakes.append((float(m.group(1)) if m else 0.0, b))
        elif "대형 사고" in first:
            incidents.append(b)              # 항공기·붕괴·폭발 등 — 재난과 분리
        elif "뇌우" in first:
            thunders.append(b)               # 뇌우 경보(말레이시아 기상청 등) — 폭우와 따로 셈
        elif "하천" in first and "수위" in first:
            rivers.append(b)                 # 하천 수위(말레이시아 배수관개국) — 폭우와 따로 셈
        elif "말레이시아 기상청" in b and re.search(r"폭우|강우", first):
            rains.append(b)                  # 말레이시아 기상청 폭우·지속 강우 특보는 '폭우'로
        elif "일본 기상청" in b and "호우" in first:
            rains.append(b)                  # 일본 기상청 호우 경보·특별경보(🚨)도 '폭우'로(2026-10-15, 말레이시아와 같은 방식)
        elif b.lstrip().startswith("🌧") or "open-meteo" in b:
            # 호우감시(open-meteo) 폭우는 🚨/🚨🚨(호우·극심)여도 '폭우'로 분류.
            #  (전엔 🚨라서 '재난'으로 새어 재난 건수가 부풀고 폭우가 재난에 섞였음)
            rains.append(b)
        else:
            alerts.append(b)

    def _loc(b):   # 한 줄용 짧은 위치 (예: '미국 알래스카')
        m = re.search(r"\[([^\]]+)\]", b.splitlines()[0])
        return m.group(1) if m else ""

    lines = []
    if quakes:
        quakes.sort(key=lambda x: -x[0])
        mag = quakes[0][0]
        qual = " (대부분 소규모)" if mag < 5.0 else ""
        lines.append(f"🌍 지진 {len(quakes)}건 · 최대 {_loc(quakes[0][1])} {mag:.1f}{qual}")
    if alerts:
        lines.append(f"🚨 재난 {len(alerts)}건 · {short_title(alerts[0])}")
    if incidents:
        locs = []
        for b in incidents:
            l = _loc(b)
            if l and l not in locs:
                locs.append(l)
        more = " 외" if len(locs) > 2 else ""
        lines.append(f"🛫 대형사고 {len(incidents)}건 · {'·'.join(locs[:2])}{more}")
    if rains:
        def _mm(b):
            m = re.search(r"하루\s*(\d+)\s*mm", b)
            return int(m.group(1)) if m else 0
        def _lv(b):   # 🚨🚨 특별경보 > 🚨 경보 > 그 밖
            t = b.lstrip()
            return 2 if t.startswith("🚨🚨") else 1 if t.startswith("🚨") else 0
        rains.sort(key=lambda b: (_lv(b), _mm(b)), reverse=True)   # 가장 심한 폭우를 대표로(경보 등급 → 비의 양)
        lines.append(f"🌧️ 폭우 {len(rains)}건 · {_loc(rains[0])}")

    def _places(bs, n=3):
        locs = []
        for b in bs:
            l = _loc(b)
            if l and l not in locs:
                locs.append(l)
        return "·".join(locs[:n]) + (f" 외 {len(locs) - n}곳" if len(locs) > n else "")
    if thunders:
        lines.append(f"⛈️ 뇌우 {len(thunders)}건 · {_places(thunders)}")
    if rivers:
        rivers.sort(key=lambda b: 0 if b.lstrip().startswith("🚨") else 1 if "위험" in b.splitlines()[0] else 2)   # 위험 수위 먼저
        lines.append(f"🌊 하천 수위 {len(rivers)}건 · {_places(rivers)}")
    return lines


def build_briefing(country, mode="아침"):
    """한 나라의 브리핑 메시지(문자열). mode: '아침' 또는 '저녁'."""
    if mode == "저녁":
        lines = [f"🌙 저녁 브리핑 · {flag(country)} {country}"]
    else:
        lines = [f"🌏 오늘의 브리핑 · {flag(country)} {country}"]
    lines.append(local_kst_str(country))          # 현지 · 한국 시각

    wx = weather_for(country)
    if wx:
        lines.append(f"{wx['emoji']} {wx['city']} {wx['desc']} {wx['temp']:.0f}°C (출처: {wx['source']})")
    else:
        lines.append("🌡️ 날씨 정보를 가져오지 못했습니다")

    rl = rate_line(country)
    if rl:
        lines.append(rl)

    # 특이 정책: 제목들을 나열 (개수만이 아니라 실제 내용 보이게)
    ptitles, ptotal = top_titles([country], ['정책뉴스감시'], 5, tlimit=48)
    lines.append(f"📰 특이 정책 {ptotal}건" if ptotal else "📰 특이 정책: 없음")
    for t in ptitles:
        lines.append(f"· {t}")
    if ptotal > len(ptitles):
        lines.append(f"· 그 외 {ptotal - len(ptitles)}건 (중요한 것부터 표시)")

    lines.append("━━━━━━")

    # 재난·지진: 종류별 요약 (무엇이 몇 건인지 보이게)
    ddig = disaster_digest([country])
    lines.append("⚠️ 재난·지진 요약" if ddig else "⚠️ 재난·지진: 없음")
    lines += ddig

    lines.append(health_line())
    lines.append("📩 긴급(지진·경보)은 확인되는 대로 바로 카톡으로도 보냅니다")
    return "\n".join(lines)


def compact_rate(country):
    """통합 브리핑용 짧은 환율. 예) '엔화 850원 ▼0.3%·30일 중 낮은 편'. 없으면 ''."""
    cur = COUNTRY_TO_CURRENCY.get(country)
    try:
        import 환율감시 as fx
        t = fx.brief(cur, compact=True) if cur else ""
        if t:
            return t
    except Exception:
        pass
    if not cur or cur not in CUR_KEY:
        return ""
    try:
        with open(RATES_FILE, "r", encoding="utf-8") as f:
            val = json.load(f)["rates"][CUR_KEY[cur]]
        return f"{cur} {val:,.0f}원"
    except Exception:
        return ""


def compact_line(country):
    """통합 브리핑용 나라 한 줄. 예) '🇯🇵 일본  ☀️도쿄 24°C · 엔화 870원'."""
    parts = [f"{flag(country)} {country}"]
    wx = weather_for(country)
    if wx:
        parts.append(f"{wx['emoji']}{wx['city']} {wx['temp']:.0f}°C")
    r = compact_rate(country)
    if r:
        parts.append(r)
    # 날씨/환율은 ' · '로 이어붙임
    if len(parts) > 1:
        return parts[0] + "  " + " · ".join(parts[1:])
    return parts[0]


def build_combined(mode):
    """여러 나라를 한 메시지에 통합. 200자 넘으면 여러 메시지로 나눔."""
    title = "🌙 저녁 브리핑" if mode == "저녁" else "🌏 오늘 브리핑"
    now = datetime.now()
    header = f"{title} · 한국 {now.month}/{now.day} {now:%H:%M}"

    lines = [compact_line(c) for c in MY_COUNTRIES if c in COUNTRY_INFO]

    # 특이 정책·재난 요약 — 제목들을 나열(개수만이 아니라 실제 내용)
    cs = [c for c in MY_COUNTRIES if c in COUNTRY_INFO]
    foot = []
    ptitles, ptotal = top_titles(cs, ['정책뉴스감시'], 5, tlimit=48)
    foot.append(f"📋 특이 정책 {ptotal}건" if ptotal else "📋 특이 정책: 없음")
    foot += [f"· {t}" for t in ptitles]
    if ptotal > len(ptitles):
        foot.append(f"· 그 외 {ptotal - len(ptitles)}건 (중요한 것부터 표시)")
    ddig = disaster_digest(cs)
    foot.append("⚠️ 재난·지진 요약" if ddig else "⚠️ 재난·지진: 없음")
    foot += ddig
    foot.append(health_line())
    foot.append("📩 긴급(지진·경보)은 확인되는 대로 바로 카톡으로도 보냅니다")

    # 나라 줄 + 요약 줄 을 200자 제한에 맞춰 여러 메시지로 나눔
    msgs, cur = [], header
    for ln in lines + foot:
        if len(cur) + 1 + len(ln) > 180:
            msgs.append(cur)
            cur = header
        cur += "\n" + ln
    if cur != header:
        msgs.append(cur)
    return msgs


def main():
    # 실행 옵션: '저녁'이면 저녁 브리핑, 없으면 아침
    mode = "저녁" if (len(sys.argv) > 1 and sys.argv[1] == "저녁") else "아침"

    if not MY_COUNTRIES:
        print("MY_COUNTRIES 가 비어 있습니다. (카톡발송.py에서 나라 설정)")
        return
    try:
        token = get_valid_token()
    except Exception as e:
        print(f"❌ 토큰 문제: {e}")
        return

    print(f"[{mode} 브리핑 · 대상 {len(MY_COUNTRIES)}개국]")

    # 나라 2개 이상 → 통합 브리핑 (한 메시지에), 1개 → 기존 상세 브리핑
    if len([c for c in MY_COUNTRIES if c in COUNTRY_INFO]) >= 2:
        sent = 0
        for text in build_combined(mode):
            ok, msg = send_memo(token, text)
            if ok:
                sent += 1
                print(f"✅ 통합 브리핑 발송 ({sent}):")
                print("   " + text.replace("\n", "\n   "))
            else:
                print(f"❌ 실패: {msg}")
        print(f"\n통합 브리핑 {sent}개 메시지 발송 완료.")
        return

    # 나라 1개: 상세 브리핑
    sent = 0
    for country in MY_COUNTRIES:
        if country not in COUNTRY_INFO:
            print(f"  (건너뜀: {country} — 나라정보에 없음)")
            continue
        text = build_briefing(country, mode)
        ok, msg = send_memo(token, text)
        if ok:
            sent += 1
            print(f"✅ 브리핑 발송: {country}")
            print("   " + text.replace("\n", "\n   "))
        else:
            print(f"❌ 발송 실패({country}): {msg}")
    print(f"\n총 {sent}개국 브리핑 발송 완료.")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
