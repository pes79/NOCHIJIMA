# -*- coding: utf-8 -*-
"""
입국·여행경보 변경 감지 프로그램 (샘플)
- 소스: 미국 국무부(State Dept) 공식 여행경보 RSS  (무료 · API키 불필요)
  주소: https://travel.state.gov/_res/rss/TAsTWs.xml
- 전 세계 200개국+ 중, 내가 감시할 나라만 골라서 봅니다.
- 각 나라의 '경보 레벨'과 '변경 날짜'를 한국어로 정리합니다.
- 지난번과 비교해 변경 날짜가 바뀌면 "⚠️ 변경 감지"로 알려줍니다.
- 이전 상태는 같은 폴더의 'visa_state.json'에 저장됩니다.

※ 주의: 이 소스는 '미국 정부가 보는' 각국 입국·안전 정보입니다.
   (각국 이민국 원문이 아니라, 자동수집이 되는 유일한 공식 소스라 샘플로 사용)
"""

import json
import os
import re
import sys
from datetime import datetime
from urllib.request import Request, urlopen

# ── 설정 ─────────────────────────────────────────────
RSS_URL = "https://travel.state.gov/_res/rss/TAsTWs.xml"

# 감시할 나라 (왼쪽=RSS에서 찾을 영문 키워드, 오른쪽=화면에 쓸 한국어 이름)
#  ※ 이름이 정확히 같지 않아도, 이 키워드가 제목에 들어있으면 찾습니다.
#     (예: "China" -> "Mainland China, Hong Kong & Macau" 항목을 찾아냄)
WATCH_COUNTRIES = {
    "Japan": "일본",
    "Vietnam": "베트남",
    "Thailand": "태국",
    "China": "중국",
    "Philippines": "필리핀",
}

# 경보 레벨 한국어 번역표 (미국 국무부 4단계 공통 문구 → 깔끔한 한국어)
LEVEL_KR = {
    1: "🟢 레벨1 · 정상적인 주의 (여행 무난)",
    2: "🟡 레벨2 · 주의 강화",
    3: "🟠 레벨3 · 여행 재고(가급적 자제)",
    4: "🔴 레벨4 · 여행 금지(가지 마세요)",
}

SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "visa_state.json")


def fetch_advisories():
    """국무부 RSS를 가져와 {영문나라명: {level, level_text, date, link}} 로 정리."""
    req = Request(RSS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=25) as response:
        raw = response.read().decode("utf-8", errors="replace")

    result = {}
    # <item> ... </item> 단위로 잘라서 파싱 (외부 라이브러리 없이)
    for block in re.findall(r"<item>(.*?)</item>", raw, re.DOTALL):
        title = _tag(block, "title")   # 예: "Japan - Level 1: Exercise Normal Precautions"
        if not title:
            continue
        link = _tag(block, "link")
        date = _tag(block, "pubDate")

        # "나라이름 - Level N: ..." 형태에서 나라명과 레벨 숫자 뽑기
        m = re.match(r"^(.*?)\s*-\s*Level\s*(\d)", title)
        if not m:
            continue
        country = m.group(1).strip()
        level = int(m.group(2))

        result[country] = {
            "level": level,
            "level_text": LEVEL_KR.get(level, title),
            "date": (date or "").strip(),
            "link": (link or "").strip(),
        }
    return result


def _tag(block, name):
    """block 안에서 <name>...</name> 내용을 꺼냅니다 (CDATA도 처리)."""
    m = re.search(rf"<{name}>(.*?)</{name}>", block, re.DOTALL)
    if not m:
        return ""
    val = m.group(1)
    val = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", val, flags=re.DOTALL)
    return val.strip()


def load_state():
    if not os.path.exists(SAVE_FILE):
        return {}
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("countries", {})
    except Exception:
        return {}


def save_state(current_watch):
    payload = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "countries": current_watch,
    }
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    print("=" * 54)
    print("     🛂  입국·여행경보 변경 감지 (미국 국무부 소스)")
    print("=" * 54)
    print(f"조회 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 54)

    try:
        all_advisories = fetch_advisories()
    except Exception as e:
        print("❌ 정보를 가져오는 중 문제가 생겼습니다.")
        print(f"   ({e})")
        print("   인터넷 연결을 확인한 뒤 다시 실행해 주세요.")
        return

    previous = load_state()      # 지난번 저장 상태
    current_watch = {}           # 이번에 감시대상 나라만 추림
    change_count = 0

    for eng_name, kor_name in WATCH_COUNTRIES.items():
        # 1) 이름이 정확히 같은 항목을 먼저 찾고,
        # 2) 없으면 키워드가 제목에 '포함'된 항목을 찾습니다.
        info = all_advisories.get(eng_name)
        if not info:
            for country, data in all_advisories.items():
                if eng_name.lower() in country.lower():
                    info = data
                    break
        if not info:
            print(f"● {kor_name}({eng_name}) : (소스에서 항목을 찾지 못함)")
            print()
            continue

        current_watch[eng_name] = info
        prev = previous.get(eng_name)

        # 변경 감지: 지난번과 '변경 날짜'가 다르면 바뀐 것
        changed = prev is not None and prev.get("date") != info["date"]
        first_time = prev is None

        header = f"● {kor_name} ({eng_name})"
        if changed:
            header += "   ⚠️ 변경 감지!"
            change_count += 1
        print(header)
        print(f"    현재 경보: {info['level_text']}")
        print(f"    갱신 날짜: {info['date']}")
        if changed:
            old_level = prev.get("level")
            print(f"    (이전 레벨: {old_level} → 현재 레벨: {info['level']})")
        elif first_time:
            print("    (처음 기록 — 다음 실행부터 변경을 비교합니다)")
        print(f"    공식 링크: {info['link']}")
        print()

    print("-" * 54)
    if change_count:
        print(f"🔔 이번에 {change_count}개 나라에서 변경이 감지되었습니다.")
    else:
        print("변경된 나라가 없습니다. (모두 지난번과 동일) ✅")

    save_state(current_watch)
    print("현재 상태를 저장했습니다. 다음 실행 때 비교됩니다. ✅")
    print("=" * 54)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
