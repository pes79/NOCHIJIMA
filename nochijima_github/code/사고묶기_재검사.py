# -*- coding: utf-8 -*-
"""
사고묶기_재검사.py — 지난 7일 '대형사고·산사태' 통과 기록을 지금의 같은 사건 묶기 규칙으로 다시 돌려 봅니다.
(도시가 없어도 같은 나라·같은 종류·7일 안·사망·실종 수 ±30% → 후속. 애매하면 AI에게 같은 사건인지만 물음)
카톡·알린 기록·AI 기록은 건드리지 않습니다.   실행: py 사고묶기_재검사.py
"""
import os
import re
import sys
from datetime import datetime, timedelta

import AI판단 as ai
import 대형사고감시 as inc

DAYS = 7


def passes(kind):
    out = []
    for i in range(DAYS - 1, -1, -1):
        day = datetime.now() - timedelta(days=i)
        p = os.path.join("logs", f"AI판단기록_{day:%Y-%m-%d}.log")
        if not os.path.exists(p):
            continue
        for m in re.finditer(rf"^(\d\d:\d\d) \[{kind}\] ✅ yes \| \[([^\]]*)\] (.*)\n\s+원문: (.*)\n\s+이유: (.*)$", open(p, encoding="utf-8").read(), re.M):
            hh, country, ko, title, why = m.groups()
            d = re.search(r"사망·실종 (\d+)", why)
            out.append({"guid": f"{kind}{len(out)}", "country": country, "ko": title, "title": title, "src_name": "", "pub": "",
                        "_when": datetime.strptime(f"{day:%Y-%m-%d} {hh}", "%Y-%m-%d %H:%M"),
                        "_deaths": int(d.group(1)) if d else 0, "_sum": why.split("⇒")[-1].split("(모델")[0].strip()})
    return out


def facts(kind, items):
    """종류·도시·지역을 AI 1차 판정으로 다시 받음(묶음으로 물어 호출을 아낌)."""
    cfg = dict(ai.load_config())
    got = {}
    for s0 in range(0, len(items), 25):
        chunk = items[s0:s0 + 25]
        res, _ = ai._ask(kind, chunk, cfg)
        for i, it in enumerate(chunk):
            raw = res.get(i, ("", "", "", {}))[3]
            got[it["guid"]] = raw
    return got


def main():
    calls = {"same": 0}
    real_same = ai.same_event

    def counted_same(info, cands, cfg=None):
        calls["same"] += 1
        k = real_same(info, cands, cfg)
        print(f"      (AI에게 물음: '{info.get('summary', '')[:30]}' ↔ 후보 {len(cands)}건 → {('같은 사건 #' + str(k)) if k >= 0 else '다른 사건'})")
        return k
    for kind in ("대형사고", "산사태"):
        items = sorted(passes(kind), key=lambda x: x["_when"])
        if not items:
            continue
        raw = facts(kind, items)
        events, sent = [], 0
        print(f"\n■ {kind}: 지난 {DAYS}일 통과 {len(items)}건 → 지금 규칙으로 묶으면")
        for it in items:
            r = raw.get(it["guid"]) or {}
            info = {"etype": "산사태" if kind == "산사태" else str(r.get("etype") or "기타"),
                    "city": str(r.get("city") or ""), "region": str(r.get("region") or ""),
                    "deaths": it["_deaths"], "summary": it["_sum"], "tier": "대형"}
            send, why = inc.check_event(events, it["country"], info, it["_when"].timestamp(), ask_same=counted_same)
            sent += send
            print(f"  {it['_when']:%m/%d %H:%M} [{it['country']}] {it['_sum'][:34]:34} ({info['etype']} · {info['city'] or info['region'] or '장소 없음'} · {it['_deaths']}명)"
                  f" → {'✅ 발송' if send else '↩️ 후속 — ' + why[:70]}")
        print(f"  → 전에는 {len(items)}건 모두 나감, 지금 규칙으로는 {sent}건")
    print(f"\nAI에게 '같은 사건인지' 물은 횟수: {calls['same']}회")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
