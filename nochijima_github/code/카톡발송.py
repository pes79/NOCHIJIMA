# -*- coding: utf-8 -*-
"""
카톡발송.py  ── logs 폴더의 알림을 카카오톡 '나에게 보내기'로 발송합니다.

사용법(터미널):
  py 카톡발송.py test   → 테스트 메시지 1개만 보냄 (진짜 오는지 확인용)
  py 카톡발송.py        → 실제 알림 발송 (아직 안 보낸 새 알림만)

특징:
- 우리 발송 형식(🚨💱📋🚗🌏) 그대로. 긴 링크 줄은 빼서 200자 안에 맞춤.
- 알림 1건 = 카톡 1개 메시지.
- 이미 보낸 알림은 kakao_sent.json 에 기록해 다시 안 보냄.
- access 토큰이 만료되면 kakao_token.py 가 자동 갱신.
- 한 번에 너무 많이 안 보내도록 최대 개수 제한(스팸 방지).
"""

import glob
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from kakao_token import get_valid_token   # 토큰 자동 갱신 도우미
from 나라정보 import flag, COUNTRY_INFO    # 국기 이모지 매핑
from 지역필터 import block_passes, HIDDEN_PREFIXES, unplaced_local   # 내가 고른 지역만 · 숨은 줄(좌표·코드) 표시

# 통화 → 국기 (환율 알림용)
CUR_FLAG = {"엔화": "🇯🇵", "달러": "🇺🇸", "유로": "🇪🇺"}


def flag_for(name):
    """[...] 안 이름(나라/통화)에 맞는 국기. 못 찾으면 ''."""
    if name in CUR_FLAG:
        return CUR_FLAG[name]
    f = flag(name)                       # 한국어·영어 이름 정확 매칭
    if f != "🌍":
        return f
    for c in sorted(COUNTRY_INFO, key=len, reverse=True):   # 부분일치(예: '미국 알래스카')
        if c in name:
            return flag(c)
    return ""

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE, "logs")
SENT_FILE = os.path.join(BASE, "kakao_sent.json")

MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
# 메시지 아래에 붙는 링크 (우리 홈페이지)
LINK_URL = "https://dainty-creponne-765c4f.netlify.app"

# 한 번 실행할 때 보낼 최대 알림 수 (스팸 방지). 나머지는 조용히 '보낸 것'으로 처리.
MAX_SEND = 6

# ★ 하루 총 발송 상한 (스팸·비용·스트레스 방지). 이 숫자만 바꾸면 됩니다.
#   상한을 넘으면 즉시 발송은 멈추고, 나머지는 아침/저녁 브리핑에 요약됩니다.
#   단, 🚨🚨(최고위험: 큰 지진·토네이도·특별경보)는 상한과 무관하게 항상 발송.
DAILY_LIMIT = 6
COUNTER_FILE = os.path.join(BASE, "발송카운터.json")

# ★ 야간 방해금지: 이 시간엔 즉시 발송 안 함. 나머지는 아침 브리핑으로 모아서.
#   단, 최고위험(🚨🚨: 대형 지진·특별경보·토네이도 경보)은 밤에도 예외로 즉시 발송.
#   QUIET_ENABLED=False 로 두면 방해금지 없이 밤에도 모든 알림을 즉시 받습니다.
QUIET_ENABLED = False   # ← 야간 방해금지 해제(밤에도 다 받음). 다시 켜려면 True.
QUIET_START = 23   # 밤 11시부터
QUIET_END = 7      # 아침 7시까지 (이 시각 전까지 조용)


def _is_quiet_now():
    """지금이 야간 방해금지 시간대인지 (컴퓨터 시각=한국시각 기준)."""
    if not QUIET_ENABLED:
        return False                            # 방해금지 해제 → 밤에도 즉시 발송
    h = datetime.now().hour
    if QUIET_START <= QUIET_END:
        return QUIET_START <= h < QUIET_END
    return h >= QUIET_START or h < QUIET_END   # 자정을 넘는 구간(23~7)

# ── ★ 관심 나라 설정 ─────────────────────
#  평소 구독 나라(기본값). 임시 체험은 구독설정.json으로 기간을 정해 덮어씀.
DEFAULT_COUNTRIES = ["일본", "미국", "말레이시아"]
SUB_FILE = os.path.join(BASE, "구독설정.json")


def _load_countries():
    """관심 나라 목록을 정합니다.
    구독설정.json에 임시 체험(override)이 있고 만료시각 전이면 그 목록,
    지나갔으면(또는 없으면) 자동으로 기본 목록으로 되돌아갑니다."""
    try:
        with open(SUB_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        ov = cfg.get("override") or {}
        until, countries = ov.get("until"), ov.get("countries")
        if until and countries:
            if datetime.now() < datetime.strptime(until, "%Y-%m-%d %H:%M:%S"):
                return countries          # 체험 기간 중 → 임시 목록 사용
    except Exception:
        pass
    return DEFAULT_COUNTRIES               # 기본(평소) 목록으로 복귀

# 매 실행마다 현재 시각 기준으로 계산 → 만료시각 지나면 자동으로 기본값 복귀
MY_COUNTRIES = _load_countries()

# 나라 → 그 나라 통화 (환율 알림 매칭용). 이름은 환율감시.py의 통화 이름과 똑같이.
COUNTRY_TO_CURRENCY = {
    "미국": "미국달러", "일본": "엔화", "말레이시아": "링깃",
    "영국": "파운드", "호주": "호주달러", "캐나다": "캐나다달러", "스위스": "스위스프랑",
    "싱가포르": "싱가포르달러", "태국": "바트", "대만": "대만달러", "필리핀": "필리핀페소",
    "인도네시아": "루피아", "베트남": "베트남동", "인도": "인도루피", "브라질": "헤알",
    "멕시코": "멕시코페소", "뉴질랜드": "뉴질랜드달러", "러시아": "루블", "스웨덴": "크로나",
    "튀르키예": "리라", "폴란드": "즈워티",
    # 유로존 나라들은 모두 '유로'
    "독일": "유로", "프랑스": "유로", "이탈리아": "유로", "스페인": "유로",
    "네덜란드": "유로", "벨기에": "유로", "오스트리아": "유로", "포르투갈": "유로",
    # 아르헨티나(페소)는 네이버가 환율을 주지 않아 제외
}

# 감시 프로그램들의 로그 파일 앞이름
#  ※ '대형사고감시'는 뉴스 기반이라 오발송이 잦음 → 테스트 중 계속 필터 다듬는 중(서버에서 AI 판단으로 완성 예정).
PREFIXES = ["환율감시", "지진감시", "재난감시", "정책뉴스감시", "일본기상청감시", "호우감시", "미국기상청감시", "대형사고감시",
            "말레이시아기상청감시"]

# 알림(보낼 것)의 첫 글자. 🟢(소식없음/소폭)은 제외.
#  🌧️ = 일본 기상청 '호우/홍수 주의보'(경보보다 낮은 초기 단계)
ALERT_STARTS = ("🚨", "💱", "📋", "🚗", "🌏", "🌧️")
ALL_STARTS = ALERT_STARTS + ("🟢",)


def recent_logs(prefix):
    """해당 프로그램의 최근 로그 파일 2개(오늘+어제)를 돌려줍니다.
    자정을 넘기거나 PC를 껐다 켜도 놓치지 않도록 이틀치를 함께 읽습니다."""
    files = sorted(glob.glob(os.path.join(LOG_DIR, f"{prefix}_*.log")))
    return files[-2:]   # 파일명이 날짜라, 뒤에서 2개 = 어제+오늘


def extract_blocks(path):
    """로그에서 알림 블록만 뽑아냅니다 (실행시작/끝·구분선 제외)."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks, cur = [], []
    for line in content.splitlines():
        s = line.strip()
        is_wrapper = s.startswith("=") or s.startswith("[실행")
        is_blank = (s == "")
        starts_new = any(s.startswith(e) for e in ALL_STARTS)
        if is_wrapper or is_blank:
            if cur:
                blocks.append("\n".join(cur)); cur = []
            continue
        if starts_new and cur:
            blocks.append("\n".join(cur)); cur = []
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))

    # 진짜 알림(🟢 제외)만 남기기
    return [b for b in blocks if any(b.lstrip().startswith(e) for e in ALERT_STARTS)]


# 재난 알림(지진·폭우·재난경보·기상특보·대형사고) 맨 끝에 붙는 한 줄. 환율·정책 알림에는 안 붙음.
DISASTER_CLOSING = "자녀분께 안부를 한번 물어보세요."


def is_disaster_block(block):
    """재난 알림이면 True. (💱 환율, 📋 비자·정책, 🚗 생활 제도는 False)"""
    s = (block or "").lstrip()
    first = s.splitlines()[0] if s else ""
    if not s.startswith(("🚨", "🌧")):
        return False
    if "환율" in first or "📋" in first or "🚗" in first:
        return False
    if "비 예보" in first:              # 폭우 '예보'(아직 실제로 온 비가 아님)에는 안부 문구를 붙이지 않음
        return False
    return True


def to_kakao_text(block):
    """카톡용으로 다듬기: 긴 링크 줄 제거 + 200자 이내. 재난 알림은 맨 끝에 안부 한 줄."""
    disaster = is_disaster_block(block)
    keep = []
    for i, line in enumerate(block.splitlines()):
        s = line.strip()
        if s.startswith(HIDDEN_PREFIXES):
            continue   # 지역 필터용 숨은 줄(좌표·도도부현 코드) — 카톡엔 절대 안 보이게
        if s.startswith("자세히 ▶") or s.startswith("링크:"):
            continue   # 너무 긴 URL 줄은 뺌
        # 옛 재난 로그의 중복 제목줄 제거(예: '일본의 녹색 홍수 경보') — 머리글과 중복+등급단어
        if i > 0 and s.endswith("경보") and re.search(r"녹색|주황|적색|오렌지", s):
            continue
        # 재난 알림은 맨 끝에 안부 한 줄이 따로 붙으므로, 본문의 옛 '가족 안부 확인' 줄은 뺌(같은 말 두 번 방지)
        if disaster and i > 0 and "가족 안부" in s:
            continue
        keep.append(line)
    # 첫 줄(제목)의 '이미지·사진 :' 접두어와 '：언론사' 꼬리표 제거 (오래된 로그 대응)
    if keep:
        keep[0] = re.sub(r"(\]\s*)(?:이미지|사진|영상|포토|그래픽|자료사진)[\s·・/,]*(?:(?:이미지|사진|영상|포토|그래픽)[\s·・/,]*)*[:：]\s*", r"\1", keep[0])
        keep[0] = re.sub(r"\s*：\s*[^：]{1,20}$", "", keep[0]).rstrip()
        # [나라] → 🇯🇵 나라 (국기 이모지)
        #  ※ 국기는 '첫 단어(나라)'로만 찾음. '미국 아이다호주'의 '아이다호주'가
        #    '호주'로 오인돼 🇦🇺가 붙던 문제 방지.
        def _fl(m):
            nm = m.group(1)
            first = nm.split()[0].rstrip(",") if nm.split() else nm
            fl = flag_for(first) or flag_for(nm)
            return f"{fl} {nm}" if fl else m.group(0)
        keep[0] = re.sub(r"\[([^\]]+)\]", _fl, keep[0], count=1)
    text = "\n".join(keep).strip()
    # 옛 문구를 새(따뜻한) 문구로 자동 교체 (이미 로그에 저장된 알림도 최신 문구로)
    text = text.replace("해당되면 미리 준비해두세요", "관련 있다면 미리 확인해보세요")
    text = text.replace("현지 거주자는 확인해보세요", "현지 거주 중이면 확인해보세요")
    if disaster:
        # 안부 문구가 200자 제한에 잘리지 않도록, 본문을 먼저 줄이고 안부 줄은 항상 온전히 붙임
        room = 200 - len(DISASTER_CLOSING) - 1
        if len(text) > room:
            text = text[:room - 1].rstrip() + "…"
        return text + "\n" + DISASTER_CLOSING
    if len(text) > 200:
        text = text[:197] + "…"
    return text


def block_hash(block):
    return hashlib.md5(block.encode("utf-8")).hexdigest()[:16]


def bracket_name(block):
    """알림 첫 줄의 [...] 안 내용(나라 또는 통화)을 꺼냅니다."""
    first = block.splitlines()[0] if block else ""
    m = re.search(r"\[([^\]]+)\]", first)
    return m.group(1) if m else ""


def passes_filter(block):
    """MY_COUNTRIES 에 맞는 알림만 True. 빈 목록이면 전부 통과."""
    if not MY_COUNTRIES:
        return True
    br = bracket_name(block)                 # 예: "일본", "미국 알래스카", "엔화"
    for country in MY_COUNTRIES:
        if country in br:                    # 지진·재난·정책 (나라명으로 매칭)
            return True
        cur = COUNTRY_TO_CURRENCY.get(country)
        if cur and cur in br:                # 환율 (그 나라 통화로 매칭)
            return True
    return False


def is_urgent(block):
    """즉시 개별 발송 대상이면 True. 아니면 브리핑에만 묶임.
    ● 즉시(긴급): 지진 4.0+ / 재난 '경보'급(GDACS Orange↑·일본기상청 경보) / 환율 급변 3%↑
    ● 브리핑에만: 정책뉴스 / 소규모 재난(GDACS Green) / 일반 환율(1~3%) / 호우 주의보"""
    s = block.lstrip()
    first = s.splitlines()[0] if s else ""
    if s.startswith("🚨🚨"):          # 최고위험(Red 재난·규모6+·환율급변·호우 특별경보)
        return True
    if "지진 발생" in first:            # 지진은 전부 규모 4.0+ → 즉시
        return True
    if s.startswith("🚨"):
        if "🟢" in first or "소규모" in first:   # GDACS Green(소규모 재난) → 브리핑
            return False
        return True                    # 🟠 중대/🔴 대형 재난·일본 기상청 경보 → 즉시
    # 💱 환율 '○일 만에 최저/최고' → 송금 타이밍 정보라 바로(환율감시가 같은 흐름에선 한 번만 냄)
    if s.startswith("💱") and re.search(r"일 만에 (최저|최고)", first):
        return True
    # 🌧️ 폭우 '예보': 예상 50mm 이상(호우급)만 바로. 30~50mm 예보는 실제 '비 많음'처럼 브리핑에만.
    if s.startswith("🌧") and "비 예보" in first:
        m = re.search(r"예상 약\s*(\d+)\s*mm", first)
        return bool(m) and int(m.group(1)) >= 50
    # 💱 일반 환율 변동, 🌧️ 호우 주의보, 📋 비자·🚗 생활 정책 → 즉시 발송 안 함(브리핑에만)
    return False


def load_day_count():
    """오늘 이미 보낸 알림 수. 날짜가 바뀌면 0부터."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(COUNTER_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d.get("count", 0) if d.get("date") == today else 0
    except Exception:
        return 0


def save_day_count(count):
    try:
        with open(COUNTER_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": datetime.now().strftime("%Y-%m-%d"), "count": count},
                      f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_sent():
    if not os.path.exists(SENT_FILE):
        return set()
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("sent", []))
    except Exception:
        return set()


def save_sent(sent):
    ids = list(sent)[-1000:]
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "sent": ids},
                  f, ensure_ascii=False, indent=2)


def send_memo(access_token, text):
    """카카오톡 나에게 보내기. (성공여부, 메시지) 반환."""
    template = {
        "object_type": "text",
        "text": text,
        "link": {},   # 빈 링크: 홈페이지 버튼·"모바일에서 확인" 문구 제거
    }
    body = urlencode({"template_object": json.dumps(template, ensure_ascii=False)}).encode("utf-8")
    req = Request(MEMO_URL, data=body,
                  headers={"Authorization": "Bearer " + access_token,
                           "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"})
    try:
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("result_code") == 0), str(data)
    except HTTPError as e:
        return False, e.read().decode("utf-8", "replace")
    except Exception as e:
        return False, str(e)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "real"

    # 토큰 준비 (필요하면 자동 갱신)
    try:
        token = get_valid_token()
    except Exception as e:
        print(f"❌ 토큰 문제: {e}")
        return

    # ── 테스트 모드: 메시지 1개만 ──
    if mode == "test":
        text = ("🔔 놓치지마 카톡 연결 테스트\n"
                "이 메시지가 보이면 발송이 정상입니다!\n"
                "앞으로 환율·지진·재난·정책 알림을 여기로 보내드려요.")
        ok, msg = send_memo(token, text)
        print("✅ 테스트 메시지 발송 성공! 카톡(나와의 채팅)을 확인해보세요." if ok
              else f"❌ 발송 실패: {msg}")
        return

    # ── 실제 모드: 로그의 새 알림 발송 ──
    who = "·".join(MY_COUNTRIES) if MY_COUNTRIES else "전체 나라"
    print(f"[관심 나라: {who}]")

    candidates = []
    seen_block = set()   # 같은 알림이 어제·오늘 파일에 겹쳐 있으면 한 번만
    prefix_of = {}       # 알림 → 어느 감시에서 왔는지(지역 필터가 종류 판단에 씀)
    for prefix in PREFIXES:
        for path in recent_logs(prefix):   # 오늘+어제 로그 함께 읽기
            for b in extract_blocks(path):
                h = block_hash(b)
                if h not in seen_block:
                    seen_block.add(h)
                    candidates.append(b)
                    prefix_of[h] = prefix

    # 관심 나라 필터 적용
    candidates = [b for b in candidates if passes_filter(b)]
    # 지역(도시) 필터 적용 — 지역설정.json. 꺼져 있거나 '전체'면 그대로 통과.
    candidates = [b for b in candidates if block_passes(b, prefix_of.get(block_hash(b), ""))]

    sent = load_sent()
    # ★ 즉시 발송은 '긴급'만. 나머지(정책·소규모재난·일반환율·주의보)는 브리핑이 담당.
    new = [b for b in candidates if block_hash(b) not in sent and is_urgent(b)]
    # 대형사고·산사태인데 장소 좌표가 없으면(내 지역 근처인지 모름) 즉시 카톡 대신 브리핑에만 — 2026-10-15
    #  (브리핑은 로그를 따로 읽으므로 여기서 '보낸 것'으로 표시해도 브리핑에는 그대로 들어감)
    unplaced = [b for b in new if unplaced_local(b, prefix_of.get(block_hash(b), ""))]
    if unplaced:
        for b in unplaced:
            sent.add(block_hash(b))
            print(f"  ↪ 장소 좌표 없음 → 카톡 대신 브리핑에만: {b.splitlines()[0]}")
        save_sent(sent)
        new = [b for b in new if b not in unplaced]

    if not new:
        print(f"{who} 즉시 발송할 긴급 알림이 없습니다.")
        print("  (정책·소규모 재난·일반 환율은 아침/저녁 브리핑으로 발송됩니다)")
        return

    # ★ 우선 발송: 🚨🚨(큰 재난 Red·규모 6.0+ 지진)은 항상 맨 앞 + 개수제한에도 안 밀림
    priority = [b for b in new if b.lstrip().startswith("🚨🚨")]
    normal = [b for b in new if not b.lstrip().startswith("🚨🚨")]
    normal_send = normal[-MAX_SEND:]        # 일반 알림은 최대 MAX_SEND건
    to_send = priority + normal_send        # 최우선을 앞에 배치
    skipped = len(normal) - len(normal_send)

    day_count = load_day_count()   # 오늘 이미 보낸 수
    quiet = _is_quiet_now()        # 지금이 야간 방해금지 시간인지
    sent_count = 0
    capped = 0
    held = 0
    failed = False
    for b in to_send:
        is_top = b.lstrip().startswith("🚨🚨")   # 최고위험(항상 즉시)
        if not is_top:
            # 야간 방해금지: 밤엔 최고위험 아니면 아침 브리핑으로 미룸
            if quiet:
                sent.add(block_hash(b))
                held += 1
                continue
            # 하루 상한 초과분도 브리핑으로 (최고위험은 상한과 무관하게 발송)
            if day_count >= DAILY_LIMIT:
                sent.add(block_hash(b))
                capped += 1
                continue
        text = to_kakao_text(b)
        if is_top:                            # 최우선 표시
            text = ("⚡ 긴급 최우선\n" + text)[:200]
        ok, msg = send_memo(token, text)
        if ok:
            sent_count += 1
            day_count += 1
            sent.add(block_hash(b))          # ★ 실제로 보낸 것만 기록
            print(f"  ✅ 보냄: {b.splitlines()[0]}")
            # 보낸 문장 전체도 기록(일일점검이 읽음). 줄마다 '     | ' 를 붙여 다른 기록과 구분.
            print("\n".join("     | " + ln for ln in text.splitlines()))
            _lk = re.search(r"자세히 ▶ (\S+)", b)
            if _lk:
                print(f"     | (원문 링크) {_lk.group(1)}")
        else:
            print(f"  ❌ 실패: {msg}")
            failed = True
            break   # 실패하면 멈춤 — 아직 못 보낸 건 기록 안 함 → 다음 주기에 재시도
        time.sleep(0.6)   # 카카오 서버 배려

    save_day_count(day_count)
    save_sent(sent)

    print(f"\n총 {sent_count}건 발송 완료. (오늘 누적 {day_count}/{DAILY_LIMIT})")
    if failed:
        print("(발송 실패분은 기록하지 않았습니다 — 다음 주기에 다시 보냅니다.)")
    if held:
        print(f"(야간 방해금지: {held}건은 아침 브리핑으로 보냅니다. 최고위험만 밤에도 즉시.)")
    if capped:
        print(f"(하루 상한 초과 {capped}건은 즉시 발송 대신 브리핑으로 안내됩니다.)")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
