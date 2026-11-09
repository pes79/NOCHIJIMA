# -*- coding: utf-8 -*-
"""
정책_재검사.py — 지난 며칠 동안 AI판단에 올라왔던 '정책' 기사를 지금 기준(유학생 가족 주제 + 1건씩 재확인)으로 다시 판정합니다.
원문 제목 그대로 묻습니다(실제 감시와 같은 방식). 카톡·알린 기록·AI 기록·보류 목록은 건드리지 않습니다.
실행: py 정책_재검사.py [날짜수=7]      결과는 화면과 정책_재검사_결과.txt 에 남습니다.
"""
import os
import re
import sys
import time
from datetime import datetime, timedelta

import AI판단 as ai


EXTRA_DAYS = 14          # 7일보다 오래된 기록에서도, 아래 낱말이 있는 '진짜 소식 후보'는 함께 다시 봄(예: 10/1 일본 체류 수수료 인상)
EXTRA_KEY = re.compile(r"수수료|手数料|유학|留学|학생비자|student visa|무비자", re.I)


def logged_items(days):
    seen, out = set(), []
    for i in range(max(days, EXTRA_DAYS) - 1, -1, -1):
        day = datetime.now() - timedelta(days=i)
        path = os.path.join("logs", f"AI판단기록_{day:%Y-%m-%d}.log")
        if not os.path.exists(path):
            continue
        t = open(path, encoding="utf-8").read()
        for m in re.finditer(r"^(\d\d:\d\d) \[정책\] (\S+) (\S+) \| \[([^\]]*)\] (.*)\n\s+원문: (.*)\n\s+이유: (.*)$", t, re.M):
            hh, mark, verdict, country, ko, title, why = m.groups()
            if (country, title) in seen or not title.strip():
                continue
            if i >= days and not EXTRA_KEY.search(ko + title):
                continue
            seen.add((country, title))
            out.append({"guid": f"p{len(out)}", "country": country, "ko": title, "title": title, "src_name": "",
                        "pub": "", "_day": f"{day:%m/%d}", "_old": verdict, "_ko": ko})
    return out


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 7
    items = logged_items(days)
    calls = {"n": 0}
    real = ai._call

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    ai._call = counted
    ai._load_recent = lambda: []      # 시험: '이미 보낸 소식' 기록 없이 판정(주제 기준 자체를 보기 위해). 같은 목록 안의 중복은 그대로 묶임
    t0 = time.time()
    print(f"지난 {days}일 AI판단에 올라온 정책 기사 {len(items)}건(중복 제외)을 다시 판정합니다…", flush=True)
    cfg = ai.load_config()
    if len(sys.argv) > 2:                      # 둘째 인자: 먼저 쓸 모델(구글 쪽이 느릴 때 시험을 끝내기 위해)
        cfg = dict(cfg, models=[sys.argv[2]] + [m for m in cfg.get("models", []) if m != sys.argv[2]])
    print("모델 순서:", cfg.get("models"), flush=True)
    passed, res = [], {}
    for s0 in range(0, len(items), 10):
        p1, r1 = ai.judge("정책", items[s0:s0 + 10], cfg=cfg, use_pending=False, hold_on_fail=False, write_log=False)
        passed += p1
        res.update(r1)
        print(f"  … {min(s0 + 10, len(items))}/{len(items)}건 · 통과 {len(passed)} · 호출 {calls['n']}회 · {round(time.time() - t0)}초", flush=True)
    ok = {it["guid"] for it in passed}
    lines = [f"정책 재검사 · {datetime.now():%Y-%m-%d %H:%M} · 기사 {len(items)}건 · Gemini 호출 {calls['n']}회 · {round(time.time() - t0)}초",
             f"통과 {len(passed)}건 / 보류(AI 실패) {sum(1 for v in res.values() if v[0] == '보류')}건", "", "■ 통과"]
    for it in items:
        if it["guid"] in ok:
            a = it.get("ai") or {}
            lines.append(f"  {it['_day']} [{it['country']}] ({a.get('topic')}) {a.get('summary')}  | 전: {it['_old']} | 원문: {it['title'][:70]}")
    lines += ["", "■ 전에는 통과했는데 지금은 빠진 것"]
    for it in items:
        if it["guid"] not in ok and it["_old"] == "yes":
            lines.append(f"  {it['_day']} [{it['country']}] {it['_ko'][:50]}  → {res[it['guid']][1][:70]}")
    lines += ["", "■ 재확인(2단계)에서 빠진 것 — 1차는 통과"]
    for it in items:
        if it["guid"] not in ok and res[it["guid"]][1].startswith("재확인"):
            lines.append(f"  {it['_day']} [{it['country']}] {it['_ko'][:50]}  → {res[it['guid']][1][:80]}")
    lines += ["", "■ 유학생 관련 낱말이 있는데 빠진 것(놓친 진짜 소식이 있는지 볼 것)"]
    key = re.compile(r"유학|학생|留学|student|수수료|手数料|갱신|무비자|입국|체류|在留|운전면허|免許|건강보험|학비|등록금", re.I)
    for it in items:
        if it["guid"] not in ok and key.search(it["_ko"] + it["title"]):
            lines.append(f"  {it['_day']} [{it['country']}] {it['_ko'][:50]}  → {res[it['guid']][0]} · {res[it['guid']][1][:60]}")
    text = "\n".join(lines)
    print(text)
    with open("정책_재검사_결과.txt", "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
