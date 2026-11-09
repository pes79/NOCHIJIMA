# -*- coding: utf-8 -*-
"""
일일점검.py — 직전 24시간의 감시 품질을 점검해 보고서 파일을 만듭니다.

■ 읽기 전용 : 감시 프로그램·설정·기록 파일을 고치지 않습니다. 카톡도 보내지 않습니다. 인터넷도 쓰지 않습니다.
              키·토큰 파일(gemini_config.json, kakao_config.json, kakao_token.json)은 열지 않습니다.
■ 결과      : 점검보고서\\점검_YYMMDD.txt  (화면에도 같은 내용을 찍습니다)
■ 실행      : 매일 07:30 (작업 스케줄러 nochijima_일일점검)
              py 일일점검.py            ← 지금 기준 직전 24시간
■ 알아둘 점 : 2026-10-14부터 카톡발송 로그에 보낸 문장 전체가 남습니다. 그 전 기록은 첫 줄만 있어서 원본 알림으로 다시 만듭니다(재구성).
              AI판단기록의 원문 링크, 번역 호출 기록(logs\번역_사용량_날짜.log)도 2026-10-14부터 남습니다.
"""
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(BASE, "logs")
OUT_DIR = os.path.join(BASE, "점검보고서")
NOW = datetime.now()
SINCE = NOW - timedelta(hours=24)
WEEK = NOW - timedelta(days=7)

# 감시별 실행 간격(분). 예정 횟수 = 24시간 ÷ 간격
INTERVAL = {"지진감시": 10, "환율감시": 10, "카톡발송": 10, "재난감시": 15, "일본기상청감시": 15, "미국기상청감시": 15,
            "자가감시": 15, "호우감시": 30, "정책뉴스감시": 60, "대형사고감시": 60, "아침브리핑": 720, "스토리감": 1440,
            "말레이시아기상청감시": 30}
ALERT_SOURCES = ["환율감시", "지진감시", "재난감시", "정책뉴스감시", "일본기상청감시", "호우감시", "미국기상청감시", "대형사고감시",
                 "말레이시아기상청감시"]
STARTS = ("🚨", "💱", "📋", "🚗", "🌏", "🌧")
SECRET_FILES = ("gemini_config.json", "kakao_config.json", "kakao_token.json")   # 절대 열지 않음


def read(path):
    if os.path.basename(path) in SECRET_FILES:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def read_json(name, default=None):
    if name in SECRET_FILES:
        return default
    try:
        return json.loads(read(os.path.join(BASE, name)) or "null") or default
    except Exception:
        return default


def days_between(a, b):
    d = a.date()
    while d <= b.date():
        yield d
        d += timedelta(days=1)


def log_text(prefix, since=SINCE):
    return [(d, read(os.path.join(LOGS, f"{prefix}_{d:%Y-%m-%d}.log"))) for d in days_between(since, NOW)]


def runs(prefix, since=SINCE):
    """[(시작 시각, 본문, 끝 줄)] — 자동실행기가 남긴 실행 단위."""
    out = []
    for _, t in log_text(prefix, since):
        for m in re.finditer(r"\[실행 시작\] (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[^\n]*\n(.*?)(?=\n=+\n\[실행 시작\]|\Z)", t, re.S):
            when = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            if since <= when <= NOW:
                end = re.search(r"\[실행 끝\]([^\n]*)", m.group(2))
                out.append((when, m.group(2), end.group(1).strip() if end else ""))
    return out


def blocks_of(body):
    """실행 본문에서 알림 블록(빈 줄로 나뉜, 알림 기호로 시작하는 덩어리)."""
    out = []
    body = re.sub(r"^=+\s*$", "", body, flags=re.M)        # 실행 머리의 구분선(====)은 알림이 아님
    for part in re.split(r"\n\s*\n", body):
        part = part.strip("\n")
        first = part.lstrip().splitlines()[0] if part.strip() else ""
        if first.startswith(STARTS) and not part.lstrip().startswith("🟢"):
            out.append(part.strip())
    return out


def kakao_text(block):
    """카톡발송.to_kakao_text 와 같은 다듬기(그 함수를 그대로 빌려 씀 — 읽기만 함). 실패하면 블록에서 숨은 줄·링크만 뺌."""
    try:
        sys.path.insert(0, BASE)
        import 카톡발송
        return 카톡발송.to_kakao_text(block)
    except Exception:
        return "\n".join(l for l in block.splitlines() if not l.startswith(("@", "자세히 ▶")))


def link_of(block):
    m = re.search(r"자세히 ▶ (\S+)", block)
    return m.group(1) if m else ""


def ai_entries(since=SINCE):
    """AI판단기록 → [dict(when, kind, verdict, country, title, orig, reason)]"""
    out = []
    for d, t in log_text("AI판단기록", since):
        for m in re.finditer(r"^(\d\d:\d\d) \[(\S+)\] (.+?) \| \[([^\]]*)\] (.*)\n\s+원문: (.*)\n\s+이유: (.*)(?:\n\s+링크: (.*))?(?:\n\s+인용: (.*))?$", t, re.M):
            when = datetime.strptime(f"{d:%Y-%m-%d} {m.group(1)}", "%Y-%m-%d %H:%M")
            if since - timedelta(minutes=1) <= when <= NOW:
                v = m.group(3)
                verdict = ("yes" if "yes" in v else "후속" if "후속" in v else "판정 불일치" if "불일치" in v
                           else "장소 불명" if "장소 불명" in v else "보류" if "보류" in v
                           else "버림" if "버림" in v else "검사 실패" if "검사 실패" in v else "no")
                out.append({"when": when, "kind": m.group(2), "verdict": verdict, "country": m.group(4), "title": m.group(5),
                            "orig": m.group(6), "reason": re.sub(r"\s*\(모델 [^)]*\)\s*$", "", m.group(7)),
                            "link": (m.group(8) or "").strip() if (m.group(8) or "").strip() not in ("", "(없음)") else "",
                            "quote": (m.group(9) or "").strip()})
    return out


def source_blocks(since):
    """감시 로그의 알림 블록 전부 → [(시각, 감시, 블록)]"""
    out = []
    for p in ALERT_SOURCES:
        for when, body, _ in runs(p, since):
            for b in blocks_of(body):
                out.append((when, p, b))
    return out


def fmt(dt):
    return dt.strftime("%m/%d %H:%M") if dt else "기록 없음"


def age_mark(dt, limit_h=24):
    if not dt:
        return "⚠️ 기록 없음"
    h = (NOW - dt).total_seconds() / 3600
    return f"{fmt(dt)} ({h:.1f}시간 전)" + ("  ⚠️ 오래됨" if h > limit_h else "")


def parse_dt(s, fmts=("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")):
    for f in fmts:
        try:
            return datetime.strptime(str(s)[:19], f)
        except Exception:
            pass
    return None


def kst_from_block(block):
    """블록의 '출처: … 한국 10/14 09:36' 또는 '현지 …' → 날짜(올해로 가정)."""
    m = re.search(r"한국 (\d{1,2})/(\d{1,2}) (\d\d):(\d\d)", block) or re.search(r"현지 (\d{1,2})/(\d{1,2}) (\d\d):(\d\d)", block)
    if not m:
        return None
    try:
        d = NOW.replace(month=int(m.group(1)), day=int(m.group(2)), hour=int(m.group(3)), minute=int(m.group(4)), second=0, microsecond=0)
        return d if d <= NOW + timedelta(hours=2) else d.replace(year=d.year - 1)
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════════
def build():
    issues = []                  # 맨 위 요약에 올릴 '확인 필요' 항목
    S = []                       # 본문 줄

    def h(title):
        S.extend(["", "━" * 60, title, "━" * 60])

    # ── 1. 가동 상태 ──
    h("1. 가동 상태 (직전 24시간)")
    S.append(f"{'감시':<10} 실행/예정   비정상 종료   가져오기 실패")
    for p, iv in INTERVAL.items():
        rs = runs(p)
        expect = round(24 * 60 / iv)
        bad = [(w, e) for w, _, e in rs if "코드 0" not in e]
        fails = Counter()
        for w, body, _ in rs:
            for ln in re.findall(r"^(?:⚠️|❌)[^\n]*", body, re.M):
                if "오래됨" in ln or "번역 차단" in ln:
                    continue
                fails[re.sub(r"\s+", " ", ln)[:90]] += 1
            if "Traceback" in body:
                fails["프로그램 오류(Traceback)"] += 1
        flag = ""
        if len(rs) < expect * 0.9:
            flag = "  ⚠️ 실행 부족"
            issues.append(f"{p} 실행 부족({len(rs)}/{expect})")
        S.append(f"{p:<10} {len(rs):>4}/{expect:<5}  {len(bad):>3}건        {sum(fails.values())}건{flag}")
        for w, e in bad[:5]:
            S.append(f"      비정상 종료 {fmt(w)}: {e or '끝 기록 없음(실행 중이었거나 강제 종료)'}")
        for k, n in fails.most_common(4):
            S.append(f"      실패 {n}회: {k}")
        if bad:
            issues.append(f"{p} 비정상 종료 {len(bad)}건")
        if sum(fails.values()) >= 3:
            issues.append(f"{p} 가져오기 실패 {sum(fails.values())}회")

    # ── 2. 자료 신선도 ──
    h("2. 자료 신선도 (출처별 가장 최근 자료)")
    blocks7 = source_blocks(WEEK)

    def latest_block_time(prefix):
        ts = [kst_from_block(b) for w, p, b in blocks7 if p == prefix]
        ts = [t for t in ts if t]
        return max(ts) if ts else None

    def my_latest(which):
        """말레이시아 감시 기록에서 가장 최근 '발표'(특보) 또는 '수위' 시각."""
        best = None
        for w, p, b in blocks7:
            if p != "말레이시아기상청감시":
                continue
            if (which == "발표") != ("기상청(MET)" in b):
                continue
            m = re.search(r"(?:발표 |· )(\d\d)/(\d\d) (\d\d):(\d\d)", b)
            if m:
                try:
                    t = NOW.replace(month=int(m.group(1)), day=int(m.group(2)), hour=int(m.group(3)), minute=int(m.group(4)), second=0, microsecond=0)
                    best = max(best, t) if best else t
                except ValueError:
                    pass
        return best

    jma = read_json("jma_seen.json", {}) or {}
    jma_t = None
    if jma.get("last_entry"):
        try:
            jma_t = datetime.strptime(jma["last_entry"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=9)
        except Exception:
            jma_t = None
    rates = read_json("last_rates.json", {}) or {}
    hist = read_json("rates_history.json", {}) or {}
    last_close = max((d for d in (hist.get("FX_USDKRW") or {})), default="")
    fresh = [
        ("일본 기상청(경보 전문)", "마지막으로 읽은 전문의 발표 시각", jma_t, 24 * 3),
        ("지진(USGS)", "가장 최근 지진의 발생 시각", latest_block_time("지진감시"), 24),
        ("재난(GDACS)", "가장 최근 재난 알림의 갱신 시각", latest_block_time("재난감시"), 24),
        ("환율(네이버·하나은행)", "마지막으로 받은 시각", parse_dt(rates.get("time")), 24),
        ("폭우(open-meteo)", "마지막 정상 수신", parse_dt((read_json("rain_seen.json", {}) or {}).get("time")), 24),
        ("미국 기상청(NWS)", "마지막 정상 수신", parse_dt((read_json("us_nws_seen.json", {}) or {}).get("time")), 24),
        ("정책 뉴스(구글뉴스)", "마지막 정상 수신", parse_dt((read_json("seen_news.json", {}) or {}).get("time")), 24),
        ("대형사고 뉴스(구글뉴스)", "마지막 정상 수신", parse_dt((read_json("incident_seen.json", {}) or {}).get("time")), 24),
        ("말레이시아 기상청 특보(MET)", "가장 최근 특보의 발표 시각(기록에 남은 것)", my_latest("발표"), 24 * 3),
        ("말레이시아 하천 수위(JPS)", "가장 최근 수위 알림의 측정 시각(기록에 남은 것)", my_latest("수위"), 24 * 3),
        ("말레이시아 기상청 감시", "마지막 정상 수신", parse_dt((read_json("my_met_seen.json", {}) or {}).get("time")), 24),
    ]
    for name, what, t, limit in fresh:
        mark = age_mark(t, limit)
        S.append(f"· {name}: {mark}   [{what}]")
        if "⚠️" in mark:
            issues.append(f"자료 오래됨: {name}")
    S.append(f"· 환율 일별 기록의 마지막 날짜: {last_close or '기록 없음'}")
    S.append("  ※ 일본 기상청은 경보·주의보가 없는 날엔 새 전문이 없어 3일까지는 정상으로 봅니다.")
    S.append("  ※ 말레이시아 특보·수위 시각은 '알림으로 기록된 것' 기준이라 특보가 없는 날엔 오래돼 보일 수 있어 3일까지 정상으로 봅니다.")
    S.append("  ※ '마지막 정상 수신'은 자료 자체의 발표 시각이 아니라, 감시가 마지막으로 정상 처리한 시각입니다(자료에 발표 시각이 없는 출처).")

    # ── 3. 발송된 알림 ──
    h("3. 발송된 알림 전부 (직전 24시간)")
    src7 = blocks7
    sent = []            # (시각, 종류, 첫 줄, 문장 전체, 링크, 원본 감시)
    for when, body, _ in runs("카톡발송", WEEK):
        for m in re.finditer(r"✅ 보냄: (.*)((?:\n     \| .*)*)", body):
            first = m.group(1).strip()
            logged = [l[7:] for l in m.group(2).splitlines() if l.startswith("     | ")]
            if logged:                                   # 2026-10-14부터: 보낸 문장 전체가 그대로 기록됨
                lk = next((l.replace("(원문 링크) ", "") for l in logged if l.startswith("(원문 링크) ")), "")
                text = "\n".join(l for l in logged if not l.startswith("(원문 링크) "))
                srcs = [(w, p, b) for w, p, b in src7 if b.splitlines()[0].strip() == first and w <= when + timedelta(minutes=1)]
                p0, b0 = (max(srcs, key=lambda x: x[0])[1:] if srcs else ("", ""))
                sent.append((when, "즉시 알림", first, text, lk, p0, b0, True))
                continue
            cand = [(w, p, b) for w, p, b in src7 if b.splitlines()[0].strip() == first and w <= when + timedelta(minutes=1)]
            if cand:
                w, p, b = max(cand, key=lambda x: x[0])
                sent.append((when, "즉시 알림", first, kakao_text(b), link_of(b), p, b, False))
            else:
                sent.append((when, "즉시 알림", first, "(원본 블록을 찾지 못함 — 첫 줄만 기록됨)", "", "", "", False))
    brief = []
    for when, body, _ in runs("아침브리핑"):
        for m in re.finditer(r"✅ 통합 브리핑 발송 \(\d+\):\n((?:   .*\n?)+)", body):
            brief.append((when, "브리핑", "\n".join(l[3:] for l in m.group(1).splitlines())))
    for when, body, _ in runs("주간요약"):
        for m in re.finditer(r"✅ 발송:\n((?:   .*\n?)+)", body):
            brief.append((when, "주간 요약", "\n".join(l[3:] for l in m.group(1).splitlines())))
    sent24 = [x for x in sent if x[0] >= SINCE]
    ai24 = ai_entries()
    ai7 = ai_entries(WEEK)

    def orig_title(block):
        """정책·사고 알림 → AI판단기록에서 원문 제목 찾기(요약 문장으로 맞춤)."""
        body = "\n".join(block.splitlines()[:3])
        for e in reversed(ai7):
            if e["verdict"] == "yes" and "⇒" in e["reason"]:
                summ = e["reason"].split("⇒")[-1].strip()
                if summ and summ[:18] in body:
                    return e["orig"]
        return ""

    S.append(f"즉시 알림 {len(sent24)}건 · 브리핑·요약 메시지 {len(brief)}통")
    S.append("※ 2026-10-14 이후 알림은 발송 로그에 남은 문장 그대로입니다. '(재구성)' 표시가 붙은 것은 그 전 기록이라 원본 알림으로 다시 만든 것입니다.")
    untranslated = []
    for when, typ, first, text, link, p, b, exact in sent24:
        S.append(f"\n▶ {fmt(when)} · {p or '?'} · {first}" + ("" if exact else "  (재구성)"))
        S.extend("    " + l for l in text.splitlines())
        ot = orig_title(b) if b else ""
        if ot:
            S.append(f"    원문 제목: {ot}")
        if link:
            S.append(f"    링크: {link}")
        main = " ".join(text.splitlines()[:2])
        if p in ("정책뉴스감시", "대형사고감시") and len(re.findall(r"[가-힣]", main)) < 6:
            untranslated.append((when, first))
    for when, typ, text in brief:
        S.append(f"\n▶ {fmt(when)} · {typ}")
        S.extend("    " + l for l in text.splitlines())
    if not sent24 and not brief:
        S.append("(발송된 알림 없음)")

    # ── 4. 정책 감시 ──
    h("4. 정책 감시 (AI 판정)")
    pol = [e for e in ai24 if e["kind"] == "정책"]
    pol_blocks = [(w, b) for w, p, b in src7 if p == "정책뉴스감시"]

    def block_link(summary):
        for w, b in reversed(pol_blocks):
            if summary and summary[:18] in b:
                return link_of(b)
        return ""

    S.append(f"AI가 검사한 기사 {len(pol)}건 · 통과 {sum(1 for e in pol if e['verdict'] == 'yes')}건 · 탈락 {sum(1 for e in pol if e['verdict'] == 'no')}건"
             f" · 보류 {sum(1 for e in pol if e['verdict'] == '보류')}건")
    S.append("\n[통과]")
    passes = [e for e in pol if e["verdict"] == "yes"]
    for e in passes:
        why, _, summ = e["reason"].partition("⇒")
        S.append(f"· {fmt(e['when'])} [{e['country']}] {summ.strip() or '(요약 없음)'}")
        S.append(f"    원문 제목: {e['orig']}")
        S.append(f"    링크: {e.get('link') or block_link(summ.strip()) or '(기록에서 찾지 못함)'}")
        S.append(f"    통과 이유: {why.strip()}")
    if not passes:
        S.append("(없음)")
    S.append("\n[탈락 — 제목 | 이유]")
    for e in pol:
        if e["verdict"] != "yes":
            S.append(f"· [{e['country']}] {e['orig'][:80]} | {e['verdict']}: {e['reason'][:90]}" + (f" | {e['link']}" if e.get("link") else ""))
    if not [e for e in pol if e["verdict"] != "yes"]:
        S.append("(없음)")

    # ── 5. 대형사고·산사태 ──
    h("5. 대형사고·산사태")
    inc = [e for e in ai24 if e["kind"] in ("대형사고", "산사태")]
    inc_blocks = [(w, b) for w, p, b in src7 if p == "대형사고감시"]

    def inc_link(summary):
        for w, b in reversed(inc_blocks):
            if summary and summary[:16] in b:
                return link_of(b)
        return ""

    S.append(f"AI가 검사한 기사: 대형사고 {sum(1 for e in inc if e['kind'] == '대형사고')}건 · 산사태 {sum(1 for e in inc if e['kind'] == '산사태')}건")
    S.append("\n[통과]")
    ip = [e for e in inc if e["verdict"] == "yes"]
    for e in ip:
        why, _, summ = e["reason"].partition("⇒")
        nums = re.search(r"\[[^\]]*\]", why)
        S.append(f"· {fmt(e['when'])} [{e['country']}] ({e['kind']}) {summ.strip() or e['orig'][:60]}")
        S.append(f"    숫자: {nums.group(0) if nums else '(기록 없음)'}")
        S.append(f"    원문 제목: {e['orig']}")
        S.append(f"    링크: {e.get('link') or inc_link(summ.strip()) or '(기록에서 찾지 못함)'}")
    if not ip:
        S.append("(없음)")
    for label, key in (("후속으로 묶여 보내지 않은 건", "후속"), ("판정 불일치로 보내지 않은 건", "판정 불일치"),
                       ("장소 불명으로 보내지 않은 건", "장소 불명"), ("다른 나라 사고라 기록만 남긴 건", "다른 나라")):
        S.append(f"\n[{label}]")
        rows = ([e for e in inc if e["verdict"] == "no" and e["reason"].startswith("다른 나라(")] if key == "다른 나라"
                else [e for e in inc if e["verdict"] == key])
        for e in rows:
            S.append(f"· {fmt(e['when'])} [{e['country']}] {e['orig'][:80]} | {e['reason'][:100]}" + (f" | {e['link']}" if e.get("link") else ""))
        if not rows:
            S.append("(없음)")
        if key in ("판정 불일치", "장소 불명") and rows:
            issues.append(f"사고·산사태 {key} {len(rows)}건")

    # ── 6. 중복 의심 ──
    h("6. 중복 의심 (7일 안에 같은 나라·도시·종류로 2번 이상 나간 즉시 알림)")
    groups = defaultdict(list)
    for when, typ, first, text, link, p, b, exact in sent:
        key = re.sub(r"\(.*?\)|\d+(\.\d+)?", "", first)
        key = re.sub(r"[🚨🌧️💱📋🚗🌏🟢🟠🔴\s]+", " ", key).strip()
        detail = (b.splitlines()[1] if b and len(b.splitlines()) > 1 else "")[:60]
        groups[(p, key)].append((when, detail))
    dup = {k: v for k, v in groups.items() if len(v) >= 2}
    for (p, key), v in sorted(dup.items(), key=lambda kv: -len(kv[1])):
        S.append(f"· {key}  ({p}) — {len(v)}번")
        for when, detail in sorted(v):
            S.append(f"      {fmt(when)}  {detail}")
    if not dup:
        S.append("(없음)")
    else:
        S.append("  ※ 지진처럼 서로 다른 사건이 같은 제목으로 나가는 경우도 여기에 잡힙니다. 둘째 줄(위치·규모)을 보고 판단하세요.")
        real_dup = [k for k, v in dup.items() if k[0] in ("정책뉴스감시", "대형사고감시") or len({d for _, d in v}) < len(v)]
        if real_dup:
            issues.append(f"중복 의심 {len(real_dup)}묶음")

    # ── 7. 후보 0건 나라 ──
    h("7. 정책 감시 — 후보가 0건인 나라 (직전 24시간)")
    try:
        sys.path.insert(0, BASE)
        from 나라정보 import COUNTRY_INFO
        names = list(COUNTRY_INFO)
    except Exception:
        names = []
    asked = Counter(e["country"] for e in pol)
    asked7 = Counter(e["country"] for e in ai7 if e["kind"] == "정책")
    zero = [n for n in names if asked.get(n, 0) == 0]
    zero7 = [n for n in names if asked7.get(n, 0) == 0]
    S.append(f"24시간 동안 0건: {len(zero)}개국 — " + (", ".join(zero) if zero else "없음"))
    S.append(f"7일 내내 0건: {len(zero7)}개국 — " + (", ".join(zero7) if zero7 else "없음"))
    S.append("  ※ 하루 0건은 소식이 없던 날이면 정상입니다. 7일 내내 0건인 나라는 검색어·신뢰 언론 목록을 점검해야 합니다.")
    if zero7:
        issues.append(f"7일 내내 정책 후보 0건인 나라 {len(zero7)}곳")

    # ── 8. 번역·AI ──
    h("8. 번역·AI")
    use = []
    for path in glob.glob(os.path.join(LOGS, "gemini_사용량_*.log")):
        for line in read(path).splitlines():
            f = line.split("\t")
            t = parse_dt(f[0]) if f else None
            if t and len(f) >= 4 and SINCE <= t <= NOW:
                use.append((t, f[1], f[2], f[3]))
    by_model = defaultdict(Counter)
    for t, model, kind, status in use:
        by_model[model]["전체"] += 1
        by_model[model]["성공" if status == "ok" else "429(한도 초과)" if "429" in status else "실패"] += 1
    limit = read_json("gemini_한도.json", {}) or {}
    S.append(f"Gemini 호출 {len(use)}회 (하루 한도 모델당 {limit.get('daily_limit', '?')}회, 알림 기준 {limit.get('warn_at', '?')}회)")
    for model, c in sorted(by_model.items(), key=lambda kv: -kv[1]["전체"]):
        S.append(f"· {model}: {c['전체']}회 (성공 {c['성공']} · 실패 {c['실패']} · 429 {c['429(한도 초과)']})")
    fail_kinds = Counter(s for _, _, _, s in use if s != "ok")
    if fail_kinds:
        S.append("  실패 내용: " + ", ".join(f"{k} {n}회" for k, n in fail_kinds.most_common()))
    per_min = max((sum(1 for u in use if 0 <= (u[0] - t[0]).total_seconds() < 60) for t in use), default=0)
    S.append(f"· 1분에 가장 많이 부른 횟수: {per_min}회 (속도 조절 기준 {limit.get('pace_per_minute', '?')}회)")
    n429 = sum(c["429(한도 초과)"] for c in by_model.values())
    if n429:
        issues.append(f"Gemini 한도 초과(429) {n429}회")
    for model, c in by_model.items():
        if limit.get("warn_at") and c["전체"] >= int(limit["warn_at"]):
            issues.append(f"Gemini {model} 하루 {c['전체']}회(알림 기준 이상)")
    cache = read_json("번역캐시.json", {}) or {}
    g_ok = sum(1 for v in cache.values() if isinstance(v, list) and len(v) > 1 and SINCE.timestamp() <= v[1] <= NOW.timestamp())
    st = read_json("번역상태.json", {}) or {}
    tr_use = Counter()
    tr_first = None
    for d, t in log_text("번역_사용량"):
        for line in t.splitlines():
            f = line.split("\t")
            tt = parse_dt(f[0]) if f else None
            if tt and len(f) >= 2 and SINCE <= tt <= NOW:
                tr_use["성공" if f[1] == "성공" else "차단" if f[1].startswith("차단") else "건너뜀" if f[1].startswith("건너뜀") else "실패"] += 1
                tr_first = min(tr_first, tt) if tr_first else tt
    if tr_use:
        S.append(f"구글 번역(기록 시작 {fmt(tr_first)}): 성공 {tr_use['성공']}회 · 실패 {tr_use['실패']}회 · 차단 {tr_use['차단']}회 · 차단 중이라 건너뜀 {tr_use['건너뜀']}회")
        if tr_use["차단"]:
            issues.append(f"구글 번역 차단 {tr_use['차단']}회")
    else:
        S.append(f"구글 번역: 직전 24시간 호출 기록 없음(호출 기록은 2026-10-14부터 남음). 새로 번역해 저장한 제목 {g_ok}건.")
    if st.get("blocked_since"):
        S.append(f"  ⚠️ 지금 번역 차단 중 — {fmt(datetime.fromtimestamp(st['blocked_since']))}부터 ({st.get('last_error', '')})")
        issues.append("구글 번역 차단 중")
    else:
        S.append(f"  번역 차단 없음 (마지막으로 차단이 풀린 때: {fmt(datetime.fromtimestamp(st['last_ok'])) if st.get('last_ok') else '기록 없음'})")
    held = Counter((e["kind"], e["verdict"]) for e in ai24 if e["verdict"] in ("보류", "버림", "검사 실패"))
    S.append("보류·만료: " + (", ".join(f"{k} {v} {n}건" for (k, v), n in held.items()) if held else "없음"))
    pend = read_json("ai_pending.json", {}) or {}
    S.append("지금 보류 중인 기사: " + (", ".join(f"{k} {len(v)}건" for k, v in pend.items() if v) or "없음"))
    retry = read_json("재검사_대기.json", {}) or {}
    S.append(f"번역 실패로 다시 볼 기사: {len(retry)}건")
    dropped = sum(n for (k, v), n in held.items() if v in ("버림", "검사 실패"))
    if dropped:
        issues.append(f"AI 확인 실패로 버린 기사 {dropped}건")
    S.append(f"번역 없이 원문이 그대로 나간 알림: {len(untranslated)}건")
    for when, first in untranslated:
        S.append(f"· {fmt(when)} {first}")
    if untranslated:
        issues.append(f"원문 그대로 나간 알림 {len(untranslated)}건")

    # ── 9. 시스템 알림 ──
    h("9. 시스템 알림 (직전 24시간)")
    sysn = []
    for d, t in log_text("시스템알림"):
        for m in re.finditer(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (✅ 발송|❌ 실패) \[([^\]]*)\]([^\n]*)\n((?:    .*\n?)*)", t, re.M):
            when = parse_dt(m.group(1))
            if when and SINCE <= when <= NOW:
                sysn.append((when, m.group(2), m.group(3), m.group(4).strip(), m.group(5)))
    for when, mark, key, note, body in sysn:
        S.append(f"▶ {fmt(when)} {mark} [{key}] {note}")
        S.extend("    " + l.strip() for l in body.splitlines() if l.strip())
    if not sysn:
        S.append("(없음)")
    real_sys = [x for x in sysn if "시험" not in x[3] and "TEST" not in x[2]]
    if real_sys:
        issues.append(f"시스템 알림 {len(real_sys)}건 발송됨")

    # ── 10. 인식 안 된 도시 ──
    h("10. 받는 도시 인식 (지역설정.json · 주간요약_나라.json)")
    try:
        sys.path.insert(0, BASE)
        import 지역필터 as rf
        aliases, ver = rf.NAME_ALIASES, rf.CACHE_VER
    except Exception:
        aliases, ver = {}, ""
    cache = read_json("지역캐시.json", {}) or {}
    cache = cache.get("rules", cache) if isinstance(cache, dict) else {}
    places = []
    region_cfg = read_json("지역설정.json", {}) or {}
    for c, v in (region_cfg.get("regions") or {}).items():
        for p in ([v] if isinstance(v, str) else list(v or [])):
            places.append((c, p, "지역설정.json"))
    for t in (read_json("주간요약_나라.json", {}) or {}).get("targets") or []:
        if t.get("city"):
            places.append((t.get("country", ""), t["city"], "주간요약_나라.json"))
    bad = []
    for c, p, src in places:
        if not p or p == "전체":
            S.append(f"· [{c}] 전체 ({src}) — 나라 전체라 인식할 필요 없음")
            continue
        clean = re.sub(r"\(?\s*전역\s*\)?$", "", p.replace("지역:", "")).strip()
        clean = aliases.get(c, {}).get(clean, clean)
        key = f"{ver}|{c}|{p}"
        if key in cache:
            r = cache[key]
            if r:
                where = r.get("name") or r.get("code") or ""
                S.append(f"· [{c}] {p} ({src}) — 인식됨: {r.get('type', '')} {where}")
            else:
                S.append(f"· [{c}] {p} ({src}) — ⚠️ 인식 못 함 → 이 도시 알림은 보류 중")
                bad.append(f"{c} {p}")
        else:
            S.append(f"· [{c}] {p} ({src}) — 아직 확인 기록 없음(감시가 처음 이 도시를 쓸 때 확인됨)")
    if bad:
        issues.append(f"인식 안 된 도시 {len(bad)}곳: " + ", ".join(bad))
    S.append("  ※ 인식 못 한 도시는 나라 전체로 넘기지 않고 그 도시 알림을 보류하며, 시스템 알림이 하루 1번 갑니다.")

    # ── 11. 장소 불명으로 보류된 사고·산사태 ── (2026-10-15)
    #  장소를 확인하지 못해 보내지 않은 것들. 운영자가 보고 '놓친 진짜 소식'이 있는지 판단할 수 있게 원문·링크·AI 인용문·이유를 그대로 보여 줌.
    h("11. 장소 불명으로 보류된 사고·산사태 (직전 24시간 · 보내지 않음)")
    unk = [e for e in ai_entries() if e["kind"] in ("대형사고", "산사태") and e["verdict"] == "장소 불명"]
    for e in unk:
        S.append(f"▶ {fmt(e['when'])} [{e['kind']}] [{e['country']}] {e['orig']}")
        S.append(f"    링크: {e['link'] or '(없음)'}")
        S.append(f"    AI 인용문: {e['quote'] or '(없음 — AI가 장소 문장을 찾지 못함)'}")
        why = re.sub(r"\s*→ 보내지 않음\s*$", "", e["reason"])
        S.append(f"    보류 이유: {why}")
    if not unk:
        S.append("(없음)")
    else:
        S.append("  ※ 진짜 우리 지역 소식인데 막힌 것이 있으면 알려 주세요. 줄임말이면 도시약칭.json 에 보태면 다음부터 인식합니다.")
        issues.append(f"장소 불명 보류 {len(unk)}건(놓친 소식 없는지 확인)")

    # ── 12. 맨 위 요약 3줄 ──
    head = [f"놓치지마 일일 점검 · {NOW:%Y-%m-%d %H:%M} 기준 (직전 24시간: {fmt(SINCE)} ~ {fmt(NOW)})", "=" * 60]
    if issues:
        head.append(f"① 확인 필요 {len(issues)}건")
        head.append("② " + " / ".join(issues[:4]) + (f" 외 {len(issues) - 4}건" if len(issues) > 4 else ""))
    else:
        head.append("① 문제 없음")
        head.append("② 실행 부족·비정상 종료·오래된 자료·한도 초과·중복 의심이 없습니다.")
    head.append(f"③ 즉시 알림 {len(sent24)}건 · 브리핑·요약 {len(brief)}통 · 정책 검사 {len(pol)}건(통과 {len(passes)}) · "
                f"사고·산사태 검사 {len(inc)}건(통과 {len(ip)}) · Gemini {len(use)}회")
    return "\n".join(head + S) + "\n"


def main():
    text = build()
    for name in SECRET_FILES:                       # 혹시라도 키 파일 이름·내용이 섞이지 않았는지 마지막 확인
        assert name not in text
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"점검_{NOW:%y%m%d}.txt")
    with open(path, "w", encoding="utf-8-sig") as f:   # 메모장에서 한글이 깨지지 않게
        f.write(text)
    print(text)
    print(f"[보고서 저장: {path}]")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
