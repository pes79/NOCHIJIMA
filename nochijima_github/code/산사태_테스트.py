# -*- coding: utf-8 -*-
"""
산사태_테스트.py — 최근 30일 뉴스로 '산사태 감시'가 무엇을 잡았을지 시험합니다.
카톡을 보내지 않고, 알린 기록(incident_seen.json)·AI 기록·보류 목록도 건드리지 않습니다.
실행: py 산사태_테스트.py
"""
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import 대형사고감시 as inc
import 정책뉴스감시 as pol
import AI판단 as ai
import 카톡발송 as k

DAYS = 30
PER_COUNTRY = 8


def recent(pub):
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days <= DAYS
    except Exception:
        return False


def main():
    first, stats = [], []
    for country in pol.COUNTRIES:
        name = country["name_kr"]
        try:
            items = inc.fetch(country, inc.q_for_landslide(country["lang"]))
        except Exception as e:
            stats.append((name, "가져오기 실패", 0, 0, 0))
            continue
        trusted = [it for it in items if pol.is_trusted(it, country["trusted"]) and recent(it["pub"])]
        cand = inc.landslide_candidates(items, country, None, recent, PER_COUNTRY)
        first += cand
        stats.append((name, "", len(items), len(trusted), len(cand)))
    print("■ 나라별: 검색된 기사 / 신뢰 언론·30일 이내 / AI에 물어본 수")
    for name, err, a, b, c in stats:
        print(f"  {name}: {err or f'{a} / {b} / {c}'}")

    passed, results = ai.judge("산사태", first, use_pending=False, hold_on_fail=False, write_log=False)
    ok = {it["guid"] for it in passed}
    print(f"\n■ AI판단: 1차 통과 {len(first)}건 → 최종 통과 {len(passed)}건")
    print("\n■ 통과(알림이 나갔을 것)")
    for it in passed:
        text = k.to_kakao_text(inc.make_block(it, it["country"]))
        print(f"--- {pol.short_date(it['pub'])} · {results[it['guid']][1][:60]}\n{text}\n")
    print("■ AI가 뺀 것")
    for it in first:
        if it["guid"] not in ok:
            print(f"  [{it['country']}] {it['ko'][:50]} → {results.get(it['guid'], ('', ''))[1][:70]}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
