# -*- coding: utf-8 -*-
"""
gemini_사용량.py — Gemini(제미나이) 호출 수를 날짜별로 기록하고, 하루 한도의 80%에 닿으면 시스템 알림을 보냅니다.

■ 기록 : logs/gemini_사용량_날짜.log  (한 줄 = 호출 1번: 시각 · 모델 · 용도 · 결과)
         날짜는 '태평양 시간' 기준 — 구글의 하루 한도(RPD)가 태평양 시간 자정에 초기화되기 때문.
■ 한도 : gemini_한도.json 의 "daily_limit"(모델 하나당 하루 요청 수). 구글은 한도를 문서에 적지 않고
         AI Studio(https://aistudio.google.com → 사용량/Rate limit)에서만 보여 주므로, 거기서 본 숫자로 고쳐 주세요.
■ 알림 : 어느 모델이든 하루 호출 수가 알림 기준(warn_at, 지금 400회)에 닿으면 "Gemini 하루 한도 80%" 시스템 알림(하루 1번).
■ 속도 : wait_turn() — 모든 감시 프로그램을 합쳐 1분에 pace_per_minute(지금 10)회까지만 부르게 기다림.
         공용 기록 gemini_rate.json(최근 1분의 호출 시각)을 여러 프로그램이 같이 씀.
■ 보기 : py gemini_사용량.py        ← 오늘·최근 7일 호출 수
"""
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")
LIMIT_FILE = os.path.join(BASE, "gemini_한도.json")
DEFAULT_LIMIT = {
    "설명": "Gemini 한도(모델마다 따로). AI Studio 에서 확인한 숫자로 고쳐 주세요.",
    "daily_limit": 500,
    "rpm_limit": 15,
    "warn_at": 400,
    "pace_per_minute": 10,
    "확인됨": False,
    "models": {},
}
RATE_FILE = os.path.join(BASE, "gemini_rate.json")          # 최근 1분 동안의 호출 시각(모든 감시 프로그램 공용)
RATE_LOCK = os.path.join(BASE, "gemini_rate.lock")


def model_limit(model, lim=None):
    """그 모델의 (하루 한도, 알림 기준). 모델별 값이 없으면 공통 값."""
    lim = lim or load_limit()
    m = (lim.get("models") or {}).get(model) or {}
    daily = int(m.get("daily_limit") or lim.get("daily_limit") or 0)
    warn = int(m.get("warn_at") or lim.get("warn_at") or (daily * 0.8))
    return daily, warn


# ── 속도 조절: 모든 감시를 합쳐 1분에 pace_per_minute 회까지 ──
def _lock():
    """공용 기록을 고칠 차례 잡기(다른 프로그램과 동시에 쓰지 않게). 최대 20초 기다림."""
    for _ in range(400):
        try:
            fd = os.open(RATE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(RATE_LOCK) > 15:
                    os.remove(RATE_LOCK)              # 죽은 프로그램이 남긴 잠금
            except OSError:
                pass
            time.sleep(0.05)
        except OSError:
            time.sleep(0.05)
    return False


def _unlock():
    try:
        os.remove(RATE_LOCK)
    except OSError:
        pass


def wait_turn(max_wait=180):
    """Gemini 를 부르기 직전에 호출. 최근 60초 동안(모든 프로그램 합계) 이미 pace 회를 불렀으면 자리가 날 때까지 기다림.
    반환: 기다린 시간(초). 기록 파일에 문제가 있어도 감시는 계속되게 조용히 넘어감."""
    waited = 0.0
    try:
        pace = int(load_limit().get("pace_per_minute") or 10)
    except Exception:
        pace = 10
    while True:
        sleep_for = 0.0
        got = _lock()
        try:
            now = time.time()
            try:
                with open(RATE_FILE, "r", encoding="utf-8") as f:
                    times = [t for t in json.load(f) if now - t < 60]
            except Exception:
                times = []
            if len(times) < pace or waited >= max_wait or not got:
                times.append(now)
                try:
                    with open(RATE_FILE, "w", encoding="utf-8") as f:
                        json.dump(times, f)
                except Exception:
                    pass
                return round(waited, 1)
            sleep_for = max(0.2, 60 - (now - min(times)) + 0.1)
        finally:
            if got:
                _unlock()
        time.sleep(sleep_for)
        waited += sleep_for



def pacific_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        return datetime.now(timezone.utc) - timedelta(hours=8)      # 시간대 자료가 없으면 대략(겨울 시간)


def load_limit():
    try:
        with open(LIMIT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = dict(DEFAULT_LIMIT)
        try:
            with open(LIMIT_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    for k, v in DEFAULT_LIMIT.items():
        d.setdefault(k, v)
    return d


def _path(day):
    return os.path.join(LOG_DIR, f"gemini_사용량_{day}.log")


def counts(day=None):
    """그날(태평양 날짜) 모델별 호출 수."""
    day = day or pacific_now().strftime("%Y-%m-%d")
    c = Counter()
    try:
        with open(_path(day), "r", encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2:
                    c[parts[1]] += 1
    except Exception:
        pass
    return c


def record(model, kind="", status="ok", sent=None):
    """호출 1번을 기록하고, 80%를 넘으면 시스템 알림(하루 1번). 기록·알림이 실패해도 감시는 계속되게 조용히 넘어감."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        now = pacific_now()
        day = now.strftime("%Y-%m-%d")
        with open(_path(day), "a", encoding="utf-8") as f:
            f.write(f"{(sent or datetime.now()):%Y-%m-%d %H:%M:%S}\t{model}\t{kind}\t{status}\n")
        lim = load_limit()
        limit, warn = model_limit(model, lim)
        if limit <= 0:
            return
        n = counts(day).get(model, 0)
        if n >= warn:
            import 시스템알림
            text = (f"⚠️ [놓치지마] Gemini 하루 한도 80%\n{model}: 오늘 {n}회 / 한도 {limit}회"
                    + ("" if lim.get("확인됨") else " (한도는 추정치)")
                    + "\n한도를 넘으면 정책·사고 감시의 AI 확인이 보류됩니다. (태평양 시간 자정에 초기화)")
            시스템알림.send(f"Gemini 한도 80% {day}", text, cooldown_min=24 * 60)
    except Exception:
        pass


def main():
    lim = load_limit()
    print(f"하루 한도(모델당): {lim.get('daily_limit')}회 · 분당 {lim.get('rpm_limit')}회 · 알림 기준 {lim.get('warn_at')}회 · "
          f"속도 조절 1분 {lim.get('pace_per_minute')}회 · "
          + ("확인된 값" if lim.get("확인됨") else "확인되지 않은 추정치 — AI Studio 에서 확인 필요"))
    today = pacific_now()
    for i in range(7):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        c = counts(day)
        if c or i == 0:
            print(f"  {day}(태평양 날짜): 합계 {sum(c.values())}회  " + " · ".join(f"{m} {n}" for m, n in c.most_common()))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
