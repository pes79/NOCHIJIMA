# -*- coding: utf-8 -*-
"""환율감시_테스트.py — 가짜 환율 흐름으로 알림 규칙을 시험(발송·기록 안 건드림).  py 환율감시_테스트.py"""
import os, sys
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
import 환율감시 as fx
import 카톡발송 as k

CODE, NAME = "FX_JPYKRW", "엔화"
D0 = datetime(2026, 9, 1)
ok = bad = 0


def day(i):
    return (D0 + timedelta(days=i)).strftime("%Y-%m-%d")


def base_hist(n=30, level=870.0, wobble=2.0):
    """n일 동안 870 근처에서 ±wobble 로 오르내린 평범한 기록."""
    return {CODE: {day(i): {"c": level + (wobble if i % 2 else -wobble), "s": level + 8} for i in range(n)}}


def run(hist, today_i, value, state):
    st = fx.stats(CODE, value, hist, day(today_i))
    return fx.decide(CODE, NAME, st, state, day(today_i), "현지 10/1 09:00"), st


def check(label, got, want):
    global ok, bad
    head = got.splitlines()[0] if got else "(알림 없음)"
    good = (want in head) if want else (got is None)
    ok += good; bad += (not good)
    print(f"{'✅' if good else '❌'} {label}: {head}")


print("기준 t(평범한 기록):", round(fx.stats(CODE, 870, base_hist(), day(30))["t"], 2), "%\n")

# 1) 평소처럼 조금 움직임 → 알림 없음
a, _ = run(base_hist(), 30, 869.0, {}); check("조금 움직임(-0.3%)", a, None)

# 2) 하루에 1.8% 내림 → 변동 / 같은 날 다시 → 없음
h = base_hist(wobble=0.5); s = {}
a, _ = run(h, 30, 870.5 * 0.982, s)
check("하루 -1.8% (30일 최저이기도 함)", a, "30일 만에 최저")
a, _ = run(h, 30, 870.5 * 0.981, s); check("  같은 날 10분 뒤 다시(최저 알림이 나간 날엔 변동 생략)", a, None)
a, _ = run(h, 30, 870.5 * 0.980, s); check("  같은 날 또 다시(중복 방지)", a, None)

# 3) 하루에 5% 급락 → 🚨🚨 급변
a, _ = run(base_hist(wobble=0.5), 30, 870.5 * 0.95, {}); check("하루 -5%", a, "🚨🚨 [엔화] 환율 급변")
a, _ = run(base_hist(wobble=0.5), 30, 870.5 * 1.05, {}); check("하루 +5%", a, "🚨🚨 [엔화] 환율 급변")

# 4) 이번에 실제 있었던 일: 열흘에 걸쳐 조금씩 내려 30일 범위를 벗어남
h = base_hist(n=20, wobble=1.0); s = {}; v = 869.0; alerts = []
for i in range(20, 34):                       # 매일 0.3%씩 14일 연속 하락
    v *= 0.997
    a, st = run(h, i, v, s)
    alerts.append((day(i)[5:], round(v, 1), a.splitlines()[0] if a else "-"))
    h[CODE][day(i)] = {"c": v, "s": v + 8}
for d, val, head in alerts:
    print(f"     {d} {val} {head}")
n_low = sum("만에 최저" in x[2] for x in alerts)
good = 1 <= n_low <= 4
ok += good; bad += (not good)
print(f"{'✅' if good else '❌'} 14일 연속 조금씩 하락(총 {(_ := (alerts[-1][1]/869-1)*100):.1f}%): '최저' 알림 {n_low}번 (처음 + 1% 더 내려갈 때마다, 매일 울리지 않음)\n")

# 5) 일주일 사이 5% 쌓였지만 30일 범위 안(예전에 더 낮았던 적 있음) → 일주일 알림
h = {CODE: {}}
for i in range(30):
    h[CODE][day(i)] = {"c": 800.0 if i < 5 else 870.0, "s": 878}       # 초반에 800 → 30일 최저는 800
v = 870.0
for i in range(23, 30):
    v *= 0.993; h[CODE][day(i)] = {"c": v, "s": v + 8}
a, st = run(h, 30, v * 0.995, {}); check(f"일주일 새 {st['d7']:.1f}% (하루는 -0.5%)", a, "일주일 새")

# 6) 기록이 며칠뿐(10일 미만) → 30일 최저는 말하지 않음, 하루 변동만
h = {CODE: {day(i): {"c": 870.0, "s": 878} for i in range(25, 30)}}
a, st = run(h, 30, 860.0, {}); check("기록 5일뿐인데 최저값(−1.1%)", a, "환율 변동")

# 7) 카톡 문구·발송 판정
a, st = run(base_hist(wobble=0.5), 30, 852.0, {})
print("\n--- 카톡에 보이는 모양 (" + str(len(k.to_kakao_text(a))) + "자)\n" + k.to_kakao_text(a))
for label, blk, want in [("30일 최저 → 바로", a, True),
                         ("변동 → 브리핑", "💱 [엔화] 환율 변동\n100엔 = 860원", False),
                         ("일주일 변화 → 브리핑", "💱 [엔화] 일주일 새 ▼3.4%\n100엔 = 860원", False),
                         ("급변 → 바로", "🚨🚨 [엔화] 환율 급변\n━━\n100엔 = 830원", True)]:
    g = k.is_urgent(blk) == want
    ok += g; bad += (not g)
    print(f"{'✅' if g else '❌'} {label}: 즉시발송={k.is_urgent(blk)} · 내 나라 통과={k.passes_filter(blk)}")
print(f"\n→ {ok}개 맞음 / {bad}개 틀림")
