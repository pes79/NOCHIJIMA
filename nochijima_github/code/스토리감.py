# -*- coding: utf-8 -*-
"""
스토리감.py — 하루 한 번 '오늘 가장 눈에 띈 소식' 1개를 골라 운영자에게만 보냅니다.
(인스타 스토리 참고용. 기존 알림·브리핑과 별개. 하루 1개.)

■ 무엇을 고르나 (전 세계 기준, 구독 나라 무관)
  - 지진(규모 5.0+), 재난(경보급 GDACS·토네이도), 폭우(극심), 환율(급변) 중
    '자극적인 것'을 점수로 매겨 그날 1등을 뽑음.
  - 정책·대형사고는 제외(스토리엔 안 자극적/뉴스 노이즈).

■ 언제 : 저녁에 하루 1번(작업 스케줄러). 운영자 카톡 '나에게 보내기'로만.
■ 형식 : 국기 · 내용 · 출처 · 시각 한눈에. (실명 등 개인정보 없음)
"""

import glob
import os
import re
import sys
from datetime import datetime

from 나라정보 import flag
from kakao_token import get_valid_token
from 카톡발송 import send_memo

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

# 자극적인(스토리감) 소스만. 정책·대형사고 제외.
#  ※ 호우(구독도시만)·미국기상청(미국만)은 뺌 → '전 세계' 지진·재난 중심.
SOURCES = ["지진감시", "재난감시", "환율감시"]

# 스토리는 '전 세계 딴 나라' 용 → 구독국(일본·미국·말레이)은 제외.
EXCLUDE = ["일본", "Japan", "미국", "말레이시아", "말레이", "달러", "엔화", "링깃"]


def resolve_flag(loc):
    """위치의 국기. '일본 도쿄'·'칠레' 등 첫 단어로도 시도. 없으면 🌍."""
    f = flag(loc)
    if f == "🌍" and loc:
        f = flag(loc.split()[0])
    return f


def read_today_logs():
    """지난 하루(어제+오늘) 로그를 한 덩어리로.
    아침 8시에 돌려도 '어젯밤~새벽' 소식까지 후보에 들어오게."""
    from datetime import timedelta
    now = datetime.now()
    days = [(now - timedelta(days=1)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")]
    texts = []
    for day in days:
        for pfx in SOURCES:
            fp = os.path.join(LOG_DIR, f"{pfx}_{day}.log")
            if os.path.exists(fp):
                try:
                    texts.append(open(fp, encoding="utf-8", errors="replace").read())
                except Exception:
                    pass
    return "\n".join(texts)


def extract_blocks(text):
    """알림 블록(이모지 시작 ~ '출처:' 줄)들을 뽑음."""
    return re.findall(r"^(?:🚨🚨|🚨|🌧️|💱).*?출처:[^\n]*", text, re.MULTILINE | re.DOTALL)


def score(b):
    """블록을 점수화. (점수, 종류, 요약제목, 상세) 반환. 스토리감 아니면 None."""
    # 지진 — 규모 5.0 이상만
    if "지진 발생" in b:
        m = re.search(r"규모\s*(\d+\.\d)", b)
        mag = float(m.group(1)) if m else 0.0
        if mag < 5.0:
            return None
        word = "대지진" if mag >= 7.0 else ("강진" if mag >= 6.0 else "지진")
        return (mag * 12, "지진", f"규모 {mag:.1f} {word}", _quake_detail(b))
    # 환율 급변(🚨🚨)
    if "환율 급변" in b:
        m = re.search(r"([▲▼])\s*([\d.]+)\s*%", b)
        ch = float(m.group(2)) if m else 0.0
        arrow = m.group(1) if m else ""
        val = re.search(r"([\d,]+원)", b)
        return (55 + ch * 4, "환율", f"환율 급변 {arrow}{ch:.1f}%",
                val.group(1) if val else "")
    # 폭우
    if "극심한 폭우" in b or ("폭우" in b and "mm" in b) or ("호우" in b and "mm" in b):
        m = re.search(r"하루\s*(\d+)\s*mm", b)
        mm = int(m.group(1)) if m else 0
        if mm >= 100:
            return (65 + mm * 0.1, "폭우", f"극심한 폭우 (하루 {mm}mm)", "침수·산사태 위험")
        if mm >= 50:
            return (45 + mm * 0.1, "폭우", f"호우 (하루 {mm}mm)", "")
        return None
    # 재난(GDACS 경보·토네이도) — 적색/최고위험 우선
    if "경보" in b or "토네이도" in b:
        first = b.splitlines()[0]
        typ = next((w for w in ("토네이도", "허리케인", "태풍", "산불", "홍수", "화산",
                                "돌발홍수", "눈보라") if w in b), "재난")
        if "🔴" in b or b.lstrip().startswith("🚨🚨"):
            return (78, "재난", f"{typ} 최고등급 경보", "큰 피해 우려")
        if "🟠" in b:
            return (55, "재난", f"{typ} 경보 (중대)", "")
    return None


def _quake_detail(b):
    """지진 블록에서 지역 상세 한 줄(예: 'Miyako 동쪽 148km')."""
    m = re.search(r"━+\n([^\n]+)", b)
    if m:
        return re.sub(r"\s*·?\s*규모.*$", "", m.group(1)).strip()
    return ""


def parse_meta(b):
    """블록에서 위치·출처·시각."""
    loc = re.search(r"\[([^\]]+)\]", b)
    loc = loc.group(1) if loc else ""
    src = re.search(r"출처:\s*([^·\n]+?)(?:\s*·\s*(.+))?$", b, re.MULTILINE)
    source = src.group(1).strip() if src else ""
    when = (src.group(2).strip() if src and src.group(2) else "")
    return loc, source, when


def pick_best():
    text = read_today_logs()
    best = None
    for b in extract_blocks(text):
        s = score(b)
        if not s:
            continue
        loc, source, when = parse_meta(b)
        if any(x in loc for x in EXCLUDE):     # 구독국(일본·미국·말레이) 제외 → 전 세계 딴 나라
            continue
        # 국기 없는 영어 지명(먼 바다 등)은 살짝 감점 → 국기 있는 나라를 우선(스토리 가시성)
        pts = s[0] - (10 if resolve_flag(loc) == "🌍" else 0)
        cand = {"score": pts, "kind": s[1], "head": s[2], "detail": s[3],
                "loc": loc, "src": source, "when": when}
        if best is None or cand["score"] > best["score"]:
            best = cand
    return best


def make_message(c):
    day = datetime.now()
    fl = resolve_flag(c["loc"])
    lines = [f"📸 오늘의 스토리감 · {day.month}/{day.day}", "━━━━━━━━━━━━",
             f"{fl} {c['loc']}", c["head"]]
    if c["detail"]:
        lines.append(c["detail"])
    src_line = f"출처: {c['src']}" + (f" · {c['when']}" if c["when"] else "")
    lines.append(src_line)
    lines.append("━━━━━━━━━━━━")
    lines.append("(스토리 참고용 · 오늘 가장 눈에 띈 소식)")
    return "\n".join(lines)


def main():
    dry = len(sys.argv) > 1 and sys.argv[1] == "dry"
    best = pick_best()
    if not best:
        msg = f"📸 오늘의 스토리감 · {datetime.now().month}/{datetime.now().day}\n오늘은 크게 눈에 띈 소식이 없어요 (조용한 하루)."
    else:
        msg = make_message(best)

    if dry:
        print(msg)
        return

    try:
        token = get_valid_token()
    except Exception as e:
        print(f"❌ 토큰 문제: {e}")
        return
    ok, info = send_memo(token, msg)
    print("✅ 스토리감 발송 완료" if ok else f"❌ 발송 실패: {info}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
