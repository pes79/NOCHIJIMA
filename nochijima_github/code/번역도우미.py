# -*- coding: utf-8 -*-
"""
번역도우미.py — 구글 번역 호출을 한곳에서 관리합니다(정책뉴스감시·대형사고감시·재난감시가 같이 씀).

■ 왜 필요한가 : 2026-10-12, 번역을 짧은 시간에 너무 많이 불러 구글이 막았음(429 오류).
               막힌 동안 감시 프로그램이 제목을 번역하지 못해 기사를 놓칠 수 있었음.
■ 하는 일
  1) 번역 호출 사이에 간격(0.6초)을 둠
  2) 한 번 번역한 제목은 번역캐시.json 에 저장해 다시 부르지 않음(14일 보관)
  3) 막힌 걸 알면(429) 그 실행에서는 더 부르지 않음 → 감시 쪽이 '원문 제목을 바로 AI판단'으로 대신 처리
  4) 1시간 넘게 계속 막히면 운영자 카톡(나에게 보내기)으로 "번역 차단 중 — 감시 일부 지연" 한 번만 알림
     (풀리면 상태가 지워져서, 나중에 다시 막히면 또 한 번 알림)
■ 파일 : 번역캐시.json, 번역상태.json
"""
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE, "번역캐시.json")
STATE_FILE = os.path.join(BASE, "번역상태.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MIN_GAP = 0.6               # 번역 호출 사이 최소 간격(초)
CACHE_DAYS = 14
CACHE_MAX = 6000
ALERT_AFTER = 3600          # 이만큼(초) 계속 막히면 카톡 알림 1번
ALERT_TEXT = "⚠️ [놓치지마] 번역 차단 중 — 감시 일부 지연\n구글 번역이 1시간 넘게 막혀 있습니다. 정책·대형사고 감시는 원문 제목을 AI판단에 직접 물어 계속 돌아갑니다."

_cache = None
_dirty = False
_last_call = 0.0
_blocked_this_run = False
_fails_in_row = 0


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _get_cache():
    global _cache
    if _cache is None:
        _cache = _load(CACHE_FILE, {})
    return _cache


def state():
    return _load(STATE_FILE, {})


def _mark_blocked(why):
    global _blocked_this_run
    _blocked_this_run = True
    st = state()
    if not st.get("blocked_since"):
        st["blocked_since"] = time.time()
        st["alerted"] = False
    st["last_error"] = why
    st["last_check"] = time.time()
    _save(STATE_FILE, st)


def _mark_ok():
    st = state()
    if st.get("blocked_since") or not st.get("last_ok"):
        _save(STATE_FILE, {"blocked_since": None, "alerted": False, "last_ok": time.time(), "last_check": time.time()})


def is_blocked():
    """이번 실행에서 번역이 막힌 걸 확인했으면 True."""
    return _blocked_this_run


def _usage(status, lang):
    """구글 번역 호출 1번을 logs/번역_사용량_날짜.log 에 기록. status: 성공 / 실패(이유) / 차단(HTTP 코드) / 건너뜀(차단 중이라 안 부름)"""
    try:
        from datetime import datetime
        d = os.path.join(BASE, "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"번역_사용량_{datetime.now():%Y-%m-%d}.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{status}\t{lang}\n")
    except Exception:
        pass


def translate(text, src_lang):
    """한국어 번역. 실패(차단 포함)하면 None — 원문을 번역인 척 돌려주지 않음."""
    global _last_call, _dirty, _fails_in_row
    if not text:
        return ""
    cache = _get_cache()
    key = f"{(src_lang or '').split('-')[0].lower()}|{text}"
    hit = cache.get(key)
    if hit:
        return hit[0]
    if _blocked_this_run:
        _usage("건너뜀(차단 중)", src_lang)
        return None
    wait = MIN_GAP - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.time()
    try:
        url = ("https://translate.googleapis.com/translate_a/single"
               f"?client=gtx&sl={src_lang}&tl=ko&dt=t&q=" + quote(text))
        with urlopen(Request(url, headers=UA), timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ko = "".join(seg[0] for seg in data[0] if seg and seg[0])
    except HTTPError as e:
        if e.code in (429, 403, 503):
            _mark_blocked(f"HTTP {e.code}")
            _usage(f"차단(HTTP {e.code})", src_lang)
        else:
            _fails_in_row += 1
            _usage(f"실패(HTTP {e.code})", src_lang)
        return None
    except Exception as e:
        _fails_in_row += 1
        _usage(f"실패({type(e).__name__})", src_lang)
        if _fails_in_row >= 3:                       # 연결 문제가 이어지면 이번 실행은 그만 부름
            _mark_blocked(type(e).__name__)
        return None
    if not ko:
        _usage("실패(빈 답)", src_lang)
        return None
    _usage("성공", src_lang)
    _fails_in_row = 0
    cache[key] = [ko, int(time.time())]
    _dirty = True
    _mark_ok()
    return ko


def finish(alert=True):
    """실행 끝에 한 번: 캐시 저장 + (1시간 넘게 막혀 있으면) 카톡 알림 1번."""
    global _dirty
    if _dirty and _cache is not None:
        cut = time.time() - CACHE_DAYS * 86400
        keep = {k: v for k, v in _cache.items() if v[1] >= cut}
        if len(keep) > CACHE_MAX:
            keep = dict(sorted(keep.items(), key=lambda kv: kv[1][1])[-CACHE_MAX:])
        _save(CACHE_FILE, keep)
        _dirty = False
    st = state()
    since = st.get("blocked_since")
    if alert and _blocked_this_run and since and not st.get("alerted") and time.time() - since >= ALERT_AFTER:
        try:
            import 시스템알림
            ok, why = 시스템알림.send("번역 차단", ALERT_TEXT, cooldown_min=360)
        except Exception:
            ok, why = False, ""
        # 내가 보냈거나, 같은 시각에 도는 다른 감시가 이미 보냈으면 → 이번 차단에 대해서는 더 보내지 않음
        if ok or "이미 보냄" in why:
            st = state()
            st["alerted"] = True
            _save(STATE_FILE, st)
