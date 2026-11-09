# -*- coding: utf-8 -*-
"""
시스템알림.py — "번역 차단 중", "감시 멈춤" 같은 '시스템 알림'을 한곳에서 보냅니다.

■ 하는 일
  1) 카톡 '나에게 보내기'로 발송
  2) 발송 기록을 남김 : logs/시스템알림_날짜.log (시각·성공 여부·내용 전체)
                        logs/카톡발송_날짜.log 에도 한 줄(일반 알림 발송 기록과 같이 볼 수 있게)
  3) 같은 알림(key)이 여러 감시에서 동시에 나가지 않게 한 번만 보냄
     - 먼저 '잠금 파일'을 만든 프로그램만 보냄(동시에 돌아도 하나만 성공)
     - 보낸 뒤 cooldown_min 분 동안은 같은 key 를 다시 보내지 않음
■ 쓰는 곳 : 번역도우미.py(번역 차단), 자가감시.py(감시 멈춤 경고)
"""
import os
import re
import time
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")


def _lock_path(key):
    return os.path.join(LOG_DIR, "_시스템알림_" + re.sub(r"[^0-9A-Za-z가-힣]+", "_", key)[:60] + ".lock")


def _claim(key, cooldown_min):
    """이 알림을 보낼 차례를 잡으면 True. 다른 프로그램이 방금 잡았거나 최근에 보냈으면 False."""
    os.makedirs(LOG_DIR, exist_ok=True)
    path = _lock_path(key)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, datetime.now().strftime("%Y-%m-%d %H:%M:%S").encode("utf-8"))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) < cooldown_min * 60:
                    return False
                os.remove(path)                       # 오래된 잠금 → 지우고 다시 시도
            except OSError:
                return False
    return False


def _log(ok, key, text, note=""):
    now = datetime.now()
    mark = "✅ 발송" if ok else "❌ 실패"
    try:
        with open(os.path.join(LOG_DIR, f"시스템알림_{now:%Y-%m-%d}.log"), "a", encoding="utf-8") as f:
            f.write(f"{now:%Y-%m-%d %H:%M:%S} {mark} [{key}]{(' ' + note) if note else ''}\n"
                    + "\n".join("    " + ln for ln in text.splitlines()) + "\n\n")
        with open(os.path.join(LOG_DIR, f"카톡발송_{now:%Y-%m-%d}.log"), "a", encoding="utf-8") as f:
            f.write(f"[시스템 알림] {now:%Y-%m-%d %H:%M:%S} {mark} [{key}] {text.splitlines()[0][:80]}\n")
    except Exception:
        pass


def send(key, text, cooldown_min=60, dry_run=False):
    """시스템 알림 1건. 반환: (보냈는지, 이유)
    dry_run=True 면 실제 카톡은 보내지 않고 나머지(한 번만 보내기·기록)만 똑같이 함 — 시험용."""
    if os.environ.get("NOCHIJIMA_TEST"):          # 시험 중(환경변수) → 실제 카톡은 절대 보내지 않음
        dry_run = True
    if not _claim(key, cooldown_min):
        return False, "같은 알림을 최근에 이미 보냄(또는 다른 감시가 보내는 중)"
    ok, note = False, ""
    try:
        if dry_run:
            ok, note = True, "(시험 — 실제 카톡은 보내지 않음)"
        else:
            from kakao_token import get_valid_token
            from 카톡발송 import send_memo
            ok, msg = send_memo(get_valid_token(), text[:200])
            note = "" if ok else str(msg)[:80]
    except Exception as e:
        note = f"{type(e).__name__}"
    if not ok:
        try:
            os.remove(_lock_path(key))                 # 실패했으면 잠금을 풀어 다음 실행이 다시 시도하게
        except OSError:
            pass
    _log(ok, key, text, note)
    return ok, note or "발송"
