# -*- coding: utf-8 -*-
"""
호우감시_테스트.py — 다낭·호이안 기록(다낭폭우기록.csv, 10/5~10/11)을 새 규칙으로 다시 돌려 봅니다.
카톡은 보내지 않고, rain_seen.json 도 건드리지 않습니다.   실행: py 호우감시_테스트.py
"""
import csv
import io
import sys
from collections import OrderedDict
from datetime import datetime, timedelta

import 호우감시 as rain
import 카톡발송 as k


def load_runs():
    """기록 파일 → {기록시각(한국): [관측값]} (30분마다 1번 실행한 것과 같음)"""
    runs = OrderedDict()
    for r in csv.DictReader(io.open("다낭폭우기록.csv", encoding="utf-8-sig")):
        if not r["현지시각"] or r["오늘 하루합계(예보 포함, mm)"] == "":
            continue
        today = r["현지시각"][:10]
        yday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        obs = {"city": r["도시"], "country": "베트남", "lat": None, "lon": None, "today": today, "yday": yday,
               "today_total": float(r["오늘 하루합계(예보 포함, mm)"]), "today_actual": float(r["오늘 지금까지 실제(mm)"] or 0),
               "yday_sum": float(r["어제 합계(mm)"] or 0), "hail_day": ""}
        runs.setdefault(r["기록시각(한국)"], {})[r["도시"]] = obs      # 같은 시각에 두 번 적힌 줄은 하나로
    return runs


def main():
    runs = load_runs()
    seen, pending = set(), {}
    events = []          # (한국시각, 현지날짜, 도시, 종류, 첫 줄)
    held = []
    for t, obs in runs.items():
        now = datetime.strptime(t, "%Y-%m-%d %H:%M")
        before = set(pending)
        for mm, block in rain.decide(list(obs.values()), seen, pending, now):
            first = block.splitlines()[0]
            city = k.bracket_name(block).split()[-1]
            kind = "예보" if "비 예보" in first else "실제"
            m_day = block.splitlines()[-1]
            events.append((t, city, kind, first, k.is_urgent(block), k.to_kakao_text(block)))
        for u in set(pending) - before:
            held.append((t, u))

    print("■ 새 규칙으로 나가는 알림 (시간순)")
    for t, city, kind, first, urgent, text in events:
        print(f"  {t} · {kind} · {'바로 발송' if urgent else '브리핑에만'} · {first}")
    print("\n■ 밤이라 보류했던 예보 (23~07시)")
    for t, u in held:
        print(f"  {t} 보류 시작 · {u}")

    print("\n■ 확인")
    def has(city, day_txt, kind):
        return any(c == city and kd == kind and day_txt in text for _, c, kd, _, _, text in events)
    checks = [
        ("10/7 다낭: 예보 알림은 나감", has("다낭", "10월 7일", "예보"), True),
        ("10/7 다낭: '폭우(실제)' 알림은 안 나감", has("다낭", "10월 7일", "실제"), False),
        ("10/10 다낭: 밤에 보류 → 07시엔 예보가 기준 아래로 내려가 안 보냄", has("다낭", "10월 10일", "예보"), False),
        ("10/10 다낭: '폭우(실제)' 알림은 안 나감", has("다낭", "10월 10일", "실제"), False),
        ("10/8 호이안: 실제 알림 나감", has("호이안", "10월 8일", "실제"), True),
        ("예보 알림에는 안부 문구 없음", all("안부" not in x[5] for x in events if x[2] == "예보"), True),
        ("실제 알림에는 안부 문구 있음", all(x[5].endswith(k.DISASTER_CLOSING) for x in events if x[2] == "실제"), True),
        ("예보 알림은 밤(23~07시)에 안 나감", all(7 <= int(x[0][11:13]) < 23 for x in events if x[2] == "예보"), True),
        ("같은 날·같은 도시·같은 등급 반복 없음", len({(x[1], x[5]) for x in events}) == len(events), True),
        ("모든 알림 200자 이내", all(len(x[5]) <= 200 for x in events), True),
    ]
    bad = 0
    for label, got, want in checks:
        bad += got != want
        print(f"  {'✅' if got == want else '❌'} {label}")
    print(f"\n→ {len(checks) - bad}개 맞음 / {bad}개 틀림")
    ex = [x for x in events if x[2] == "예보"][:1] + [x for x in events if x[2] == "실제" and x[4]][:1]
    for x in ex:
        print(f"\n--- 카톡에 보이는 모양 ({x[2]}, {len(x[5])}자)\n{x[5]}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
