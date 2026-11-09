# -*- coding: utf-8 -*-
"""
대형사고_재검사.py — 지난 7일 동안 '대형 사고'로 통과했던 기사를 지금 기준으로 다시 판정해 봅니다.
(새 규모 기준 + 같은 사건 7일 묶기)  카톡·알린 기록·AI 기록은 건드리지 않습니다.   실행: py 대형사고_재검사.py
"""
import os
import re
import sys
from datetime import datetime, timedelta

import AI판단 as ai
import 대형사고감시 as inc

DAYS = 7


def old_passes():
    """AI판단기록에서 지난 7일 '대형사고 yes' 기사."""
    out = []
    for i in range(DAYS - 1, -1, -1):
        day = datetime.now() - timedelta(days=i)
        path = os.path.join("logs", f"AI판단기록_{day:%Y-%m-%d}.log")
        if not os.path.exists(path):
            continue
        t = open(path, encoding="utf-8").read()
        for m in re.finditer(r"^(\d\d:\d\d) \[대형사고\] ✅ yes \| \[([^\]]*)\] (.*)\n\s+원문: (.*)\n\s+이유: (.*)$", t, re.M):
            hh, country, ko, title, why = m.groups()
            when = datetime.strptime(f"{day:%Y-%m-%d} {hh}", "%Y-%m-%d %H:%M")
            out.append({"guid": f"re{len(out)}", "country": country, "ko": ko, "title": title, "src_name": "",
                        "pub": when.strftime("%a, %d %b %Y %H:%M:%S +0900"), "_when": when, "_old": why.split("]")[0] + "]"})
    return out


def main():
    items = old_passes()
    passed, res = ai.judge("대형사고", items, use_pending=False, hold_on_fail=False, write_log=False)
    ok = {it["guid"] for it in passed}
    events, n_send = [], 0
    print(f"지난 {DAYS}일 '대형 사고' 통과 {len(items)}건 → 새 기준으로 다시 판정\n")
    for it in sorted(items, key=lambda x: x["_when"]):
        verdict, why = res.get(it["guid"], ("?", ""))
        if it["guid"] in ok:
            info = it.get("ai") or {}
            send, why2 = inc.check_event(events, it["country"], info, it["_when"].timestamp())
            result = "✅ 발송" if send else f"↩️ 후속(발송 안 함) — {why2}"
            n_send += send
            extra = f"[{info.get('tier')} · {info.get('etype')} · {info.get('city') or '-'} / {info.get('region') or '-'}]"
        else:
            result, extra = f"❌ 제외 — {why[:60]}", ""
        print(f"{it['_when']:%m/%d %H:%M} [{it['country']}] {it['ko'][:44]}\n      전: {it['_old']}  →  지금: {result} {extra}")
    print(f"\n→ 전에는 {len(items)}건 통과, 지금 기준으로는 {n_send}건만 발송")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
