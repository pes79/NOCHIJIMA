# -*- coding: utf-8 -*-
"""
자가감시.py  ── 감시들이 '조용히 멈추는 것'을 잡아내는 감시자(워치독)

하는 일:
1) heartbeat.json 을 읽어, 각 감시가 예상 주기보다 오래 안 돌면(멈춤) 찾아냄
   (예: 지진감시는 10분 주기인데 30분째 정상 실행 기록이 없으면 '멈춤')
2) 카카오 토큰이 살아있는지 점검 (발송 실패 조기 감지)
3) 문제가 있으면 카톡으로 "⚠️ ~감시가 N분째 멈춤" 경고
   - 같은 문제로 매번 도배하지 않게, 같은 상태면 2시간에 한 번만 재알림
4) 하루 1번 작업(일일점검·아침/저녁 브리핑·스토리감·주간요약)이 노트북 절전 때문에 빠졌으면 깨어난 뒤 바로 실행
   (2026-10-15 — 작업 스케줄러의 '놓친 작업 바로 시작'이 이미 켜져 있었는데도, 덮개를 닫은 절전 상태에서는
    07:30 일일점검·08:00 브리핑이 따라 돌지 않았음. 그래서 여기서 직접 확인해 같은 작업을 한 번 시작시킴)
- 이 파일도 자동실행기.py가 15분마다 돌립니다(작업: nochijima_watchdog).
  결과는 logs/자가감시_날짜.log 에 남습니다.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

from kakao_token import get_valid_token
from 카톡발송 import send_memo

BASE = os.path.dirname(os.path.abspath(__file__))
HB_FILE = os.path.join(BASE, "heartbeat.json")
STATE_FILE = os.path.join(BASE, "자가감시_state.json")

# 감시할 프로그램과 '예상 실행 주기(분)'. 이 주기의 3배 넘게 소식 없으면 '멈춤'.
WATCH = {
    "지진감시": 10,
    "환율감시": 10,
    "재난감시": 15,
    "일본기상청감시": 15,
    "정책뉴스감시": 60,
    "카톡발송": 10,
    "호우감시": 30,
    "미국기상청감시": 15,
    "대형사고감시": 60,
    "말레이시아기상청감시": 30,
}
# ── 놓친 하루 1번 작업 따라잡기 ──
#  작업 이름: (반복 간격, 늦어도 몇 시간 안이면 따라 돌릴지)
#  너무 늦으면 오히려 헷갈리므로 돌리지 않음(예: 아침 브리핑은 14시까지만, 저녁 브리핑은 자정까지만).
CATCHUP = {
    "nochijima_일일점검": (timedelta(days=1), 12),
    "nochijima_briefing": (timedelta(days=1), 6),
    "nochijima_story": (timedelta(days=1), 6),
    "nochijima_briefing_pm": (timedelta(days=1), 4),
    "nochijima_weekly": (timedelta(days=7), 24),
}
CATCHUP_FROM = datetime(2026, 10, 15, 11, 0)   # 이 시각 이후에 놓친 것부터(설치 전에 놓친 것은 다시 보내지 않음)
CATCHUP_FILE = os.path.join(BASE, "자가감시_따라잡기.json")
STALE_MULT = 3        # 주기 × 3 넘으면 멈춤으로 판단
REMIND_MIN = 120      # 같은 문제 재알림 간격(분)


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _task_times(names):
    """작업 스케줄러에서 {작업: (마지막 실행, 다음 실행)}. 읽기만 함."""
    ps = ("$ErrorActionPreference='SilentlyContinue';"
          + "".join(f"$i=Get-ScheduledTaskInfo -TaskName '{n}'; if($i){{'{n}|'+$i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')+'|'+$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')}};"
                    for n in names))
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                          "[Console]::OutputEncoding=[Text.Encoding]::UTF8;" + ps],
                         capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    got = {}
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        parts = line.strip().split("|")
        if len(parts) == 3:
            try:
                got[parts[0]] = tuple(datetime.strptime(x, "%Y-%m-%d %H:%M:%S") for x in parts[1:])
            except ValueError:
                pass
    return got


def catch_up(now, dry=False):
    """하루 1번 작업이 예정 시각에 안 돌았으면(노트북 절전 등) 지금 한 번 시작. 시작한 작업 이름 목록을 돌려줌."""
    try:
        times = _task_times(list(CATCHUP))
    except Exception as e:
        print(f"   (따라잡기: 작업 시각 읽기 실패 — {type(e).__name__})")
        return []
    done = _load(CATCHUP_FILE)
    started = []
    for name, (period, late_h) in CATCHUP.items():
        if name not in times:
            continue
        last_run, next_run = times[name]
        due = next_run - period                          # 가장 최근에 돌았어야 할 시각
        if due > now or due < CATCHUP_FROM:
            continue
        if last_run >= due - timedelta(minutes=1):
            continue                                     # 제때(또는 이미 따라서) 돌았음
        if done.get(name) == f"{due:%Y-%m-%d %H:%M}":
            continue                                     # 이번 회차는 이미 한 번 시작시켰음(두 번 보내지 않음)
        late = (now - due).total_seconds() / 3600
        if late > late_h:
            print(f"   따라잡기 생략: {name} ({due:%m/%d %H:%M} 예정분) — {late:.1f}시간 늦어 너무 늦음(기준 {late_h}시간)")
            done[name] = f"{due:%Y-%m-%d %H:%M}"
            continue
        if dry:
            print(f"   [시험] 따라잡기 대상: {name} ({due:%m/%d %H:%M} 예정분, {late:.1f}시간 늦음) — 시험이라 실행 안 함")
            started.append(name)
            continue
        r = subprocess.run(["schtasks", "/run", "/tn", name], capture_output=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok = r.returncode == 0
        print(f"   {'▶️ 따라잡기 실행' if ok else '❌ 따라잡기 실패'}: {name} ({due:%m/%d %H:%M} 예정분, {late:.1f}시간 늦음)")
        if ok:
            done[name] = f"{due:%Y-%m-%d %H:%M}"
            started.append(name)
    if not dry:
        _save(CATCHUP_FILE, done)
    return started


def main():
    now = datetime.now()
    catch_up(now, dry=bool(os.environ.get("NOCHIJIMA_TEST")))
    hb = _load(HB_FILE)
    stale = []         # 멈춘 감시 설명
    stale_keys = []
    watched = 0        # 심장박동이 있는(감시 가능한) 감시 수

    # 1) 각 감시의 '마지막 정상 실행'이 너무 오래됐는지
    for name, interval in WATCH.items():
        rec = hb.get(name)
        if not rec:
            continue   # 아직 한 번도 안 돎(첫 배포 등) → 건너뜀
        watched += 1
        threshold = interval * STALE_MULT
        last_ok = rec.get("last_ok")
        if not last_ok:
            stale.append(f"{name}: 정상 실행 기록 없음(계속 실패 중)")
            stale_keys.append(f"{name}:nook")
            continue
        try:
            dt = datetime.strptime(last_ok, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        mins = int((now - dt).total_seconds() // 60)
        # (2026-10-20) 프로그램은 제때 돌고 있는데 '자료 받기'만 실패하는 경우는 '멈춤'이 아니라 '자료 실패'로 구분하고,
        #  자동으로 다시 시도하므로 기준을 1시간으로 늘림(10/20 USGS 서버 불안정으로 '지진감시 멈춤' 헛경보가 4번 나갔음).
        note = rec.get("note", "")
        running = False
        try:
            lr = datetime.strptime(rec.get("last_run", ""), "%Y-%m-%d %H:%M:%S")
            running = (now - lr).total_seconds() <= (interval * 2 + 5) * 60
        except Exception:
            pass
        if running and "데이터" in note:
            if mins > max(60, threshold):
                stale.append(f"{name} 자료 받기 {mins}분째 실패 — 프로그램은 돌고 있고 자동으로 다시 시도 중 (마지막 정상 {dt:%H:%M})")
                stale_keys.append(f"{name}:datafail")
            continue
        if mins > threshold:
            extra = f" ({note})" if note else ""
            stale.append(f"{name} {mins}분째 멈춤 (마지막 정상 {dt:%H:%M}){extra}")
            stale_keys.append(f"{name}:stale")

    # 2) 카카오 토큰 점검
    token_ok = True
    token_problem = None
    try:
        get_valid_token()
    except Exception as e:
        token_ok = False
        token_problem = f"카톡 토큰 문제 — 발송 안 될 수 있음 ({str(e)[:40]})"

    # ★ 컴퓨터가 방금 다시 켜진 직후(자가감시 자신도 45분 넘게 안 돌았음)에는
    #   감시들이 아직 한 바퀴 못 돈 것뿐이라 '멈춤' 경고가 헛경보가 됨 → 이번 한 번은 경고 생략.
    last_file = os.path.join(BASE, "자가감시_last.json")
    prev = _load(last_file).get("t")
    _save(last_file, {"t": now.strftime("%Y-%m-%d %H:%M:%S")})
    try:
        resumed = (now - datetime.strptime(prev, "%Y-%m-%d %H:%M:%S")).total_seconds() > 45 * 60
    except Exception:
        resumed = False
    if resumed and stale and token_ok:
        print(f"🟡 자가감시: 방금 다시 켜짐 — 감시들이 따라잡는 중이라 이번엔 경고 생략 ({now:%H:%M}, 대기 {len(stale)}건)")
        return

    # 문제 전혀 없음 → 조용히 정상
    if not stale and token_ok:
        print(f"🟢 자가감시: 모든 감시 정상 ({now:%H:%M})")
        _save(STATE_FILE, {})     # 문제 해소 → 다음 문제는 즉시 알림되게 초기화
        return

    # 로그엔 항상 전부 남긴다
    if stale:
        print(f"자가감시: 멈춘 감시 {len(stale)}건 ({now:%H:%M})")
        for p in stale:
            print("   - " + p)
    if token_problem:
        print("   - " + token_problem)

    # ★ 여러 감시가 '동시에' 멈춤 = PC 절전/종료 신호(개별 고장 아님).
    #   (감시 가능한 것의 과반, 최소 3개↑ 멈추면 시스템 전체 중단으로 판단)
    system_wide = watched and len(stale) >= max(3, (watched + 1) // 2)

    # 카톡 경고는 '진짜 고장'일 때만: 개별(부분) 멈춤 또는 토큰 문제.
    #   전체 동시 멈춤(절전/종료)은 로그만 남기고 카톡은 생략(도배 방지).
    problems, keys = [], []
    if system_wide:
        print("   → 여러 감시가 동시에 멈춤 = PC 절전/종료로 판단(코드 문제 아님).")
        print("     PC가 깨어나면 자동 재개됩니다. 카톡 경고는 생략합니다.")
    elif stale:
        problems += stale
        keys += stale_keys
    if token_problem:
        problems.append(token_problem)
        keys.append("token")

    if not problems:
        _save(STATE_FILE, {})    # 카톡 보낼 진짜 문제 없음(절전뿐) → 상태 초기화
        return

    print(f"⚠️ 카톡 경고 대상 {len(problems)}건")

    # 3) 중복방지: 같은 문제 집합이면 REMIND_MIN 마다 한 번만 재알림
    sig = ",".join(sorted(keys))
    state = _load(STATE_FILE)
    last_sig = state.get("sig")
    last_time = state.get("time")
    should_send = True
    if last_sig == sig and last_time:
        try:
            gap = (now - datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            if gap < REMIND_MIN:
                should_send = False   # 같은 문제로 방금 알렸으면 조용히
        except Exception:
            pass

    if not should_send:
        print("   (같은 문제로 최근에 이미 알림 — 이번엔 카톡 생략)")
        return

    # 4) 카톡 경고 발송
    lines = ["⚠️ 놓치지마 자가점검 경고"]
    lines += ["• " + p for p in problems]
    lines.append("→ 시스템 확인이 필요해요.")
    text = "\n".join(lines)[:200]

    sent_ok = False
    if token_ok:
        try:
            import 시스템알림                       # 발송 기록을 남기고, 같은 경고가 겹쳐 나가지 않게
            sent_ok, _ = 시스템알림.send("감시 멈춤: " + sig, text, cooldown_min=max(1, REMIND_MIN - 5))
        except Exception as e:
            print(f"   (카톡 발송 시도 실패: {e})")
    else:
        print("   (토큰 문제로 카톡 발송 불가 — 로그로만 남김)")

    print("   → 카톡 경고 발송:", "✅ 성공" if sent_ok else "❌ 실패/생략")
    _save(STATE_FILE, {"sig": sig, "time": now.strftime("%Y-%m-%d %H:%M:%S")})


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
