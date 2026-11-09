# -*- coding: utf-8 -*-
"""
주간리포트.py  ── 일주일에 1번, 지난 7일 요약을 카톡으로 보냅니다.

내용: 그 나라에 지난 일주일간 있었던 지진·재난·정책 건수(+최대 규모)와 환율 변동 요약.
- 대상 나라는 카톡발송.py의 MY_COUNTRIES 설정을 그대로 씁니다.
- 보통 일요일 저녁에 자동 발송합니다.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta

from 카톡발송 import (MY_COUNTRIES, COUNTRY_TO_CURRENCY,
                   extract_blocks, bracket_name, block_hash, send_memo)
from 나라정보 import flag, COUNTRY_INFO
from kakao_token import get_valid_token
from 지역필터 import block_passes   # 내가 고른 지역만 집계(알림과 같은 기준)

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")
RATES_FILE = os.path.join(BASE, "last_rates.json")

CUR_KEY = {"달러": "FX_USDKRW", "엔화": "FX_JPYKRW", "유로": "FX_EURKRW"}
DAYS = 7   # 며칠치를 모을지


def week_files(prefix):
    """지난 DAYS일치 로그 파일 경로(존재하는 것만)."""
    today = datetime.now()
    paths = []
    for i in range(DAYS):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        p = os.path.join(LOG_DIR, f"{prefix}_{day}.log")
        if os.path.exists(p):
            paths.append(p)
    return paths


def gather(match_token, prefixes):
    """여러 날 로그에서 [토큰] 이 든 알림 블록을 모읍니다(중복 제거)."""
    seen, blocks = set(), []
    for p in prefixes:
        for path in week_files(p):
            for b in extract_blocks(path):
                if match_token in bracket_name(b) and block_passes(b, p):
                    h = block_hash(b)
                    if h not in seen:
                        seen.add(h); blocks.append(b)
    return blocks


def current_rate(country):
    """그 나라 통화의 현재 환율 문자열. 없으면 None."""
    cur = COUNTRY_TO_CURRENCY.get(country)
    if not cur or cur not in CUR_KEY:
        return cur, None
    try:
        with open(RATES_FILE, "r", encoding="utf-8") as f:
            val = json.load(f)["rates"][CUR_KEY[cur]]
        unit = "100엔" if cur == "엔화" else ("1달러" if cur == "달러" else "1유로")
        return cur, f"{unit} {val:,.0f}원"
    except Exception:
        return cur, None


def build_report(country, start, end):
    quakes = gather(country, ["지진감시"])
    disasters = gather(country, ["재난감시"])
    policy = gather(country, ["정책뉴스감시"])

    lines = [f"📅 주간 리포트 · {flag(country)} {country}",
             f"({start}~{end}, 한국시각)"]

    # 지진 (최대 규모 함께)
    q = f"🌍 지진: {len(quakes)}건"
    if quakes:
        mags = [float(m.group(1)) for b in quakes for m in [re.search(r"규모\s*(\d+\.\d)", b)] if m]
        if mags:
            q += f" (최대 규모 {max(mags):.1f})"
    lines.append(q)

    lines.append(f"🔥 재난: {len(disasters)}건")
    lines.append(f"📋 정책: {len(policy)}건")

    # 환율 (이번 주 변동 횟수 + 현재 시세)
    cur, rate = current_rate(country)
    if cur:
        fx = gather(cur, ["환율감시"])   # 환율은 [통화]로 매칭
        fx_line = f"💱 {cur}: 변동 {len(fx)}회"
        if rate:
            fx_line += f" · 현재 {rate}"
        lines.append(fx_line)

    lines.append("한 주도 안전하게 보내세요 🙌")
    return "\n".join(lines)


def main():
    if not MY_COUNTRIES:
        print("MY_COUNTRIES 가 비어 있습니다.")
        return
    try:
        token = get_valid_token()
    except Exception as e:
        print(f"❌ 토큰 문제: {e}")
        return

    today = datetime.now()
    start = (today - timedelta(days=DAYS - 1)).strftime("%-m/%-d") if os.name != "nt" \
            else f"{(today - timedelta(days=DAYS-1)).month}/{(today - timedelta(days=DAYS-1)).day}"
    end = f"{today.month}/{today.day}"

    print("[주간 리포트]")
    sent = 0
    for country in MY_COUNTRIES:
        if country not in COUNTRY_INFO:
            print(f"  (건너뜀: {country})")
            continue
        text = build_report(country, start, end)
        ok, msg = send_memo(token, text)
        if ok:
            sent += 1
            print(f"✅ 주간 리포트 발송: {country}")
            print("   " + text.replace("\n", "\n   "))
        else:
            print(f"❌ 실패({country}): {msg}")
    print(f"\n총 {sent}개국 발송 완료.")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
