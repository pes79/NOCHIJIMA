# -*- coding: utf-8 -*-
"""
대형사고감시.py — '대형 인명사고'(항공기 추락·붕괴·폭발·침몰·탈선·테러 등)를 감시.

■ 왜 필요한가
  - 자연재해(지진·화산·태풍)와 달리 '비행기 추락' 같은 대형 사고는 GDACS에 안 올라옵니다.
  - 구독국에서 생명이 걸린 대형 사고가 나면, 가족 안부 확인용으로 즉시 알립니다.

■ '대형'으로 인정하는 기준 (좁게 — 노이즈 방지)
  (1) 확실한 대형 유형어(여객기 추락·건물 붕괴·여객선 침몰·열차 탈선·참사·테러 등) → 바로 인정
  (2) 일반 유형어(폭발·화재·붕괴·충돌·추락·침몰·탈선) + 규모/인명 단어(사망·사상자·수십 명·대형)
  → 개별 교통사고·소규모 화재·범죄·스포츠(선수 충돌 등)는 제외.

■ 산사태(2026-10-12 추가) : 나라별 언어로 따로 검색 → 사망·실종·부상 1명 이상이거나 실제 대피가 있는 것만(AI판단 '산사태').
■ 소스 : 구글뉴스 RSS(나라 언어) + 정책뉴스감시의 '신뢰 언론 화이트리스트' 재사용.
■ 중복 : incident_seen.json 에 기록해 다시 알리지 않음.
■ 2단계 : 키워드 통과 기사는 AI판단.py(제미나이)가 "지금·이 나라·대형 인명사고"인지 최종 확인.
         (gemini_config.json 의 enabled=false 면 예전처럼 키워드만)
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from urllib.request import Request, urlopen

from 나라정보 import local_kst_str
import 정책뉴스감시 as pol   # COUNTRIES(신뢰소스·언어)·번역·유틸 재사용
import AI판단 as ai          # 2단계: 제미나이 최종 판단(확실할 때만 발송)
import 지역필터 as rf         # 사고 위치 → 좌표(내 지역 근처만 보내기)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "incident_seen.json")
MAX_CANDIDATES = 12   # 나라별 번역 후보 상한(번역 호출 제한)
MAX_AGE_DAYS = 3      # 이보다 오래된 기사는 무시(옛 사건 재등장 방지)


def is_recent(pub):
    """기사 발행이 최근 MAX_AGE_DAYS일 이내면 True. (날짜 파싱 실패 시 통과)"""
    from datetime import datetime, timezone
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() <= MAX_AGE_DAYS * 86400
    except Exception:
        return True

# 나라 언어별 '사고' 검색어(구글뉴스용). 목록에 없는 언어는 영어로.
INCIDENT_QUERY = {
    "ja": "墜落 OR 爆発 OR 崩落 OR 倒壊 OR 沈没 OR 脱線 OR テロ OR 航空機事故",
    "en": '"plane crash" OR explosion OR "building collapse" OR "ferry sinking" OR "train derailment" OR "terror attack"',
    "vi": '"rơi máy bay" OR "nổ lớn" OR sập OR "chìm phà" OR "trật bánh" OR "khủng bố"',
    "zh": "墜機 OR 空難 OR 爆炸 OR 倒塌 OR 沉沒 OR 出軌 OR 恐怖攻擊",
    "th": "เครื่องบินตก OR ระเบิด OR ถล่ม OR เรือล่ม OR ตกราง OR ก่อการร้าย",
    "id": '"pesawat jatuh" OR ledakan OR runtuh OR "kapal tenggelam" OR tergelincir OR teror',
    "de": "Flugzeugabsturz OR Explosion OR Einsturz OR Fährunglück OR Zugunglück OR Terror",
    "fr": "\"crash d'avion\" OR explosion OR effondrement OR naufrage OR déraillement OR attentat",
    "it": '"aereo precipitato" OR esplosione OR crollo OR naufragio OR deragliamento OR attentato',
    "es": '"avión estrellado" OR explosión OR derrumbe OR naufragio OR descarrilamiento OR atentado',
    "nl": "vliegtuigcrash OR explosie OR instorting OR schipbreuk OR ontsporing OR aanslag",
    "pt": '"queda de avião" OR explosão OR desabamento OR naufrágio OR descarrilamento OR atentado',
    "ru": "авиакатастрофа OR взрыв OR обрушение OR крушение OR теракт",
    "sv": "flygkrasch OR explosion OR kollaps OR förlisning OR tågolycka OR terror",
    "tr": "\"uçak kazası\" OR patlama OR çökme OR \"gemi battı\" OR \"tren kazası\" OR terör",
    "pl": '"katastrofa lotnicza" OR wybuch OR zawalenie OR zatonięcie OR wykolejenie OR zamach',
}


# 나라 언어별 '산사태' 검색어(구글뉴스용). 지원 30개국만. 목록에 없는 언어는 영어로.
LANDSLIDE_QUERY = {
    "ja": "土砂崩れ OR 土石流 OR 地すべり OR がけ崩れ",
    "en": 'landslide OR mudslide OR "debris flow"',
    "vi": '"sạt lở đất" OR "sạt lở núi" OR "lũ bùn"',
    "zh": "土石流 OR 山崩 OR 坍方 OR 走山",
    "th": "ดินถล่ม OR โคลนถล่ม",
    "id": '"tanah longsor" OR longsor',
    "de": "Erdrutsch OR Murenabgang OR Hangrutsch",
    "fr": '"glissement de terrain" OR "coulée de boue" OR éboulement',
    "it": 'frana OR smottamento OR "colata di fango"',
    "es": '"deslizamiento de tierra" OR deslave OR "corrimiento de tierra" OR alud',
    "nl": "aardverschuiving OR modderstroom",
    "pt": '"deslizamento de terra" OR deslizamento OR desmoronamento',
    "ru": 'оползень OR сель OR "сход селя"',
    "sv": "jordskred OR lerskred",
    "tr": 'heyelan OR "toprak kayması"',
    "pl": 'osuwisko OR "lawina błotna"',
}
# 산사태 기사는 번역기를 거치지 않고(번역 호출이 많으면 구글이 막음) 원문 제목 그대로 AI판단 '산사태'에 물어봄.
#  영어권은 1차로 '피해 단어'가 있는 제목만(AI 호출 줄이기). 그 외 언어는 나라별 최신 몇 건만.
LANDSLIDE_MAX = 5      # 나라별로 한 번에 AI에 물어볼 최대 기사 수
_EN_HARM = re.compile(r"\b(dead|death|deaths|dies?|died|kill(?:s|ed)?|missing|buried|trapped|injur\w*|hurt|"
                      r"evacuat\w*|rescu\w*|casualt\w*|victims?|bodies|body)\b", re.IGNORECASE)
_EN_NOISE = re.compile(r"landslide (?:win|victory|election|defeat|majority)|wins? (?:by|in) a landslide", re.IGNORECASE)


def q_for_landslide(lang):
    return LANDSLIDE_QUERY.get((lang or "en").split("-")[0].lower(), LANDSLIDE_QUERY["en"])


def is_recent_48h(pub):
    """산사태용: 기사 발행이 48시간 이내면 True. (날짜를 못 읽으면 제외 — 옛 사건 방지)"""
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() <= 48 * 3600
    except Exception:
        return False


def landslide_candidates(items, country, seen=None, recent=is_recent_48h, limit=LANDSLIDE_MAX):
    """검색 결과 → AI에 물어볼 후보(신뢰 언론·최근·아직 안 본 것). 제목은 원문 그대로."""
    out = []
    for it in items:
        if not pol.is_trusted(it, country["trusted"]) or not recent(it["pub"]):
            continue
        if seen is not None and it["guid"] in seen:
            continue
        if (country["lang"] or "").lower().startswith("en"):
            if _EN_NOISE.search(it["title"]) or not _EN_HARM.search(it["title"]):
                continue
        it["ko"] = it["title"]                # 번역 없이 원문 — 한국어 한 줄은 AI가 summary 로 써 줌
        it["country"] = country["name_kr"]
        out.append(it)
        if len(out) >= limit:
            break
    return out


def q_for(lang):
    return INCIDENT_QUERY.get((lang or "en").split("-")[0].lower(), INCIDENT_QUERY["en"])


# ── '대형' 판정 (한국어 번역 제목 기준) ──────────────────────────
# (1) 확실한 대형 유형 — 이 단어가 있으면 바로 대형 인정
#  ※ '테러·총격'은 회고성(추도·주년) 기사가 많아 여기서 빼고, 아래 (2)에서 인명 단어와 함께일 때만 인정.
#  ※ '비행기 추락'은 개인 경비행기(정치인 등) 소형사고가 많아 뺌 → 아래 (2)에서 인명 단어와 함께일 때만.
ALWAYS_MAJOR = [
    "여객기", "항공기 추락", "항공기 사고", "공중분해",
    "여객선 침몰", "페리 침몰", "선박 침몰", "여객선 전복",
    "건물 붕괴", "다리 붕괴", "교량 붕괴", "구조물 붕괴", "붕괴 사고",
    "열차 탈선", "지하철 탈선", "고속철", "참사",
]
# (2) 일반 유형 — 규모/인명 단어와 '함께' 있을 때만 대형 인정
SCALE_TYPE = ["폭발", "화재", "붕괴", "충돌", "추락", "침몰", "탈선", "무너", "가스 누출",
              "테러", "총격", "총기 난사", "폭탄"]
SCALE_WORD = ["사망", "숨져", "숨진", "숨졌", "사상자", "명 사상", "수십 명",
              "수백 명", "다수", "참사", "대형", "떼죽음", "실종", "희생", "매몰",
              "부상", "피습", "인질"]

# 유형어가 겹치지만 사고가 아닌 것(스포츠·연예·경제) — 제외
NOISE = ["축구", "야구", "농구", "경기", "선수", "감독", "드라마", "영화", "예능",
         "주가", "증시", "코스피", "흥행", "리뷰", "게임", "콘서트", "매출",
         "폭발적", "인기 폭발", "논란", "출시", "공개",
         # 스포츠·연예(유형어가 은유로 쓰임 — 예: 골프 '대폭발') — 밤사이 사례로 추가
         "골프", "우승", "메이저 대회", "리그", "드러머", "밴드", "가수", "배우",
         "래퍼", "아이돌", "앨범", "공연", "데뷔",
         # 소형·경미 사고(대형 인명사고 아님)
         "경미", "가벼운 부상", "생존한", "무사히",
         # 무인기·군용(사람이 안 타 인명 피해 없음 / 민간 무관) — 글로벌호크 사례로 추가
         "무인", "드론", "정찰기", "자위대", "군용기", "전투기", "헬기 훈련",
         # 사고 '후일담'(사고 자체가 아니라 조언·심리·소송 등)
         "심리학자", "조언", "트라우마",
         # 법률 후속(과거 사고의 소송·재판 기사 — 지금 난 사고가 아님)
         "고소", "소송", "제소", "손해배상", "부당 사망", "재판", "판결", "배상 청구",
         # 회고·추모성(실제 사고가 아니라 '지난 사건 기념') — 9.11 25주년 사례로 추가
         "추도", "추모", "추념", "주년", "식전", "묵념", "반세기", "위령",
         "기일", "주기", "회고", "돌아본", "년 전", "재조명"]

# ── 다른 나라 사건 걸러내기(구글뉴스가 세계 뉴스를 섞어줌) ──────────
#  우리 30개국 + 사고 뉴스에 자주 나오는 다른 나라들.
EXTRA_COUNTRIES = [
    "바하마", "카타르", "볼리비아", "크로아티아", "시리아", "이라크", "아프가니스탄",
    "파키스탄", "나이지리아", "콩고", "케냐", "이집트", "사우디", "예멘", "가자",
    "레바논", "아이티", "과테말라", "칠레", "방글라데시", "네팔", "스리랑카",
    "캄보디아", "미얀마", "라오스", "몽골", "카자흐스탄", "남아프리카", "모로코",
    "알제리", "수단", "에티오피아", "짐바브웨", "그리스", "페루", "콜롬비아", "에콰도르",
    # 태평양 도서국(페리·태풍 사고 자주 보도됨)
    "바누아투", "피지", "파푸아뉴기니", "솔로몬제도", "통가", "사모아", "팔라우",
    # 중남미·기타
    "자메이카", "쿠바", "도미니카", "온두라스", "니카라과", "파나마", "코스타리카",
    "베네수엘라", "우루과이", "파라과이", "우크라이나", "요르단", "튀니지", "가나",
]
# 다른 나라 대표 도시(도시만 나오고 나라명이 없을 때 대비). 도시→나라.
FOREIGN_CITIES = {
    # 미국
    "뉴욕": "미국", "브롱크스": "미국", "맨해튼": "미국", "로스앤젤레스": "미국",
    "시카고": "미국", "휴스턴": "미국", "마이애미": "미국", "샌프란시스코": "미국",
    "워싱턴": "미국", "라스베이거스": "미국", "필라델피아": "미국",
    # 인도(뉴델리 사례로 추가)
    "뉴델리": "인도", "델리": "인도", "뭄바이": "인도", "콜카타": "인도",
    "첸나이": "인도", "벵갈루루": "인도", "하이데라바드": "인도",
    # 중국
    "베이징": "중국", "상하이": "중국", "광저우": "중국", "선전": "중국", "청두": "중국",
    # 기타 주요 도시
    "런던": "영국", "맨체스터": "영국", "파리": "프랑스", "베를린": "독일",
    "뮌헨": "독일", "도쿄": "일본", "오사카": "일본", "방콕": "태국",
    "마닐라": "필리핀", "자카르타": "인도네시아", "하노이": "베트남", "호치민": "베트남",
    "멕시코시티": "멕시코", "상파울루": "브라질", "리우데자네이루": "브라질",
    "모스크바": "러시아", "이스탄불": "튀르키예", "앙카라": "튀르키예",
    "시드니": "호주", "멜버른": "호주", "토론토": "캐나다", "밴쿠버": "캐나다",
    "마드리드": "스페인", "바르셀로나": "스페인", "로마": "이탈리아", "밀라노": "이탈리아",
    "카이로": "이집트", "라고스": "나이지리아", "카라치": "파키스탄",
}


def _is_location_mention(name, t):
    """name(나라)이 '장소'로 쓰였으면 True. 국적(~인/~계/~국적/~출신)뿐이면 False.
    예: '말레이시아인 피해'(국적)→False / '볼리비아 군기지'(장소)→True."""
    idx = 0
    while True:
        i = t.find(name, idx)
        if i < 0:
            return False
        after = t[i + len(name):]
        if not after.startswith(("인", "계", "국적", "출신")):
            return True        # 국적 접미사가 아니면 '장소' 언급
        idx = i + len(name)


def is_foreign(title, feed):
    """제목이 '피드 나라와 다른 나라' 사건이면 True. (구글뉴스 세계뉴스 혼입 제거)"""
    import 정책뉴스감시 as _p
    all_countries = set(_p.COUNTRY_NAMES) | set(EXTRA_COUNTRIES)
    t = title or ""
    others = [c for c in all_countries if c != feed and _is_location_mention(c, t)]
    others += [city for city, ctry in FOREIGN_CITIES.items() if ctry != feed and city in t]
    if not others:
        return False          # 다른 나라 '장소' 언급 없음 → 자국(또는 지역 도시) 사건
    if feed in t:
        return False          # 자국도 함께 언급되면 자국 관련으로 봄(모호하면 통과)
    return True               # 다른 나라만 언급 → 외국 사건 → 제외


def is_major(ko):
    """번역된 제목이 '대형 인명사고'면 True."""
    if any(w in ko for w in NOISE):
        return False
    if any(w in ko for w in ALWAYS_MAJOR):
        return True
    if any(w in ko for w in SCALE_TYPE) and any(w in ko for w in SCALE_WORD):
        return True
    return False


# 같은 사건 묶기용 유형어(대표 1개만 뽑음)
_SIG_TYPES = ["여객기", "항공기", "비행기", "여객선", "페리", "선박", "건물", "다리",
              "교량", "열차", "지하철", "참사", "폭발", "화재", "붕괴", "충돌",
              "추락", "침몰", "탈선", "테러", "총격", "가스"]


def event_sig(ko, country, pub):
    """같은 사건 판별용 지문: 나라|유형|날짜. 여러 언론사가 같은 사건을 보도해도 1건으로."""
    typ = next((w for w in _SIG_TYPES if w in ko), "사고")
    try:
        d = parsedate_to_datetime(pub).astimezone(timezone(timedelta(hours=9))).strftime("%m-%d")
    except Exception:
        d = ""
    return f"SIG:{country}|{typ}|{d}"


# ── 같은 사건 묶기(7일) ───────────────────────────────────────────
#  같은 나라 + 같은 종류 + 같은 도시(또는 같은 주·도·현)의 사고가 7일 안에 다시 잡히면 '후속'으로 보고 발송하지 않음.
#  (전엔 '나라+유형+날짜'로만 묶어, 날짜가 바뀌면 같은 사건이 다시 나갔음 — 델리 건물 붕괴가 사흘간 여러 번)
#  예외: 전에는 '대형'이었는데 이번에 '초대형'으로 커진 경우는 한 번 더 알림.
EVENT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "incident_events.json")
SAME_EVENT_DAYS = 7


def load_events():
    try:
        with open(EVENT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_events(events):
    cut = time.time() - 30 * 86400
    with open(EVENT_FILE, "w", encoding="utf-8") as f:
        json.dump([e for e in events if e.get("t", 0) >= cut], f, ensure_ascii=False, indent=1)


def _place_key(name):
    n = re.sub(r"\b(city|province|prefecture|state|district|county|region)\b", "", (name or "").lower())
    return re.sub(r"[^0-9a-z가-힣぀-ヿ一-鿿]+", "", n)


def _same_place(a, b):
    """도시끼리·광역끼리(또는 도시-광역 교차) 이름이 같거나 한쪽이 다른 쪽을 포함하면 같은 곳."""
    ka = [k for k in (_place_key(a.get("city")), _place_key(a.get("region"))) if len(k) >= 3]
    kb = [k for k in (_place_key(b.get("city")), _place_key(b.get("region"))) if len(k) >= 3]
    return any(x == y or (len(x) >= 4 and x in y) or (len(y) >= 4 and y in x) for x in ka for y in kb)


def find_same_event(events, country, info, now=None):
    """7일 안에 이미 알린 같은 사건이 있으면 그 기록, 없으면 None."""
    now = now or time.time()
    for e in reversed(events):
        if e.get("country") != country or e.get("etype") != info.get("etype"):
            continue
        if now - e.get("t", 0) > SAME_EVENT_DAYS * 86400 or now < e.get("t", 0) - 86400:
            continue
        if _same_place(e, info):
            return e
    return None


DEATH_TOL = 0.30         # 사망·실종 수가 ±30% 안이면 '비슷하다'


def _deaths_close(a, b):
    a, b = int(a or 0), int(b or 0)
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) <= DEATH_TOL * max(a, b)


def _similar_text(a, b, cut=0.5):
    """두 요약 문장이 거의 같은 내용이면 True(글자 기준 닮은 정도)."""
    import difflib
    a, b = re.sub(r"\s+", "", a or ""), re.sub(r"\s+", "", b or "")
    return bool(a and b) and difflib.SequenceMatcher(None, a, b).ratio() >= cut


def check_event(events, country, info, now=None, ask_same=None):
    """(보낼지, 이유). 보낼 때는 events 에 기록을 추가/갱신.
    ① 같은 나라·같은 종류·7일 안에 같은 도시(광역)이면 후속
    ② 도시를 모르는 쪽이 있으면: 사망·실종 수가 ±30% 안이면 후속(2026-10-15 — 인도 갠지스강 배 침몰이 장소 없이 6번 나갔음)
    ③ 그래도 애매하면(같은 나라·종류·7일 안인데 숫자가 다름) ask_same(정보, 후보들) 로 AI에게 '같은 사건인지'만 물음
    예외: 전에는 '대형'이었는데 이번에 '초대형'으로 커진 경우는 한 번 더 알림."""
    now = now or time.time()
    has_place = bool(info.get("city") or info.get("region"))
    same, how = (find_same_event(events, country, info, now), "같은 장소") if has_place else (None, "")
    pool = [e for e in reversed(events) if e.get("country") == country and e.get("etype") == info.get("etype")
            and 0 <= now - e.get("t", 0) <= SAME_EVENT_DAYS * 86400 + 86400]
    if not same:
        for e in pool:
            if (not has_place or not (e.get("city") or e.get("region"))) and _deaths_close(e.get("deaths"), info.get("deaths")):
                # 숫자가 비슷해도 요약 문장이 많이 다르면(다른 강·다른 지역일 수 있음) 아래에서 AI에게 확인
                #  (시험: '갠지스강 배 침몰 13명' 과 '아삼주 브라마푸트라강 여객선 12명'이 숫자만으로 묶였음)
                if _similar_text(e.get("summary", ""), info.get("summary", "")) or not ask_same:
                    same, how = e, f"사망·실종 수 비슷({e.get('deaths')}명 / {info.get('deaths')}명)"
                    break
    if not same and ask_same:
        unsure = [e for e in pool if not has_place or not (e.get("city") or e.get("region"))]
        if unsure:
            try:
                k = ask_same(info, unsure)
            except Exception:
                k = -1                               # AI 확인 실패 → 새 사건으로(놓치는 것보다 한 번 더 가는 쪽)
            if 0 <= k < len(unsure):
                same, how = unsure[k], "AI가 같은 사건으로 판단"
    if same:
        if info.get("tier") == "초대형" and same.get("tier") != "초대형":
            same["tier"] = "초대형"               # 피해가 크게 불어남 → 한 번 더 알림
            return True, ""
        when = datetime.fromtimestamp(same["t"]).strftime("%m/%d %H:%M")
        where = same.get("city") or same.get("region") or same.get("summary", "")[:20]
        return False, f"같은 사건({where} · {same.get('etype')})을 {when} 에 이미 알림 — {how}"
    events.append({"country": country, "etype": info.get("etype"), "city": info.get("city", ""),
                   "region": info.get("region", ""), "tier": info.get("tier", ""), "t": now,
                   "deaths": int(info.get("deaths") or 0), "summary": info.get("summary", "")})
    return True, ""


# ── 장소 확인용 '관련 보도' ────────────────────────────────────────
#  제목에 사고 장소가 없을 때(외신을 옮겨 실은 기사 등), 같은 사건을 다룬 다른 언론의 제목을 구글뉴스에서 찾아 AI에 함께 보여 줌.
#  (구글뉴스 RSS 에는 기사 본문·요약이 없고 제목만 있어서, 본문 대신 '다른 언론의 제목'으로 장소를 확인)
def related_headlines(it, max_each=8):
    title = re.sub(r"\s+[-–|]\s+[^-–|]{1,40}$", "", it.get("title", "")).strip()      # 끝의 ' - 언론사' 떼기
    if len(title) < 8:
        return ""
    by_name = {c["name_kr"]: c for c in pol.COUNTRIES}
    feeds = [by_name.get(it.get("moved_from") or it.get("country"))]
    us = by_name.get("미국")
    if us and us is not feeds[0]:
        feeds.append(us)                              # 세계 소식은 영어 보도가 가장 많음
    seen_t, out = {it.get("title", "")}, []
    for c in feeds:
        if not c:
            continue
        try:
            items = fetch(c, title + " when:3d")
        except Exception:
            continue
        n = 0
        for x in items:
            if x["title"] in seen_t:
                continue
            seen_t.add(x["title"])
            out.append(re.sub(r"\s+[-–|]\s+[^-–|]{1,40}$", "", x["title"]).strip())   # 언론사 이름은 장소 근거가 아니므로 뗌(2026-10-15)
            n += 1
            if n >= max_each:
                break
    return " || ".join(out)[:1800]


def fetch(country, query=None):
    """구글뉴스에서 그 나라 '사고' 뉴스를 가져옵니다. (query 를 주면 그 검색어로 — 산사태용)"""
    url = ("https://news.google.com/rss/search?q=" + quote(query or q_for(country["lang"]))
           + f"&hl={country['lang']}&gl={country['gl']}&ceid={country['ceid']}")
    with urlopen(Request(url, headers=UA), timeout=25) as resp:
        raw = resp.read().decode("utf-8", "replace")
    items = []
    for block in re.findall(r"<item>(.*?)</item>", raw, re.DOTALL):
        title = pol._clean(pol._tag(block, "title"))
        link = pol._tag(block, "link")
        pub = pol._tag(block, "pubDate")
        guid = pol._tag(block, "guid") or link
        m = re.search(r'<source[^>]*url="([^"]*)"[^>]*>(.*?)</source>', block, re.DOTALL)
        items.append({
            "title": title, "link": link, "pub": pub, "guid": guid,
            "src_url": m.group(1) if m else "",
            "src_name": pol._clean(m.group(2)) if m else "",
        })
    return items


def _clean_incident_title(ko):
    """사고 제목 정리. 정책용 clean_title과 달리 '·지명'(예: 도쿄·오타구)은 보존.
    번역 보정(대전구→오타구 등) 적용, 사진캡션·대괄호·언론사 꼬리표만 제거."""
    t = pol.fix_translation(ko)                       # 대전구→오타구 등 오역 보정
    t = re.sub(r"^\s*(?:이미지|사진|영상|포토|그래픽|자료사진)[\s·・/,]*[:：]\s*", "", t)
    t = re.sub(r"[\[【][^\]】]*[\]】]", "", t)          # [ ] 【 】 제거 (「」·지명은 유지)
    t = re.sub(r"\s*[\-–|]\s*[^\-–|]{1,25}$", "", t)  # 끝의 ' - 언론사'만 제거(·지명은 보존)
    return re.sub(r"\s+", " ", t).strip()


def locate(country, ai):
    """AI가 적은 도시·지역 이름 → 좌표(위도, 경도). 못 찾으면 (None, None).
    도시는 open-meteo 지오코딩, 안 되면 주·도·현 대표 좌표(행정구역.json)."""
    cc = rf.COUNTRY_CODE.get(country)
    city, region = (ai.get("city") or "").strip(), (ai.get("region") or "").strip()
    if cc and city:
        try:
            res = rf._search(city, cc, "en")
            if region:                                  # 같은 이름 도시가 여럿이면 지역이 맞는 것 우선
                res = [x for x in res if rf._n(region) in rf._n(x.get("admin1", ""))] or res
            if res:
                return round(res[0]["latitude"], 4), round(res[0]["longitude"], 4)
        except Exception:
            pass
    if region:
        a = rf.find_admin(country, region, loose=True)
        if a and a.get("center"):
            return a["center"][0], a["center"][1]
    return None, None


def make_block(it, country):
    """대형 사고 1건 → 발송용 블록.
    · 초대형(여객기 추락·사망 20명↑ 등): 🚨🚨 — 지역 상관없이 나라 전체에 최고 긴급(좌표 안 붙임)
    · 대형(사망 5명↑ 등)           : 🚨  — 좌표를 붙여 '내 지역 200km 안'일 때만 발송(하루 한도 적용)"""
    ai_info = it.get("ai") or {}
    title = ai_info.get("summary") or _clean_incident_title(pol.korean_title(it, it.get("lang", "auto")))   # AI가 다듬은 한 줄(없으면 번역)
    src = it["src_name"] or it["src_url"]
    try:
        when = local_kst_str(country, parsedate_to_datetime(it["pub"]))
    except Exception:
        when = pol.short_date(it["pub"])
    if ai_info.get("tier") == "산사태":               # 산사태(인명 피해·대피) — 좌표를 붙여 '내 지역 200km 안'일 때만
        lat, lon = locate(country, ai_info)
        tag = f"\n{rf.geo_tag(lat, lon)}" if lat is not None else ""
        return (
            f"🚨 [{country}] 산사태\n"
            f"{title}\n"
            f"현지 안전·가족 안부 확인해보세요.\n"
            f"출처: {src} · {when}\n"
            f"자세히 ▶ {it['link']}"
        ) + tag
    if ai_info.get("tier", "초대형") == "초대형":       # (AI 꺼짐 등 등급 정보가 없으면 예전처럼 최고 긴급)
        return (
            f"🚨🚨 [{country}] 대형 사고\n"
            f"━━━━━━━━━━━━\n"
            f"{title}\n"
            f"현지 안전·가족 안부 확인해보세요.\n"
            f"출처: {src} · {when}\n"
            f"자세히 ▶ {it['link']}"
        )
    lat, lon = locate(country, ai_info)
    tag = f"\n{rf.geo_tag(lat, lon)}" if lat is not None else ""
    return (
        f"🚨 [{country}] 대형 사고\n"
        f"{title}\n"
        f"현지 안전·가족 안부 확인해보세요.\n"
        f"출처: {src} · {when}\n"
        f"자세히 ▶ {it['link']}"
    ) + tag


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("seen", []))
    except Exception:
        return set()


def save_seen(seen):
    # 들어온 순서를 지켜 '오래된 것부터' 버림. (전엔 순서 없는 묶음에서 3000개를 잘라, 최근 것이 임의로 빠질 수 있었음)
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            old = json.load(f).get("seen", [])
    except Exception:
        old = []
    old_set = set(old)
    ids = ([g for g in old if g in seen] + [g for g in seen if g not in old_set])[-20000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"time": datetime_now(), "seen": ids}, f, ensure_ascii=False, indent=2)


def datetime_now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 실행 시간 예산(2026-10-16) — 자동실행기는 10분이 지나면 강제로 끊고, 끊기면 그때까지 판정한 알림도 저장도 다 사라짐.
#  그래서 AI 확인은 시작 후 사고 5분·산사태 7분까지만 새로 묻고, 남은 기사는 보류 목록에 넣어 다음 실행에서 확인.
#  한 번에 확인할 기사 수도 상한을 둠(사고 30건·산사태 20건 — 평소 한 번에 15건 안팎).
AI_BUDGET_SEC = {"대형사고": 300, "산사태": 420}
AI_MAX_ITEMS = {"대형사고": 30, "산사태": 20}


def main():
    t0 = time.time()
    seen = load_seen()
    out = []
    first = []                                   # 키워드 1차 통과(AI에게 물어볼 후보)
    retry = pol.load_retry()                     # 번역 실패로 다시 볼 기사
    n_wait = 0
    direct = pol.ai_direct_on()                  # AI판단이 켜져 있으면 구글 번역을 거치지 않음
    for country in pol.COUNTRIES:
        name = country["name_kr"]
        try:
            items = fetch(country)
        except Exception:
            continue
        # 신뢰 소스 + 미발송 + 최근(3일 이내)만
        cand = [it for it in items
                if pol.is_trusted(it, country["trusted"]) and it["guid"] not in seen
                and is_recent(it["pub"])]
        if direct:                                # 번역 없이 원문 제목을 바로 AI판단에(통과한 것만 Gemini가 한국어로 요약)
            for it in cand[:pol.AI_DIRECT_MAX]:
                it["ko"], it["country"], it["untranslated"], it["lang"] = it["title"], name, True, country["lang"]
                seen.add(it["guid"])
                retry.pop(it["guid"], None)
                first.append(it)
            continue                              # 나머지는 다음 실행 때
        cand = cand[:MAX_CANDIDATES]
        ok, failed = [], []
        for it in cand:
            ko = pol.tr.translate(it["title"], country["lang"])
            if ko is None:
                failed.append(it)                 # 번역 실패 → '본 것'에 넣지 않음(다음 실행 때 다시)
                continue
            it["ko"] = ko
            seen.add(it["guid"])
            retry.pop(it["guid"], None)
            ok.append(it)
        hold = {it["guid"] for it in failed}
        for it in items:                      # 나머지도 seen 처리(다음에 재검사 안 함)
            if pol.is_trusted(it, country["trusted"]) and it["guid"] not in hold:
                seen.add(it["guid"])
        if failed:                                # 번역이 막혀 있으면 원문 제목을 바로 AI판단에(산사태와 같은 방식)
            to_ai, waiting = pol.split_untranslated("대형사고", name, failed, country["lang"], seen, retry)
            first += to_ai
            n_wait += waiting
        cand = ok
        # ① 키워드 1차: 대형만 + 외국 사건 제외
        for it in cand:
            if is_major(it["ko"]) and not is_foreign(it["ko"], name):
                it["country"] = name
                first.append(it)

    # ② AI 최종 판단(제미나이) — 1차 통과 기사만 한 번에 묶어서. 실패하면 안 보내고 보류.
    events = load_events()
    ai_cfg = dict(ai.load_config(), _context_fn=related_headlines,   # 장소 불명일 때 관련 보도를 찾아 주는 함수를 같이 넘김
                  _deadline=t0 + AI_BUDGET_SEC["대형사고"], _max_items=AI_MAX_ITEMS["대형사고"])
    passed, _ = ai.judge("대형사고", first, cfg=ai_cfg)
    for it in passed:
        name = it["country"]
        info = it.get("ai") or {}
        send, why = check_event(events, name, info, ask_same=lambda inf, cands: ai.same_event(inf, cands))
        if not send:
            ai._log("대형사고", it, "후속", why, "")      # 같은 사건의 후속 보도 → 발송하지 않음
            continue
        if not (info.get("city") or info.get("region")):
            sig = event_sig(it["ko"], name, it["pub"])
            if sig in seen:
                continue                             # 장소를 모를 때만 옛 방식(나라+유형+날짜)
            seen.add(sig)
        out.append(make_block(it, name))
    save_seen(seen)                                  # 중간 저장 — 뒤에서 끊겨도 같은 기사를 처음부터 다시 판정하지 않게
    save_events(events)

    # ③ 산사태(지원 30개국) — 따로 검색 → 원문 제목 그대로 AI판단 '산사태'(실제 인명 피해·대피만 통과)
    slides = []
    for country in pol.COUNTRIES:
        name = country["name_kr"]
        try:
            items = fetch(country, q_for_landslide(country["lang"]))
        except Exception:
            continue
        slides += landslide_candidates(items, country, seen)
        for it in items:
            if pol.is_trusted(it, country["trusted"]):
                seen.add(it["guid"])
    passed, _ = ai.judge("산사태", slides, cfg=dict(ai.load_config(), _context_fn=related_headlines,
                                                  _deadline=t0 + AI_BUDGET_SEC["산사태"], _max_items=AI_MAX_ITEMS["산사태"]))
    for it in passed:
        name = it["country"]
        info = dict(it.get("ai") or {}, etype="산사태")
        send, why = check_event(events, name, info, ask_same=lambda inf, cands: ai.same_event(inf, cands))
        if not send:
            ai._log("산사태", it, "후속", why, "")
            continue
        out.append(make_block(it, name))
    save_events(events)

    if out:
        print("\n\n".join(out))
    else:
        print("🟢 대형사고: 새 소식 없음")
    if pol.tr.is_blocked():
        print(f"\n🟢 번역 차단 중 — 원문 제목으로 AI판단에 물음. 다음 실행 때 다시 볼 기사 {n_wait}건")
    save_seen(seen)
    pol.save_retry(retry)
    pol.tr.finish()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
