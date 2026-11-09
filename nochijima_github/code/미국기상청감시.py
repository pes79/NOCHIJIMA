# -*- coding: utf-8 -*-
"""
미국기상청감시.py — 미국 기상청(NWS) 공식 경보로 '미국 특유 재해'를 잡습니다.

왜 필요한가:
- GDACS(전세계 재난)는 토네이도·눈보라 같은 미국 특유 재해를 안 다룹니다.
- 미국 기상청(NWS) 공식 API로 토네이도·허리케인·돌발홍수·눈보라·폭풍해일 등을 직접 감지.
- 소스: https://api.weather.gov/alerts/active (무료 · API키 불필요 · 미국 정부 공식)
- 이미 알린 경보는 us_nws_seen.json 에 기록해 다시 알리지 않음(경보 id 기준).
"""

import json
import os
import re
import sys
from datetime import datetime
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from 나라정보 import local_kst_str
from 지역필터 import us_allowed_states   # 내가 고른 주(州)만 (지역설정.json)

UA = {"User-Agent": "nochijima-alert/1.0 ([연락처])"}
API_URL = "https://api.weather.gov/alerts/active"
SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_nws_seen.json")
SUMMARY_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_summary_state.json")

# 감시할 경보 종류(NWS event) → (한국어, 위험도). 여기 없는 것(소형선박·안개 등)은 무시.
WATCH_EVENTS = {
    # 🔴 최고위험(즉시 대피급)
    "Tornado Warning": ("토네이도 경보", "red"),
    "Tornado Emergency": ("토네이도 비상(최고위험)", "red"),
    "Hurricane Warning": ("허리케인 경보", "red"),
    "Flash Flood Emergency": ("돌발홍수 비상(최고위험)", "red"),
    "Blizzard Warning": ("눈보라 경보", "red"),
    "Extreme Wind Warning": ("극한 강풍 경보", "red"),
    # 🟠 경보/주의보(중대)
    "Tornado Watch": ("토네이도 주의보", "yellow"),
    "Hurricane Watch": ("허리케인 주의보", "yellow"),
    "Tropical Storm Warning": ("열대폭풍 경보", "orange"),
    "Storm Surge Warning": ("폭풍해일 경보", "orange"),
    "Flash Flood Warning": ("돌발홍수 경보", "orange"),
    "Winter Storm Warning": ("겨울폭풍 경보", "orange"),
    "Ice Storm Warning": ("얼음폭풍 경보", "orange"),
    # 🟡 참고(자주 떠서 브리핑에만)
    "Severe Thunderstorm Warning": ("강한 뇌우 경보", "yellow"),
}

# 토네이도 '경보/비상'(즉시 대피급)만 '따로 즉시' 보냄(생명 직결).
#  ※ 토네이도 '주의보'(Watch)는 자주 떠서 종합(4시간 묶음)에 둠.
TORNADO_RED = {"Tornado Warning", "Tornado Emergency"}

# 발송 주기(분) — 알림 '횟수'를 줄이는 핵심 설정.
SUMMARY_MIN = 240   # 그 외 재해 종합: 4시간에 한 번(내용은 다 담되 횟수만↓)
TORNADO_MIN = 20    # 토네이도 경보: 같은 내용만 20분 억제(=사실상 즉시)

# 종합 메시지 줄 앞에 붙일 작은 이모지
EVENT_EMOJI = {
    "Tornado Warning": "🌪️", "Tornado Emergency": "🌪️", "Tornado Watch": "🌪️",
    "Hurricane Warning": "🌀", "Hurricane Watch": "🌀", "Tropical Storm Warning": "🌀",
    "Storm Surge Warning": "🌊", "Flash Flood Warning": "🌊", "Flash Flood Emergency": "🌊",
    "Blizzard Warning": "❄️", "Winter Storm Warning": "❄️", "Ice Storm Warning": "🧊",
    "Extreme Wind Warning": "💨", "Severe Thunderstorm Warning": "⛈️",
}

# 미국 주 약자 → 한국어 (경보 위치 표시용)
STATE_KR = {
    "AL": "앨라배마", "AK": "알래스카", "AZ": "애리조나", "AR": "아칸소", "CA": "캘리포니아",
    "CO": "콜로라도", "CT": "코네티컷", "DE": "델라웨어", "FL": "플로리다", "GA": "조지아",
    "HI": "하와이", "ID": "아이다호", "IL": "일리노이", "IN": "인디애나", "IA": "아이오와",
    "KS": "캔자스", "KY": "켄터키", "LA": "루이지애나", "ME": "메인", "MD": "메릴랜드",
    "MA": "매사추세츠", "MI": "미시간", "MN": "미네소타", "MS": "미시시피", "MO": "미주리",
    "MT": "몬태나", "NE": "네브래스카", "NV": "네바다", "NH": "뉴햄프셔", "NJ": "뉴저지",
    "NM": "뉴멕시코", "NY": "뉴욕", "NC": "노스캐롤라이나", "ND": "노스다코타", "OH": "오하이오",
    "OK": "오클라호마", "OR": "오리건", "PA": "펜실베이니아", "RI": "로드아일랜드",
    "SC": "사우스캐롤라이나", "SD": "사우스다코타", "TN": "테네시", "TX": "텍사스", "UT": "유타",
    "VT": "버몬트", "VA": "버지니아", "WA": "워싱턴", "WV": "웨스트버지니아", "WI": "위스콘신",
    "WY": "와이오밍", "DC": "워싱턴DC", "PR": "푸에르토리코", "GU": "괌", "VI": "미국령버진아일랜드",
}


def state_of(area_desc):
    """경보 지역에서 대표 주(州) 약자를 뽑습니다(예: 'Chambers, TX'→'TX'). 없으면 ''."""
    first = (area_desc or "").split(";")[0].strip()
    m = re.search(r",\s*([A-Z]{2})\s*$", first)
    return m.group(1) if m else ""


def state_label(state, area_desc):
    """그룹 묶기·표시용 지역 이름(한국어). 주가 있으면 주 이름, 연안이면 '연안'."""
    if state:
        return STATE_KR.get(state, state)
    low = (area_desc or "").lower()
    if any(w in low for w in ("coastal", "waters", "gulf", "marine", "sound", "offshore", "bay")):
        return "연안"
    return "기타 지역"


def get_json(url):
    with urlopen(Request(url, headers=UA), timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_alerts():
    """NWS 활성 경보 중 우리가 감시하는 종류만."""
    data = get_json(API_URL)
    out = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        event = p.get("event", "")
        if event not in WATCH_EVENTS:
            continue
        area = p.get("areaDesc", "")
        # 주(州)는 NWS 지역코드(UGC, 예 'LAZ034'→LA)로 먼저, 없으면 areaDesc에서.
        st = ""
        for code in p.get("geocode", {}).get("UGC", []):
            if len(code) >= 2 and code[:2] in STATE_KR:
                st = code[:2]
                break
        if not st:
            st = state_of(area)
        out.append({
            "id": p.get("id", ""),
            "event": event,
            "area": area,
            "label": state_label(st, area),   # 묶기용 지역명(주)
            "st": st,                          # 주 약자(지역 필터용)
            "sent": p.get("sent", ""),
        })
    return out


def load_seen():
    if not os.path.exists(SAVE_FILE):
        return set()
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("seen", []))
    except Exception:
        return set()


def save_seen(seen):
    ids = list(seen)[-3000:]
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "seen": ids},
                  f, ensure_ascii=False, indent=2)


def load_summary_state():
    """종합 메시지 발송 상태(마지막 내용·시각) — '한 시간에 한 번' 판단용."""
    try:
        with open(SUMMARY_STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_summary_state(state):
    try:
        with open(SUMMARY_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _states_txt(states, n=2):
    s = [x for x in states if x != "기타 지역"] or states
    return "·".join(s[:n]) + (" 외" if len(s) > n else "")


def make_tornado_block(g):
    """토네이도 1건(주 단위) → 따로·즉시 블록. g={event,label,count,sent}."""
    kr, level = WATCH_EVENTS[g["event"]]
    head = f"미국 {g['label']}".strip()
    extra = f" ({g['count']}개 지역)" if g["count"] > 1 else ""
    try:
        when = local_kst_str("미국", datetime.fromisoformat(g["sent"]))
    except Exception:
        when = ""
    if level == "red":      # 토네이도 경보 = 즉시 대피
        return (
            f"🚨🚨 [{head}] {kr}{extra}\n"
            f"━━━━━━━━━━━━\n"
            f"지금 즉시 안전한 곳으로 대피하세요.\n"
            f"출처: 미국 기상청(NWS) · {when}"
        )
    return (                # 토네이도 주의보
        f"🚨 [{head}] {kr}{extra}\n"
        f"토네이도 발생 가능. 현지 상황 주시하세요.\n"
        f"출처: 미국 기상청(NWS) · {when}"
    )


def _issued(groups):
    """묶음 안에서 가장 최근 발표 시각 → '현지 10/11 14:05 (한국 10/12 06:05)'. 못 읽으면 ''.
    현지 시각은 경보를 낸 기상청 사무소의 시간대 그대로(캘리포니아면 태평양 시간).
    (2026-10-15 — 시각이 없어서 9/29 와 10/11 의 같은 문구 '돌발홍수 경보 — 캘리포니아'가
     같은 알림으로 취급돼 10/11 경보가 카톡으로 안 나갔음. 일본·말레이시아 알림처럼 발표 시각을 넣음)"""
    best = None
    for g in groups:
        try:
            dt = datetime.fromisoformat(g.get("sent") or "")
        except ValueError:
            continue
        if dt.tzinfo and (best is None or dt > best):
            best = dt
    if best is None:
        return ""
    kst = best.astimezone(ZoneInfo("Asia/Seoul"))
    return f"현지 {best.month}/{best.day} {best:%H:%M} (한국 {kst.month}/{kst.day} {kst:%H:%M})"


def make_summary_block(eg, title="재해 종합"):
    """미국 재해를 '한 통에' 종합. eg={event: {states,count,sent}}."""
    order = {"red": 0, "orange": 1, "yellow": 2}
    items = sorted(eg.values(), key=lambda g: order.get(WATCH_EVENTS[g["event"]][1], 9))
    levels = {WATCH_EVENTS[g["event"]][1] for g in items}
    # 머리글: 경보(Warning)가 있으면 즉시(🚨/🚨🚨), 주의보(Watch)만 있으면 브리핑(🌧️)
    if "red" in levels:
        head = "🚨🚨"
    elif "orange" in levels:
        head = "🚨"
    else:                       # yellow(주의보)만 → 브리핑용
        head = "🌧️"
    lines = [f"{head} [미국] {title}"]
    for g in items:
        kr, _ = WATCH_EVENTS[g["event"]]
        em = EVENT_EMOJI.get(g["event"], "•")
        lines.append(f"{em} {kr} — {_states_txt(g['states'])}")
    # 심각도에 맞춘 안내(데이터의 위험등급 기반) — 과장 없이 무게만 조절
    if "red" in levels:
        lines.append("⚠️ 최고 위험 — 즉시 안전한 곳으로 대피하세요.")
    elif "orange" in levels:
        lines.append("현지 안전·외출을 주의하세요.")
    else:
        lines.append("현지 상황을 참고하세요.")
    when = _issued(items)
    lines.append("출처: 미국 기상청(NWS)" + (f" · 발표 {when}" if when else ""))
    return "\n".join(lines)


def _group(alerts):
    """경보 목록 → {event: {states, count, sent}} 로 묶기(재해 종류별)."""
    eg = {}
    for a in alerts:
        g = eg.get(a["event"]) or {"event": a["event"], "states": [], "count": 0, "sent": a["sent"]}
        g["count"] += 1
        if a["label"] not in g["states"]:
            g["states"].append(a["label"])
        g["sent"] = max(g["sent"], a["sent"])
        eg[a["event"]] = g
    return eg


def _sig(eg):
    """묶음 내용을 한 줄 '지문'으로(내용이 바뀌었는지 판단용)."""
    return ",".join(sorted(f"{e}:{'/'.join(sorted(g['states']))}" for e, g in eg.items()))


def _should_send(sig, state, min_gap):
    """내용이 바뀌었고 + 마지막 발송 후 min_gap분이 지났을 때만 True."""
    if not state:
        return True
    if sig == state.get("sig"):          # 내용 그대로면 재발송 안 함
        return False
    last = state.get("time")
    if not last:
        return True
    try:
        gap = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        return gap >= min_gap
    except Exception:
        return True


def main():
    try:
        alerts = fetch_alerts()
    except Exception as e:
        print(f"⚠️ 미국 기상청 정보를 가져오지 못했습니다. ({e})")
        return

    # 지역 필터: 내가 고른 주(州)의 특보만 남김. (설정이 '전체'·꺼짐이면 그대로 전부)
    #  ※ 미국 기상청은 주 단위 종합 1통이라, 로그 대신 여기서 거름(연안 해상특보 등 주 없는 건 제외).
    allowed = us_allowed_states()
    if allowed is not None:
        alerts = [a for a in alerts if a.get("st") in allowed]

    seen = load_seen()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out = []

    # 재해를 두 갈래로 나눠 '알림 횟수'를 줄입니다.
    #  (A) 토네이도 '경보/비상'(즉시 대피급) → 따로 '즉시'(같은 내용만 20분 억제)
    #  (B) 그 외 모든 재해 → '종합 1통'으로 4시간에 한 번(내용은 다 담되 횟수만↓)
    tornado = [a for a in alerts if a["event"] in TORNADO_RED]
    others = [a for a in alerts if a["event"] not in TORNADO_RED]

    st = load_summary_state()

    if tornado:                          # (A) 토네이도 경보 — 사실상 즉시
        teg = _group(tornado)
        tsig = _sig(teg)
        if _should_send(tsig, st.get("tornado"), TORNADO_MIN):
            out.append((0, make_summary_block(teg, "토네이도 긴급")))
            st["tornado"] = {"sig": tsig, "time": now_str}

    if others:                           # (B) 그 외 재해 — 4시간 묶음
        oeg = _group(others)
        osig = _sig(oeg)
        if _should_send(osig, st.get("other"), SUMMARY_MIN):
            out.append((1, make_summary_block(oeg)))
            st["other"] = {"sig": osig, "time": now_str}

    save_summary_state(st)

    if out:
        out.sort(key=lambda x: x[0])
        print("\n\n".join(b for _, b in out))
    else:
        print("🟢 미국 기상청: 새 특보 없음")

    save_seen(seen)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
