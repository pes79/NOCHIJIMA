# -*- coding: utf-8 -*-
"""
환율 감시 프로그램 (한국 기준 · 하나은행 매매기준율)

■ 무엇을 보나 (2026-10-08 개편 — '10분 전'이 아니라 날짜 단위로 비교)
  ① 전날 대비      : 하루 사이 움직임(월요일엔 금요일 대비)
  ② 일주일 대비    : 며칠에 걸쳐 조금씩 쌓인 변화
  ③ 최근 30일 범위 : 지금이 그 범위에서 낮은 편인지 높은 편인지, '30일 만에 최저/최고'인지
  ※ 예전엔 직전 실행(10분 전)과만 비교해서, 열흘에 걸쳐 2.8% 내린 것 같은 변화를 전혀 못 잡았음.

■ 언제 알리나 (통화마다 기준이 다름 — 평소 움직임보다 유난히 클 때만)
  - 기준 t = max(1%, 그 통화 '평소 하루 움직임(표준편차)'의 2.5배)
  - 💱 변동      : 어제 대비 t 이상            → 브리핑
  - 🚨🚨 급변     : 어제 대비 3t 이상(최소 3%)   → 즉시
  - 💱 30일 최저/최고 : 최근 30일 범위를 벗어남   → 즉시(같은 흐름에선 처음 한 번만)
  - 💱 일주일 변화 : 일주일 대비 3t 이상(최소 3%) → 브리핑
  - 같은 통화·같은 종류는 하루 한 번만.

■ 원칙 : 사실만 알림(얼마인지, 어제·일주일 대비, 30일 범위). "더 내릴 것", "지금 사세요" 같은 예측·권유는 넣지 않음.
■ 데이터 : 네이버 금융(하나은행 고시). 지난 날짜 값은 하루 한 번 받아 rates_history.json 에 보관.
          알림의 '송금 보낼 때' 값은 하나은행 고시 송금 환율(실제 송금 땐 은행·수수료에 따라 다름).
"""

import json
import os
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

from 나라정보 import local_kst_str   # 현지시각(현지 X / 한국 Y) 변환

# ── 설정 ─────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
# 네이버 금융 시장지표 API (하나은행 매매기준율 · API 키 필요 없음)
API_URL = "https://m.stock.naver.com/front-api/marketIndex/productDetail?category=exchange&reutersCode={code}"
HIST_URL = "https://m.stock.naver.com/front-api/marketIndex/prices?category=exchange&reutersCode={code}&page=1&pageSize=45"
UA = {"User-Agent": "Mozilla/5.0"}   # 브라우저인 척(없으면 차단될 수 있음)

SAVE_FILE = os.path.join(BASE, "last_rates.json")       # 방금 값(브리핑이 읽음)
HIST_FILE = os.path.join(BASE, "rates_history.json")    # 날짜별 값(하루 한 번 갱신)
STATE_FILE = os.path.join(BASE, "fx_state.json")        # 오늘 이미 알린 것(중복 방지)

WINDOW_DAYS = 30          # '최근 30일' 범위
MIN_DAYS = 10             # 지난 값이 이만큼은 있어야 30일 범위·평소 움직임을 계산
BASE_PCT = 1.0            # 최소 기준(%). 통화별 기준 t = max(BASE_PCT, 2.5 × 평소 하루 움직임)
SIGMA_MULT = 2.5
RANGE_REALERT_DAYS = 7    # '30일 최저/최고'는 같은 흐름에서 7일 안엔 다시 즉시 알리지 않음(단, 1% 더 벗어나면 알림)

# 감시할 통화 목록 (네이버코드, 이름, 표시라벨, 대표나라)
#  - 우리 30개국 통화 전부(네이버가 값을 주는 22종). 유로존 8개국은 '유로' 하나로 묶임.
#  - 이름은 서로 부분일치하지 않게 구분("달러"→"미국달러", "루피"↔"루피아" 분리 등).
#  - 엔화·루피아·동은 네이버가 '100단위' 기준으로 주므로 라벨도 100 기준.
CURRENCIES = [
    ("FX_USDKRW", "미국달러", "1달러", "미국"),
    ("FX_JPYKRW", "엔화", "100엔", "일본"),
    ("FX_EURKRW", "유로", "1유로", "독일"),
    ("FX_MYRKRW", "링깃", "1링깃", "말레이시아"),
    ("FX_GBPKRW", "파운드", "1파운드", "영국"),
    ("FX_AUDKRW", "호주달러", "1호주달러", "호주"),
    ("FX_CADKRW", "캐나다달러", "1캐나다달러", "캐나다"),
    ("FX_CHFKRW", "스위스프랑", "1스위스프랑", "스위스"),
    ("FX_SGDKRW", "싱가포르달러", "1싱가포르달러", "싱가포르"),
    ("FX_THBKRW", "바트", "1바트", "태국"),
    ("FX_TWDKRW", "대만달러", "1대만달러", "대만"),
    ("FX_PHPKRW", "필리핀페소", "1페소", "필리핀"),
    ("FX_IDRKRW", "루피아", "100루피아", "인도네시아"),
    ("FX_VNDKRW", "베트남동", "100동", "베트남"),
    ("FX_INRKRW", "인도루피", "1루피", "인도"),
    ("FX_BRLKRW", "헤알", "1헤알", "브라질"),
    ("FX_MXNKRW", "멕시코페소", "1페소", "멕시코"),
    ("FX_NZDKRW", "뉴질랜드달러", "1뉴질랜드달러", "뉴질랜드"),
    ("FX_RUBKRW", "루블", "1루블", "러시아"),
    ("FX_SEKKRW", "크로나", "1크로나", "스웨덴"),
    ("FX_TRYKRW", "리라", "1리라", "튀르키예"),
    ("FX_PLNKRW", "즈워티", "1즈워티", "폴란드"),
]

# 통화이름 → 그 나라(현지시각 표시용) / 통화이름 → 코드. 위 표에서 자동으로 만듭니다.
CUR_COUNTRY = {name: country for _c, name, _l, country in CURRENCIES}
NAME_CODE = {name: code for code, name, _l, _c in CURRENCIES}
CODE_LABEL = {code: label for code, _n, label, _c in CURRENCIES}


# ── 파일 도구 ────────────────────────────────────────
def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _get(url):
    with urlopen(Request(url, headers=UA), timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _num(x):
    return float(str(x).replace(",", ""))


# ── 값 가져오기 ──────────────────────────────────────
def get_current_rates():
    """네이버 금융 API에서 현재 환율(하나은행 매매기준율)을 가져옵니다.
    통화가 22종이라, 한 통화가 실패해도 나머지는 정상 수집(개별 try)."""
    result = {}
    for code, _name, _label, _c in CURRENCIES:
        try:
            result[code] = _num(_get(API_URL.format(code=code))["result"]["closePrice"])
        except Exception:
            continue   # 이 통화만 건너뜀 (다음 주기에 다시 시도)
    return result


def load_history(refresh=True):
    """날짜별 환율. {code: {"YYYY-MM-DD": {"c": 매매기준율, "s": 송금 보낼 때}}}
    하루 한 번만 새로 받아옴(통화마다 1번 호출). 실패한 통화는 예전 값 유지."""
    hist = _load(HIST_FILE, {})
    today = datetime.now().strftime("%Y-%m-%d")
    if not refresh or hist.get("_fetched") == today:
        return hist
    ok = 0
    for code, _n, _l, _c in CURRENCIES:
        try:
            rows = _get(HIST_URL.format(code=code)).get("result") or []
            days = hist.setdefault(code, {})
            for r in rows:
                d = {"c": _num(r["closePrice"])}
                try:
                    d["s"] = _num(r["sendValue"])
                except Exception:
                    pass
                days[r["localTradedAt"][:10]] = d
            cut = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            for k in [k for k in days if k < cut]:
                del days[k]
            ok += 1
        except Exception:
            continue
    if ok >= len(CURRENCIES) // 2:          # 절반 이상 받았을 때만 '오늘 받음'으로 표시(아니면 다음 실행 때 다시)
        hist["_fetched"] = today
    _save(HIST_FILE, hist)
    return hist


def fmt_won(value):
    """원화 표시. 값이 작은 통화(동·루피아 등)는 소수점 2자리까지."""
    return f"{value:,.2f}원" if value < 100 else f"{value:,.0f}원"


def _pct(now, ref):
    return (now - ref) / ref * 100 if ref else 0.0


def _arrow(p):
    return f"{'▲' if p > 0 else ('▼' if p < 0 else '→')}{abs(p):.1f}%"


# ── 한 통화의 '지금 상황' 계산 ─────────────────────────
def stats(code, now_value, hist, today=None):
    """어제·일주일 대비, 최근 30일 범위, 그 통화의 평소 움직임과 기준을 계산.
    지난 값이 없으면 d1 등은 None."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    days = hist.get(code) or {}
    past = sorted(d for d in days if d < today)          # 오늘 이전 날짜들
    st = {"now": now_value, "d1": None, "d7": None, "lo": None, "hi": None, "n": 0,
          "span": 0, "t": BASE_PCT, "send": None}
    alld = sorted(d for d in days if not d.startswith("_"))
    if alld:                                             # 송금 환율: 가장 최근 고시값
        st["send"] = days[alld[-1]].get("s")
    if not past:
        return st
    st["y"] = days[past[-1]]["c"]
    st["d1"] = _pct(now_value, st["y"])
    # 일주일 전: 7일 전 날짜 '이전'의 가장 가까운 값
    wk = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    older = [d for d in past if d <= wk]
    if older:
        st["d7"] = _pct(now_value, days[older[-1]]["c"])
    # 최근 30일 범위(종가 기준)
    cut = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    win = [d for d in past if d >= cut]
    if len(win) >= MIN_DAYS:
        vals = [days[d]["c"] for d in win]
        st["lo"], st["hi"], st["n"] = min(vals), max(vals), len(win)
        span = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(win[0], "%Y-%m-%d")).days
        st["span"] = WINDOW_DAYS if span >= WINDOW_DAYS - 6 else span     # 주말 때문에 28~29일로 나오는 건 '30일'로 표시
    # 평소 하루 움직임 → 통화별 기준
    seq = [days[d]["c"] for d in past[-31:]]
    chg = [_pct(b, a) for a, b in zip(seq, seq[1:])]
    if len(chg) >= MIN_DAYS:
        # 하루 크게 튄 날 하나 때문에 기준이 한 달 내내 올라가지 않도록, 평균 대신 '중앙값' 기준으로 계산
        srt = sorted(chg)
        med = srt[len(srt) // 2]
        dev = sorted(abs(c - med) for c in chg)
        sigma = 1.4826 * dev[len(dev) // 2]
        st["sigma"] = sigma
        st["t"] = max(BASE_PCT, SIGMA_MULT * sigma)
    return st


def position_word(st):
    """30일 범위 안에서 지금 위치. 아래 20% = '낮은 편', 위 20% = '높은 편'."""
    if st["lo"] is None or st["hi"] <= st["lo"]:
        return ""
    if st["now"] < st["lo"]:
        return "최저"
    if st["now"] > st["hi"]:
        return "최고"
    pos = (st["now"] - st["lo"]) / (st["hi"] - st["lo"])
    return "낮은 편" if pos <= 0.2 else ("높은 편" if pos >= 0.8 else "")


def _detail_lines(code, st):
    """알림 본문: 값 · 어제/일주일 대비 · 30일 범위 · 송금 환율 · 기준 안내."""
    label = CODE_LABEL[code]
    l1 = f"{label} = {fmt_won(st['now'])} · 전날보다 {_arrow(st['d1'])}"
    if st["d7"] is not None:
        l1 += f" · 일주일 새 {_arrow(st['d7'])}"
    lines = [l1]
    if st["lo"] is not None:
        lines.append(f"최근 {st['span']}일 범위 {fmt_won(min(st['lo'], st['now']))[:-1]}~{fmt_won(max(st['hi'], st['now']))}")
    if st.get("send"):
        lines.append(f"송금 보낼 때 {fmt_won(st['send'])} (하나은행 고시)")
    lines.append("매매기준율 기준 · 실제 환전·송금 땐 달라요")
    return lines


# ── 알림 판단 ────────────────────────────────────────
def decide(code, name, st, state, today, when):
    """이 통화에 대해 지금 보낼 알림 블록(없으면 None). state 는 '오늘 이미 알린 것' 기록."""
    if st["d1"] is None:
        return None
    s = state.setdefault(code, {})
    t = st["t"]
    d1, d7 = st["d1"], st["d7"]

    def once(kind):
        if s.get(kind) == today:
            return False
        s[kind] = today
        return True

    body = "\n".join(_detail_lines(code, st))
    # 1) 급변: 어제 대비 3t 이상(최소 3%)
    if abs(d1) >= max(3.0, 3 * t) and once("spike_" + ("up" if d1 > 0 else "down")):
        return f"🚨🚨 [{name}] 환율 급변\n━━━━━━━━━━━━\n{body}\n🕐 {when}"
    # 2) 30일 범위를 벗어남 → '○일 만에 최저/최고'
    if st["lo"] is not None and (st["now"] < st["lo"] or st["now"] > st["hi"]):
        low = st["now"] < st["lo"]
        key = "range_low" if low else "range_high"
        last = s.get(key) or {}
        recent = False
        if last.get("day"):
            gap = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last["day"], "%Y-%m-%d")).days
            further = abs(_pct(st["now"], last.get("v") or st["now"])) >= 1.0     # 그때보다 1% 더 벗어났으면 다시 알림
            recent = gap < RANGE_REALERT_DAYS and not further
        if not recent:
            s[key] = {"day": today, "v": st["now"]}
            s["move_" + ("up" if d1 > 0 else "down")] = today      # 같은 날 '변동' 알림은 생략(같은 내용 반복 방지)
            return f"💱 [{name}] {st['span']}일 만에 {'최저' if low else '최고'}\n{body}\n🕐 {when}"
    # 3) 변동: 어제 대비 t 이상
    if abs(d1) >= t and once("move_" + ("up" if d1 > 0 else "down")):
        return f"💱 [{name}] 환율 변동\n{body}\n🕐 {when}"
    # 4) 일주일 사이 천천히 쌓인 변화: 3t 이상(최소 3%) — 7일에 한 번
    if d7 is not None and abs(d7) >= max(3.0, 3 * t):
        last = s.get("week")
        gap = 99 if not last else (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
        if gap >= 7:
            s["week"] = today
            return f"💱 [{name}] 일주일 새 {_arrow(d7)}\n{body}\n🕐 {when}"
    return None


# ── 브리핑용 한 줄 ────────────────────────────────────
def brief(name, compact=True):
    """브리핑에 넣는 환율 한 줄. 예) '엔화 850원 ▼0.3%·30일 중 낮은 편'. 값이 없으면 ''."""
    code = NAME_CODE.get(name)
    if not code:
        return ""
    now = (_load(SAVE_FILE, {}).get("rates") or {}).get(code)
    if now is None:
        return ""
    st = stats(code, now, load_history(refresh=False))
    txt = f"{name} {fmt_won(now)}"
    if st["d1"] is not None:
        txt += f" {_arrow(st['d1'])}" if compact else f" (전날보다 {_arrow(st['d1'])})"
    w = position_word(st)
    if w:
        txt += f"·{st['span']}일 중 {w}" if compact else f" · 최근 {st['span']}일 중 {w}"
    return txt


def save_rates(rates):
    """현재 환율을 파일에 저장합니다 (브리핑이 읽음)."""
    _save(SAVE_FILE, {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "rates": rates})


def main():
    # 1) 현재 환율 + 날짜별 기록
    try:
        current = get_current_rates()
    except Exception as e:
        print(f"⚠️ 환율 정보를 가져오지 못했습니다. ({e})")
        return
    hist = load_history()
    state = _load(STATE_FILE, {})
    today = datetime.now().strftime("%Y-%m-%d")

    blocks = []  # 발송용 메시지 블록들 (한 통화 = 한 블록)
    for code, name, label, _country in CURRENCIES:
        if code not in current:      # 이번에 못 가져온 통화는 건너뜀
            continue
        st = stats(code, current[code], hist, today)
        rate_txt = f"{label} = {fmt_won(st['now'])}"
        if st["d1"] is None:         # 지난 값이 아직 없음: 참고용 한 줄
            blocks.append(f"🟢 {name} {rate_txt} (기준 준비 중)")
            continue
        alert = decide(code, name, st, state, today, local_kst_str(CUR_COUNTRY.get(name)))
        if alert:
            blocks.append(alert)
        else:                        # 🟢 참고 한 줄(로그용)
            w = position_word(st)
            blocks.append(f"🟢 {name} {rate_txt} (전날 {_arrow(st['d1'])}" + (f" · {st['span']}일 중 {w}" if w else "") + ")")

    print("\n\n".join(blocks))
    save_rates(current)
    _save(STATE_FILE, state)


if __name__ == "__main__":
    # 콘솔에서 한글/이모지가 깨지지 않도록 설정
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
