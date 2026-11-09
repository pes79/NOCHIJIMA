# -*- coding: utf-8 -*-
"""
AI판단.py — 뉴스 감시(정책뉴스·대형사고)의 '2단계 AI 최종 판단' (구글 제미나이).

■ 흐름
  ① 기존 키워드 필터로 1차 거르기 (각 감시 프로그램에 그대로 있음)
  ② 살아남은 기사만 모아서 제미나이에게 '한 번에' 물어봄 → yes 만 발송
  - 보수적: "확실할 때만 yes". 애매하면 no. (오발송 방지 > 놓침 방지)

■ API 키
  - gemini_config.json 에만 저장(이 파일/코드에는 절대 안 씀).
  - 키는 구글 제미나이 주소(generativelanguage.googleapis.com)로만, 요청 '헤더'에 담겨 갑니다
    (주소창·로그에 안 찍힘).

■ 실패하면 (API 오류·한도 초과·키 없음 등)
  - 그 기사는 '보내지 않음' + 기록에 '보류'로 남김.
  - 보류 기사는 ai_pending.json 에 넣어 두었다가 다음 실행 때 한 번 더 물어봄(최대 PENDING_HOURS).
  - 그래도 안 되면 버림(기록엔 남음).
  - enabled=false 로 바꾸면 AI 판단을 끄고 예전처럼 키워드만으로 발송.

■ 기록
  logs/AI판단기록_날짜.log 에 AI가 yes/no 한 기사 '전부'를 이유와 함께 남김.
"""

import json
import os
import re
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, "gemini_config.json")
PENDING_FILE = os.path.join(BASE, "ai_pending.json")
LOG_DIR = os.path.join(BASE, "logs")

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# 앞에서부터 시도. 없는 모델(404)이거나 그 모델 한도 초과(429)면 다음 모델로.
#  ※ 무료 한도는 '모델마다 따로'라서, 하나가 막혀도 다음 모델로 이어갈 수 있음.
#  (2026-09-28 실측: 2.5 계열은 이 키로 404(서비스 종료), 3.5 Flash-Lite 48/48 최상, 3.1 Flash-Lite 47/48)
DEFAULT_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]
POLICY_BATCH = 10        # 정책 기사는 한 번에 10건씩
BATCH_SIZE = 25          # 한 번에 묻는 기사 수(너무 길면 나눠서)
PENDING_HOURS = 24       # 보류 기사를 다시 물어보는 최대 시간

CONFIG_TEMPLATE = {
    "설명": "제미나이 API 키를 api_key 따옴표 안에 붙여넣으세요. 이 파일은 절대 남에게 보내거나 올리지 마세요. "
            "enabled 를 false 로 바꾸면 AI 판단을 끄고 예전(키워드만)처럼 동작합니다.",
    "api_key": "",
    "enabled": True,
    "models": DEFAULT_MODELS,
}


# ── 설정 ─────────────────────────────────────────────────────────
def load_config():
    """설정 읽기. 파일이 없으면 빈 틀을 만들어 둠(키는 사용자가 직접 입력)."""
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CONFIG_TEMPLATE, f, ensure_ascii=False, indent=2)
        return dict(CONFIG_TEMPLATE)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception:
        return {"enabled": True, "api_key": "", "_broken": True}
    cfg.setdefault("enabled", True)
    cfg.setdefault("models", DEFAULT_MODELS)
    cfg["api_key"] = (cfg.get("api_key") or "").strip()
    order = load_model_order()
    if order:
        cfg["models"] = order            # 모델 순서는 키가 없는 별도 파일(gemini_모델순서.json)이 우선
    return cfg


MODEL_ORDER_FILE = os.path.join(BASE, "gemini_모델순서.json")


def load_model_order():
    """gemini_모델순서.json 의 "models"(앞에서부터 차례로 시도). 파일이 없거나 비었으면 None → 키 파일의 순서를 씀."""
    try:
        with open(MODEL_ORDER_FILE, "r", encoding="utf-8-sig") as f:
            models = [m.strip() for m in (json.load(f).get("models") or []) if isinstance(m, str) and m.strip()]
        return models or None
    except Exception:
        return None


def is_enabled():
    """AI 판단을 쓸지. enabled=false 일 때만 끔(키가 비어 있으면 켜진 채 '보류'로 안전하게)."""
    return bool(load_config().get("enabled", True))


# ── 질문(프롬프트) ────────────────────────────────────────────────
COMMON_RULES = """너는 해외 소식 알림 서비스의 '최종 검수자'다. 받는 사람은 그 나라에 자녀·가족이 사는 한국인이다.
잘못 보낸 알림은 사용자를 떠나게 한다.
규칙:
- 확실할 때만 "yes". 조금이라도 애매하거나 정보가 부족하면 "no".
- 기사마다 따로 판단한다. 제목(한국어 번역본)과 원문 제목을 둘 다 보고, 번역이 이상하면 원문을 믿는다.
- reason 은 한국어 한 줄(40자 이내)로, 왜 yes/no 인지 핵심만.
- where 에는 '그 규정을 만든 나라' 또는 '사고가 난 나라'를 한국어 나라 이름 하나로 적는다
  (예: 미국, 일본, 그리스). 모르면 "모름". [표시된 나라]와 where 가 다르면 answer 는 반드시 "no".
- summary 에는 원문을 바탕으로 '자연스러운 한국어 한 줄(35자 이내)'로 무슨 일인지 적는다.
  번역투·언론사 이름·말줄임표 없이. 숫자·지명은 원문 그대로. 예: "영주허가 소득 요건 10월 1일부터 강화"
- 모든 id 에 대해 빠짐없이 답한다."""

POLICY_PROMPT = COMMON_RULES + """

받는 사람은 [표시된 나라]에 '유학 중인 자녀'를 둔 한국인 부모다. (2026-10-13 기준 변경: 이민 전반 → 유학생 가족)

질문: 이 기사가 [표시된 나라] 정부가 '유학생과 그 가족에게 직접 영향을 주는 규정'을 실제로 바꾸는
(또는 정부가 공식 발표·시행·확정·공식 검토하는) 소식인가?

★ 먼저 topic 을 아래 중 하나로 고른다. '해당없음'이면 answer 는 반드시 "no" 다.
- 학생비자       : 학생비자·유학 비자의 요건·심사·발급·기간·수수료 변경
- 어학연수       : 어학연수·어학원 등록생 비자·체류 규정
- 졸업후취업     : 유학생이 졸업 뒤 받는 취업·구직 비자 규정. 졸업생이 주로 쓰는 경로가 여기에 해당한다:
                   미국 OPT·STEM OPT·H-1B(전문직 취업비자 — 신청·연장·추첨·수수료 포함), 캐나다 PGWP(졸업 후 취업허가),
                   호주 485 졸업생 비자(Temporary Graduate), 영국 Graduate route(졸업생 비자),
                   일본 유학생의 취업 체류자격 변경(유학 → 기술·인문지식·국제업무 등), 그 밖의 나라의 포스트스터디 비자
- 체류허가·수수료 : 외국인 체류허가(체류자격·재류카드·거주등록)의 갱신 절차·수수료·심사 기준 변경
                   — 모든 외국인 체류자에게 적용되는 것이면 유학생에게도 적용되므로 여기에 해당
                   — 단, 영주허가(永住)·영주권·귀화의 요건만 바뀌는 소식은 여기가 아니라 해당없음
- 입국규정       : 입국 심사·전자여행허가·무비자 체류 기간·입국 자격·입국 제한 등 '한국인(또는 모든 외국인)'의 입국에 적용되는 규정
                   — '관광객 전용 세금·수수료'(관광세, 숙박세, 관광객 입국 수수료·방문세, 출국세 등)의 변화는 해당없음.
                     단, 학생·체류자에게도 적용되는 수수료(비자·체류허가 수수료 등)와 무비자 체류 기간·입국 자격 변경은 해당.
                   — 특정 제3국 국민만 대상인 비자·입국 조치(예: 대만의 '모로코인 비자 발급 재개')는 한국인과 무관하므로 해당없음
- 유학생 아르바이트 : 유학생의 취업·아르바이트 허용 시간·조건
- 유학생 건강보험   : 유학생·외국인 체류자의 건강보험·의료보험 가입 의무·보험료
- 학비·등록      : 외국인 유학생의 학비·등록·정원·입학 요건에 관한 정부 규정
- 운전면허       : 외국인의 운전면허 취득·외국 면허 교환(전환) 제도, 국제운전면허증 인정 범위·기간의 변경
                   — 시험 문제 유출·개별 사고·단속 실적·내국인만 해당하는 면허 제도는 해당없음
- 해당없음       : 위 어디에도 속하지 않는 것. 아래는 모두 '해당없음'이다.
    · 일반 노동이민, 특정 직종·특정 산업의 노동(취업)비자, 고용주 스폰서 취업비자, 취업허가 동결·쿼터
      (단, 위 '졸업후취업'에 적은 경로 — 미국 H-1B·OPT, 캐나다 PGWP, 호주 485, 영국 Graduate route 등 — 는 해당없음이 아니다)
    · 영주권 점수·영주권 경로·영주허가 요건, 투자이민, 가족 초청 이민
    · 불법체류·불법취업 단속, 추방, 난민·망명
    · 시민권·국적 취득, 시민권 소송·판결
    · SIM 카드·통신, 세금 등 유학 비자·체류와 직접 관계없는 생활 규정
    · 이주노동자 보호·구제 제도

yes 예: 학생비자 심사 강화 발표, 체류허가 갱신 수수료 인상 시행, 무비자 체류 기간 단축 확정,
        유학생 주당 아르바이트 허용 시간 변경, 유학생 건강보험 가입 의무화,
        외국 운전면허 교환 제도 변경·국제운전면허 인정 기간 변경.
★ 규정을 바꾸는 주체가 [표시된 나라] 정부(또는 그 나라 법원·의회의 확정 결정)가 아니면 answer 는 무조건 no.
  ※ 단, 그런 '다른 나라 규정' 기사도 where(실제로 규정을 만든 나라)와 topic 은 기사 내용대로 정확히 적는다.
    (프로그램이 그 기사를 where 나라의 소식으로 옮겨서 다시 판정한다. 예: 스위스 뉴스에 실린 '태국 무비자 체류 기간 단축'
     → answer=no, where=태국, topic=입국규정)
  - 제3국끼리의 협정, 다른 나라의 비자·입국 정책은 no
    (예: 러시아 피드에 들어온 '이집트 입국비자 수수료 인상' → 이집트 규정이므로 no)
  - EU처럼 그 나라가 속한 연합의 규정은 그 나라에 직접 적용될 때만 yes.

no 예(참고 — 실제로 잘못 새어 나왔던 유형):
- 정치 공방·정쟁·선거·의회 다툼·정당 행사(예: 'MAGA 소란', '민주당 조사 착수', 극우 대두 분석)
- 사설·칼럼·오피니언·해설(explainer)·'확인하는 방법' 같은 안내 기사
- 다른 나라 기사(예: 일본 피드에 들어온 호주 워홀 기사, 다른 나라 피드의 미국 정책 기사)
- 카드사 'Visa'(비자카드) 등 기업·금융·결제 뉴스, 기업 감원·해고
- 개인 사건(체포·기소·재판·추방된 개인), 소송 '제기·요청' 단계(아직 규정 변화 아님)
- 스포츠·연예·행사(예: 선수의 비자 소송, 건국 기념 걷기 행사), 군사·국방
- 민간단체의 '제언·요청'일 뿐 정부 결정이 아닌 것
- 한국인과 무관하게 '특정 제3국 국민·특정 개인·특정 단체'를 이름으로 지목한 제재성 비자 제한
  (예: 미국의 '에티오피아 관련 인사 비자 제한', '특정 정치 단체 대상 비자 제한')
  ※ 반대로 '방문 목적·행위'에 따른 일반 규정(예: 출산 관광 목적 비자 제한, 유학생 심사 강화)은
    국적과 상관없이 한국인에게도 적용되므로 yes.
- 이민국·출입국 사무소의 내부 운영 공지(재택근무·회의·점검·캠페인), 전략·비전·중장기 계획 발표
- 한 지방·한 창구만의 아주 좁은 절차 변경(예: 한 군(郡)에서 우편 접수 가능)
- 나라 사이의 표기·외교 분쟁(규정 변화가 아님), 통계·현황 기사(예: 신청자 수 감소)

dup(중복) 규칙:
- 아래 '최근 이미 보낸 소식'과 같은 내용이면 dup=true.
- 이번 목록 안에 같은 소식이 여러 건이면, 정보가 가장 분명한 1건만 dup=false, 나머지는 dup=true.
- 새로운 사실(날짜 확정·내용 변경)이 추가된 경우만 dup=false."""

INCIDENT_PROMPT = COMMON_RULES + """

질문: 이 기사가 [지금(최근 며칠 안에) 실제로 발생한] [표시된 나라 '안'에서의] '인명사고(사람이 죽거나 다친 사고·테러)'의
발생 또는 피해 상황을 알리는 소식인가?

★ 사고의 '규모(대형인지)'는 네가 판단하지 않는다. 프로그램이 아래 숫자로 직접 계산한다.
  → 규모가 작아 보여도, 실제 발생한 이 나라의 인명사고 소식이면 answer 는 "yes" 로 하고 숫자를 정확히 적는다.
★ 아래도 모두 '피해 상황을 알리는 소식'이므로 answer 는 "yes" 다 (첫 보도가 아니어도 된다):
  - 사망자·부상자 수 집계 갱신 (예: "건물 붕괴 사망자 6명으로 증가" → yes, deaths=6)
  - 구조·수색 진행 상황 (예: "침몰선에 93명 아직 갇혀 있다고 구조당국 발표" → yes, deaths=93)
  - 발생 소식 뒤에 "당국 조사 진행 중"이 붙은 기사 (예: "화물기 추락 5명 사망, 조사 착수" → yes)

숫자 칸은 반드시 채운다(제목에 없으면 0):
- deaths = 사망+실종 수, injured = 부상 수 (제목·원문 제목에 적힌 숫자 그대로. "최소 5명"이면 5)
- transport = 승객을 태운 여객기·여객선·여객열차·노선버스 사고면 true (개인 경비행기·화물기·화물선·군용기는 false)
- followup = 기사의 '중심 내용'이 사고 발생·피해가 아니라 원인 규명·수사 결과·체포·기소·재판·책임 공방·
             장례·추모·사고 후 영향·통계이면 true.
             ※ 다음은 followup=false: 사망자 수 집계 갱신("사망자 6명으로 증가"), 구조·수색 진행 상황
               ("실종 93명 수색 중"), 발생 기사 끝에 "당국이 조사 중"이 덧붙은 경우.
- summary 에는 어디서(주·도·현과 도시) 무슨 사고로 몇 명이 피해를 입었는지 넣는다.
  예: "미에현 쓰시 주택 화재로 4명 사망"
- city = 사고가 난 도시의 영어 이름(예: "Tsu", "Sao Paulo"). 모르면 "".
- region = 사고가 난 주·도·현의 영어 이름(예: "Mie", "California"). 모르면 "".
- date = 사고가 '일어난' 날짜를 "MM-DD" 로(기사 발행일이 아니라 사고 발생일. 제목·발행일로 알 수 있을 때만. "어제"·"오늘"은
         발행일 기준으로 계산). 알 수 없으면 "".
- incident = 나라와 상관없이, 이 기사가 '방금 실제로 일어난 인명사고(사람이 죽거나 다친 사고·테러)'의 발생·피해 소식이면 true.
             (아래 'answer 가 no 인 경우' 가운데 "다른 나라에서 난 사고"만 빼고 나머지 — 추모·비유·군사·사람 피해 없음 등 — 에 해당하면 false)
             사고가 난 나라가 [표시된 나라]가 아니어도, 장소가 불명이어도, 실제 인명사고 소식이면 true 다.
- etype = 사고 종류 하나: 붕괴(건물·다리·구조물) / 폭발 / 화재 / 추락(항공기 등) / 침몰(배) / 탈선·충돌(열차·버스·차량) / 테러·총격 / 기타


★ 장소는 '글자로 적힌 것'만 쓴다(2026-10-14 — 말레이시아 신문이 실은 파키스탄 열차 사고를 말레이시아 사고로 잘못 보낸 일이 있었음).
- 제목이나 '관련 보도' 칸에 장소(나라·도시·지역 이름)가 나와 있을 때만 where 를 적는다. 도시·지역만 나와 있으면 그곳이 있는 나라를 적는다.
- 언론사가 어느 나라 신문인지, [나라] 칸이 무엇인지로 장소를 짐작하지 마라. 외신을 옮겨 실은 기사일 수 있다.
  예: 말레이시아 신문의 "At least five people killed in train derailment" → 제목에 장소가 없으므로 where="장소 불명"
- 장소가 나와 있지 않으면 where="장소 불명", city="", region="", place_text="".
- place_text = 제목(또는 관련 보도)에서 장소를 가리키는 낱말을 '글자 그대로' 옮겨 적는다(예: "Delhi", "津市", "Cebu City"). 번역·수정하지 마라.
- '관련 보도' 칸이 있으면: 같은 사건(사고 종류와 사망자 수가 맞는 것)을 다룬 제목일 때만 장소의 근거로 쓴다.
  서로 다른 사건이 섞여 있거나 같은 사건인지 확신이 없으면 "장소 불명".

answer 가 "no" 인 경우(참고 — 키워드로 잡으려다 실제로 새어 나왔던 유형):
- 다른 나라에서 난 사고(예: 스웨덴·미국·호주 피드에 들어온 '아테네(그리스) 폭발',
  바누아투 페리 침몰, 뉴델리 건물 붕괴, 멕시코 모렐리아 수류탄, 바하마 비행기 추락,
  아르헨티나 피드에 들어온 스페인 '라리오하' 붕괴 — 같은 지명이 여러 나라에 있으니 주의).
  → 표시된 나라와 사고 장소가 다르면 무조건 no. 장소가 불분명해도 no.
- 추도·추모·회고·○주년(예: 9.11 25주년), 과거 사고 통계·데이터
- 스포츠·연예 비유(예: 골프 선수 '대폭발' 스코어), 연예인 생존담(예: 드러머 비행기 사고 생존)
- 무인기·군용기·군 훈련·지뢰 등 군사 사고(예: 글로벌호크 추락)
- 사람이 죽거나 다치지 않은 사고, 사고 후 조언·심리·교통 영향(예: 탈선 후 버스 수요 증가)"""


LANDSLIDE_PROMPT = COMMON_RULES + """

이 기사는 [표시된 나라]의 '산사태·토석류' 뉴스 후보다. 아래 칸을 기사 제목에 적힌 사실대로 채워라.
★ 통과 여부는 네가 정하지 않는다. 프로그램이 칸을 보고 계산한다. (answer 는 네 의견일 뿐 쓰이지 않는다)
★ 숫자는 제목·원문 제목에 적힌 그대로. 제목에 없으면 0. 추측하지 마라.

- where  = 산사태가 '실제로 난' 나라(한국어 이름 하나). [표시된 나라]를 그대로 베끼지 마라 — 기사 속 장소로 판단한다.
           (예: 인도 뉴스에 실린 네팔 고르카 산사태 → where=네팔) 장소가 나와 있지 않으면 "장소 불명".
★ 장소는 '글자로 적힌 것'만 쓴다(2026-10-14 — 말레이시아 신문이 실은 파키스탄 열차 사고를 말레이시아 사고로 잘못 보낸 일이 있었음).
- 제목이나 '관련 보도' 칸에 장소(나라·도시·지역 이름)가 나와 있을 때만 where 를 적는다. 도시·지역만 나와 있으면 그곳이 있는 나라를 적는다.
- 언론사가 어느 나라 신문인지, [나라] 칸이 무엇인지로 장소를 짐작하지 마라. 외신을 옮겨 실은 기사일 수 있다.
  예: 말레이시아 신문의 "At least five people killed in train derailment" → 제목에 장소가 없으므로 where="장소 불명"
- 장소가 나와 있지 않으면 where="장소 불명", city="", region="", place_text="".
- place_text = 제목(또는 관련 보도)에서 장소를 가리키는 낱말을 '글자 그대로' 옮겨 적는다(예: "Delhi", "津市", "Cebu City"). 번역·수정하지 마라.
- '관련 보도' 칸이 있으면: 같은 사건(사고 종류와 사망자 수가 맞는 것)을 다룬 제목일 때만 장소의 근거로 쓴다.
  서로 다른 사건이 섞여 있거나 같은 사건인지 확신이 없으면 "장소 불명".
- inside = 그 산사태가 [표시된 나라] 안에서 났으면 true, 다른 나라에서 났거나 어디인지 불분명하면 false.
- slope  = 실제로 '산·언덕·비탈·절벽의 흙과 돌(토사)'이 무너져 내린 것이면 true.
           false: 동굴 안 낙반·동굴 붕괴, 건물·다리·옹벽 등 구조물 붕괴, 눈사태, 홍수·범람만 있는 경우, 지반 침하(싱크홀),
                  산사태 '주의보·경보·위험 예보'만 있고 실제로 무너지지 않은 경우, 선거 '압승(landslide)' 같은 비유.
- work   = 광산·채굴(불법 채굴 포함)·채석장·공사장·굴착(우물·터널·도랑 파기) '작업 중'에 흙이 무너져 작업자가 묻힌 사고면 true.
           제목에 "산사태"라는 말이 있어도, 작업 현장에서 작업자가 묻힌 것이면 true 다.
- fresh  = 기사가 '방금 새로 일어난 산사태'의 발생·피해를 알리는 소식이면 true.
           ※ 날짜 계산은 하지 마라(오래된 사건은 프로그램이 date 와 발행 시각으로 거른다). 제목의 내용만 본다.
           false: 예전 산사태의 복구·재건·예산, 이재민 지원·이재민 사연, 원인 조사·책임 공방, 장례·추모, 통계·회고,
                  "산사태가 멈추지 않는다"처럼 오래 이어지는 상황, 제목에 '지난달·몇 주 전·○년 전' 같은 과거 표시가 있는 경우.
           (같은 산사태의 사망자 수 갱신·수색·구조 진행은 true)
- followup = 기사의 중심이 발생·피해가 아니라 원인 조사·복구 계획·예산·책임 공방·이재민 지원·추모·통계이면 true.
- date   = 산사태가 '일어난' 날짜 "MM-DD"(발행일이 아니라 발생일. 제목·발행일로 알 수 있을 때만. "어제"·"오늘"은 발행일 기준). 모르면 "".
- deaths = 사망+실종+매몰된 사람 수. injured = 부상자 수.
- victims = 죽거나 다치거나 묻힌 사람이 누구인지: 주민 / 도로 이용자 / 작업자 / 등반객·관광객 / 행인 / 기타 / 없음
- moved  = 산사태 때문에 '실제로 대피했거나 고립된(길이 끊겨 갇힌)' 사람 수. 주민·관광객·학생·등반객 누구든 상관없이 센다.
           제목에 적힌 숫자 그대로("3가구"·"아파트 3채"처럼 사람 수가 아니면 0).
           ※ 고립·대피 없이 지나간 경우는 0: "위험을 피했다", "가까스로 벗어났다", "무사했다", 굴러오는 바위를 피해 달아남 등.
- moved_kind = 대피·고립된 사람이 누구인지(참고용 — 통과 여부에는 쓰이지 않음):
      주민         : 그 마을·동네에 살거나 그곳 시설(수도원·종교 공동체·기숙사·요양원·캠프 등)에 머물던 사람들
      도로 이용자   : 도로·철도가 끊겨 차량·열차 안에 갇히거나 길이 막혀 고립된 사람들
      작업자       : 광산·공사·채굴 현장의 작업자
      등반객·관광객 : 등산객·등반대·관광객·여행자
      행인         : 지나가던 사람
      기타 / 없음
- summary = 어디서(주·도·현과 도시) 산사태로 어떤 피해가 났는지 자연스러운 한국어 한 줄(35자 이내). 예: "시즈오카현 아타미 산사태로 3명 실종"
- city = 발생 도시의 영어 이름, region = 주·도·현의 영어 이름. 모르면 ""."""


# 산사태 통과 기준(코드가 계산) — 2026-10-14
#   · 실제 산·비탈 붕괴(slope)이고, 작업 현장 매몰(work)이 아니고, 새로 일어난 일(fresh, 후속 아님)이고, 발생 48시간 이내이며
#   · 사망·실종 1명 이상  또는  실제 대피·고립 10명 이상(주민·관광객·학생 누구든. "위험을 피했다/무사했다"는 0명으로 셈)
LANDSLIDE_MOVED_MIN = 10


def _event_age_days(date_str, now=None):
    """'MM-DD' 발생일이 오늘로부터 며칠 전인지. 못 읽으면 None."""
    m = re.match(r"^\s*(\d{1,2})-(\d{1,2})\s*$", str(date_str or ""))
    if not m:
        return None
    now = now or datetime.now()
    try:
        d = now.replace(month=int(m.group(1)), day=int(m.group(2)), hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        return None
    if (d - now).days > 2:                            # 연초에 12월 날짜 → 지난해
        d = d.replace(year=d.year - 1)
    return (now - d).total_seconds() / 86400


def _other_country_in(text, country):
    """글에 [표시된 나라]는 없고 다른 나라 이름만 있으면 그 이름. (AI 칸이 틀렸을 때를 위한 글자 확인)"""
    text = text or ""
    if not text or (country and country in text):
        return ""
    try:
        from 나라정보 import COUNTRY_INFO, WORLD_KR
        names = set(COUNTRY_INFO) | set(WORLD_KR.values())
    except Exception:
        return ""
    return next((n for n in sorted(names, key=len, reverse=True) if n != country and len(n) >= 2 and n in text), "")


def landslide_verdict(raw, now=None):
    """(통과 여부, 이유). raw = AI가 채운 칸."""
    d, moved = int(raw.get("deaths") or 0), int(raw.get("moved") or 0)
    kind = str(raw.get("moved_kind") or "없음")
    if not raw.get("slope"):
        return False, "산·비탈 토사 붕괴가 아님"
    if raw.get("work"):
        return False, "광산·채굴·공사 작업 중 매몰(산사태로 보지 않음)"
    if not raw.get("fresh") or raw.get("followup"):
        return False, "새로 일어난 산사태 소식이 아님(후속·옛 일)"
    age = _event_age_days(raw.get("date"), now)
    if age is not None and age > 3:                   # 날짜 단위라 '이틀 전'까지 허용(48시간) — 사흘 넘으면 제외
        return False, f"발생일({raw.get('date')})이 48시간보다 오래됨"
    if d >= 1:
        return True, f"사망·실종 {d}명"
    if moved >= LANDSLIDE_MOVED_MIN:
        return True, f"{moved}명 대피·고립({kind})"
    return False, f"기준 미달(사망·실종 {d}, 대피·고립 {moved}명 — 10명 이상이어야 함)"


# ── 정책: 통과 후보를 1건씩 '다시 확인'(2026-10-13) ──
#  여러 건을 한꺼번에 물으면 같은 기사에 대한 답이 실행마다 달라지는 경우가 있었음(3번 중 1번꼴로 새어 나옴).
#  그래서 1차에서 yes 가 나온 기사만 1건씩 따로, 더 엄격하게 다시 묻고 둘 다 yes 일 때만 통과.
CONFIRM_PROMPT = """너는 해외 소식 알림 서비스의 '재확인 담당'이다. 받는 사람은 [표시된 나라]에 유학 중인 자녀를 둔 한국인 부모다.
아래 기사 1건이 1차 검수에서 통과 후보가 됐다. 아래 칸을 하나씩 사실대로 채워라(통과 여부는 프로그램이 칸을 보고 정한다).

- where   = 그 규정을 만든 나라(한국어 이름 하나). 모르면 "모름".
- changed = [표시된 나라] 정부(또는 그 나라 의회·법원의 확정 결정)가 규정을 실제로 바꾸는(발표·시행·확정) 소식이면 true.
            정당 공약, 검토·논의 단계, 사설·해설, 통계, 개인 사건, 단속 실적이면 false.
- korean  = 그 규정이 '한국 국적자'에게 적용되면 true(모든 외국인 대상이거나 한국이 포함됨).
            특정 다른 나라 국민만 대상이면(예: 중국인 단체 관광객 비자 면제, 모로코인 비자 발급 재개) false.
- topic   = 기사가 다루는 규정의 주제 하나.
    학생비자          : 학생비자·유학 비자의 요건·심사·발급·기간·수수료
    어학연수          : 어학연수생 비자·체류 규정
    졸업후취업        : 유학생이 졸업 뒤 받는 취업·구직 비자 — 미국 OPT·STEM OPT·H-1B(신청·연장·추첨·수수료 포함),
                        캐나다 PGWP, 호주 485 졸업생 비자, 영국 Graduate route, 일본 유학생의 취업 체류자격 변경, 각국 포스트스터디 비자
    체류허가·수수료   : 외국인 체류허가(체류자격)의 갱신 절차·수수료·심사 기준
                        — 기사에 영주허가 얘기가 함께 있어도, 체류자격 갱신 수수료·절차가 바뀌는 내용이 있으면 여기에 해당
    입국규정          : 한국인이 그 나라에 들어갈 때 적용되는 규정(무비자 체류 기간, 입국 자격, 입국 심사, 전자여행허가, 입국 제한)
                        — 관광·방문 목적 무비자도 포함(유학생 가족의 방문·입국에 적용되므로)
- tourist_fee = 기사가 다루는 변화가 '관광객 전용 세금·수수료'(관광세, 숙박세, 관광객 입국 수수료·방문세, 출국세 등)뿐이면 true.
                학생·체류자에게도 적용되는 수수료(비자·체류허가 수수료)나 무비자 체류 기간·입국 자격 변경이면 false.
    유학생 아르바이트 : 유학생 취업·아르바이트 허용 시간·조건
    유학생 건강보험   : 유학생·외국인 체류자의 건강보험 가입 의무·보험료
    학비·등록         : 외국인 유학생의 학비·등록·정원·입학 요건에 관한 정부 규정
    운전면허          : 외국인의 운전면허 취득·외국 면허 교환(전환), 국제운전면허증 인정 범위·기간의 변경
                        — 시험 문제 유출·개별 사고·내국인만 해당하는 면허 제도는 해당없음
    해당없음          : 위 어디에도 정확히 맞지 않는 것(위 졸업후취업 경로는 제외). 예: 일반 노동이민·특정 직종 취업비자·취업허가 동결,
                        영주권·영주허가 요건만 다룬 것·귀화·시민권, 가족이민·투자이민, 불법체류 단속·추방,
                        난민·망명·인권법 관련 이민 개혁, 통신(SIM)·세금
- dup     = 아래 '최근 이미 보낸 소식' 목록에 같은 내용이 있으면 true(새 사실이 추가된 경우만 false). 목록이 없으면 false.
- actor   = 그 규정을 실제로 만들거나 바꾼 '정부(또는 의회·법원)'의 나라(한국어 이름 하나). 기사에 나온 나라 이름을 그대로
            베끼지 말고 '누가 결정했는지'를 본다. 예: "이탈리아가 스페인과 오가는 교통편의 국경 검문을 연장" → actor=이탈리아.
- entry_country = topic 이 입국규정이면, 그 규정이 '어느 나라에 들어갈 때' 적용되는지(한국어 이름 하나).
            예: 이탈리아가 스페인발 교통편에 검문 → 이탈리아에 들어갈 때 적용 → entry_country=이탈리아. 입국규정이 아니면 "".
- central = 그 규정 변화가 기사의 '중심 내용'이면 true. 정상회의·회담·정치 일정 기사처럼 여러 주제 가운데 하나로
            짧게 언급될 뿐이면 false. (제목 대부분이 다른 주제이고 규정은 덧붙은 한 대목이면 false)
- event_date = 기사가 알리는 규정 변화가 '실제로 일어난 날'(정부 발표일·시행 시작일·법원 판결일 가운데 기사의 중심 소식의 날)을
            "YYYY-MM-DD" 로. 제목·발행일로 확실히 알 수 있을 때만 적는다(예: "오늘부터 시행" → 발행일).
            앞으로 시행될 예정이면 발표일. 지난 일을 다시 다룬 해설·학습·정리 기사면 원래 일이 일어난 날.
            모르면 "" (추측해서 적지 마라).
- answer  = 위 칸을 종합한 네 의견("yes"/"no"), reason 은 한국어 한 줄(40자 이내),
  summary 는 자연스러운 한국어 한 줄(35자 이내, 숫자·지명은 원문 그대로)."""


# ── 사고 장소만 따로 묻기(언론사·나라를 가리고) ──
#  같은 기사를 다시 물어도 AI는 '어느 나라 신문인지'를 보고 같은 짐작을 되풀이함(브라질 신문이면 브라질, 말레이시아 신문이면 말레이시아).
#  그래서 장소는 언론사 이름과 [나라] 칸을 뺀 '제목만' 보여 주고 따로 물음. 제목에 고유 지명이 없으면 '장소 불명'.
PLACE_PROMPT = """아래는 사고·재난 기사 제목이다. 어느 나라 언론사의 기사인지는 일부러 빼 두었다.
할 일: 사고가 난 나라를 '제목(과 관련 보도)에 글자로 적힌 고유 지명'만으로 판단한다.

- place_text = 제목(또는 관련 보도)에 나온 고유 지명(나라·도시·주·지역·산·강 이름)을 글자 그대로 옮겨 적는다. 없으면 "".
   ※ 버스터미널·역·공장·광산·마을·학교·병원·도로 같은 일반 명사는 지명이 아니다(예: "rodoviária", "village", "fábrica", "駅").
   ※ 회사·기관 이름도 지명이 아니다.
- where = 그 지명이 있는 나라(한국어 이름 하나). 고유 지명이 없으면 "장소 불명".
   ※ 제목의 '언어'로 나라를 짐작하지 마라(포르투갈어라고 브라질이 아니고, 영어라고 미국이 아니다).
   ※ 같은 이름의 장소가 여러 나라에 있어 어느 나라인지 정할 수 없으면 "장소 불명".
- '관련 보도' 칸이 있으면: 제목과 같은 사건(사고 종류와 사망자 수가 맞는 것)을 다룬 보도에 나온 지명만 근거로 쓴다.
   서로 다른 사건이 섞여 있거나 같은 사건인지 확신이 없으면 "장소 불명".
- place_quote = 그 지명이 들어 있는 부분을 제목(또는 관련 보도 한 줄)에서 '원문 글자 그대로' 복사한다(10~120자).
   번역·요약·고쳐 쓰기 금지. 원문 언어 그대로. 지명이 없으면 "".
   예: 제목 "Rescuers search for people trapped in a New Delhi building collapse" → place_quote="trapped in a New Delhi building collapse"
- 언론사·방송사 이름이나 그 나라(예: The Star, Bangkok Post, Japan Times, Times of India, NHK, Bernama)는 장소의 근거가 아니다.
   언론사가 어느 나라 것인지로 장소를 짐작하지 마라.
- answer 는 장소를 확정했으면 "yes", 아니면 "no". reason 은 한국어 한 줄(40자 이내). summary 는 ""."""


# ── 같은 사건인지만 묻기(대형사고·산사태 묶기용) ──
SAME_PROMPT = """아래 '새 사고'가 '최근 7일 같은 나라 사고 목록' 가운데 하나와 같은 사건(같은 사고를 다른 언론이 보도했거나 사망자 수가 갱신된 것)인지 판단하라.
- 장소·사고 종류·대상(예: 배·어린이·공장)이 맞고 날짜가 가까우면 같은 사건이다. 사망자 수는 집계가 갱신되며 달라질 수 있다.
- 같은 나라에서 같은 종류의 '다른' 사고일 수도 있다. 확신이 없으면 같은 사건이 아니다.
- which = 같은 사건인 목록 번호(0부터). 같은 사건이 없으면 -1. reason 은 한국어 한 줄(40자 이내)."""
SAME_SCHEMA = {"type": "OBJECT", "properties": {"which": {"type": "INTEGER"}, "reason": {"type": "STRING"}},
               "required": ["which", "reason"]}


def same_event(info, candidates, cfg=None):
    """새 사고(info: summary·deaths·etype·city)가 후보 목록 중 몇 번과 같은 사건인지. 없으면 -1. 실패하면 AIError."""
    cfg = cfg or load_config()
    key = cfg.get("api_key", "")
    if not key:
        raise AIError("키 없음")
    lines = [f"{i}. {datetime.fromtimestamp(e.get('t', 0)):%m/%d} · {e.get('etype')} · {e.get('city') or e.get('region') or '장소 불명'}"
             f" · 사망·실종 {e.get('deaths', '?')}명 · {e.get('summary', '')}" for i, e in enumerate(candidates)]
    prompt = (SAME_PROMPT + f"\n\n새 사고: {info.get('etype')} · {info.get('city') or info.get('region') or '장소 불명'} · "
              f"사망·실종 {info.get('deaths', '?')}명 · {info.get('summary', '')}\n\n최근 7일 같은 나라 사고 목록:\n" + "\n".join(lines))
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                       "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SAME_SCHEMA}}).encode("utf-8")
    last = "알 수 없음"
    for model in _models(cfg):
        try:
            _pace()
            sent = datetime.now()
            req = Request(API.format(model=model), data=body, method="POST",
                          headers={"Content-Type": "application/json", "x-goog-api-key": key})
            with urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            _count(model, "같은사건", "ok", sent)
            out = json.loads("".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]))
            return int(out.get("which", -1))
        except HTTPError as e:
            _count(model, "같은사건", f"HTTP {e.code}")
            last = f"HTTP {e.code}"
            if e.code == 429 and _daily_429(e, model):
                _mark_exhausted(model)
        except Exception as e:
            last = type(e).__name__
    raise AIError(last)


def _items_text(items):
    lines = []
    for i, it in enumerate(items):
        lines.append(json.dumps({
            "id": i,
            "나라": it.get("country", ""),
            "제목": it.get("ko", ""),
            "원문 제목": it.get("title", ""),
            "언론사": it.get("src_name", "") or it.get("src_url", ""),
            "발행": it.get("pub", ""),
            **({"관련 보도": it["context"]} if it.get("context") else {}),
        }, ensure_ascii=False))
    return "\n".join(lines)


_BASE_PROPS = {
    "id": {"type": "INTEGER"},
    "answer": {"type": "STRING", "enum": ["yes", "no"]},
    "where": {"type": "STRING"},
    "reason": {"type": "STRING"},
    "summary": {"type": "STRING"},
}
SCHEMAS = {
    "정책": {"type": "ARRAY", "items": {"type": "OBJECT",
           "properties": dict(_BASE_PROPS, dup={"type": "BOOLEAN"},
                              topic={"type": "STRING", "enum": ["학생비자", "어학연수", "졸업후취업", "체류허가·수수료", "입국규정",
                                                                "유학생 아르바이트", "유학생 건강보험", "학비·등록", "운전면허", "해당없음"]}),
           "required": ["id", "where", "topic", "answer", "reason", "summary", "dup"]}},
    "장소확인": {"type": "ARRAY", "items": {"type": "OBJECT",
             "properties": dict(_BASE_PROPS, place_text={"type": "STRING"}, place_quote={"type": "STRING"}),
             "required": ["id", "where", "place_text", "place_quote", "answer", "reason", "summary"]}},
    "정책확인": {"type": "ARRAY", "items": {"type": "OBJECT",
             "properties": dict(_BASE_PROPS, korean={"type": "BOOLEAN"}, changed={"type": "BOOLEAN"}, dup={"type": "BOOLEAN"},
                                tourist_fee={"type": "BOOLEAN"}, event_date={"type": "STRING"},
                                actor={"type": "STRING"}, entry_country={"type": "STRING"}, central={"type": "BOOLEAN"},
                                topic={"type": "STRING", "enum": ["학생비자", "어학연수", "졸업후취업", "체류허가·수수료", "입국규정",
                                                                  "유학생 아르바이트", "유학생 건강보험", "학비·등록", "운전면허", "해당없음"]}),
             "required": ["id", "where", "actor", "entry_country", "central", "changed", "korean", "tourist_fee", "topic", "dup",
                          "event_date", "answer", "reason", "summary"]}},
    "대형사고": {"type": "ARRAY", "items": {"type": "OBJECT",
             "properties": dict(_BASE_PROPS, deaths={"type": "INTEGER"}, injured={"type": "INTEGER"},
                                transport={"type": "BOOLEAN"}, followup={"type": "BOOLEAN"},
                                city={"type": "STRING"}, region={"type": "STRING"}, date={"type": "STRING"},
                                incident={"type": "BOOLEAN"}, place_text={"type": "STRING"},
                                etype={"type": "STRING", "enum": ["붕괴", "폭발", "화재", "추락", "침몰", "탈선·충돌", "테러·총격", "기타"]}),
             "required": ["id", "where", "answer", "reason", "summary", "deaths", "injured",
                          "transport", "followup", "city", "region", "date", "incident", "place_text", "etype"]}},
    "산사태": {"type": "ARRAY", "items": {"type": "OBJECT",
            "properties": dict(_BASE_PROPS, inside={"type": "BOOLEAN"}, slope={"type": "BOOLEAN"}, work={"type": "BOOLEAN"}, fresh={"type": "BOOLEAN"},
                               followup={"type": "BOOLEAN"}, date={"type": "STRING"},
                               deaths={"type": "INTEGER"}, injured={"type": "INTEGER"},
                               victims={"type": "STRING", "enum": ["주민", "도로 이용자", "작업자", "등반객·관광객", "행인", "기타", "없음"]},
                               moved={"type": "INTEGER"},
                               moved_kind={"type": "STRING", "enum": ["주민", "도로 이용자", "작업자", "등반객·관광객", "행인", "기타", "없음"]},
                               city={"type": "STRING"}, region={"type": "STRING"}, place_text={"type": "STRING"}),
            "required": ["id", "where", "place_text", "inside", "slope", "work", "fresh", "followup", "date", "deaths", "injured", "victims",
                         "moved", "moved_kind", "answer", "reason", "summary", "city", "region"]}},
}

# ── 대형사고 '규모 기준'은 AI 말이 아니라 여기서 숫자로 직접 계산(느슨한 yes 차단) ──
#  대형   : 사망·실종 5명 이상 또는 사상자(사망+부상) 20명 이상. 대중교통 사고도 같은 기준(2026-10-14 확정).
#  초대형 : 사망·실종 20명 이상 → 지역 상관없이 나라 전체에 최고 긴급
def incident_tier(x):
    d, i = int(x.get("deaths") or 0), int(x.get("injured") or 0)
    if d >= 20:
        return "초대형"
    if d >= 5 or d + i >= 20:
        return "대형"
    return ""


# ── 정책: 최근 보낸 소식(중복 방지용, 7일) ──
RECENT_FILE = os.path.join(BASE, "policy_recent.json")
RECENT_DAYS = 7


def _load_recent():
    try:
        with open(RECENT_FILE, "r", encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        rows = []
    cut = time.time() - RECENT_DAYS * 86400
    return [r for r in rows if r.get("t", 0) >= cut]


def _save_recent(rows):
    try:
        with open(RECENT_FILE, "w", encoding="utf-8") as f:
            json.dump(rows[-400:], f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _recent_text(items, rows):
    countries = {it.get("country") for it in items}
    lines = [f"- [{r['country']}] {r['summary']} ({r['date']})" for r in rows if r.get("country") in countries]
    return "\n최근 이미 보낸 소식(같은 내용이면 dup=true):\n" + ("\n".join(lines[-60:]) if lines else "- (없음)")


# ── 제미나이 호출 ─────────────────────────────────────────────────
class AIError(Exception):
    pass


def _count(model, kind, status, sent=None):
    """Gemini 호출 1번을 기록(하루 한도 80% 알림 포함). 실패해도 감시는 계속."""
    try:
        import gemini_사용량
        gemini_사용량.record(model, kind, status, sent)
    except Exception:
        pass


def _pace():
    try:
        import gemini_사용량
        gemini_사용량.wait_turn()
    except Exception:
        pass


QUOTA_WAIT = 60          # 429(한도 초과)가 나면 이만큼(초) 기다린 뒤 다음 모델로


class QuotaError(AIError):
    """한도 초과(429)가 두 모델에서 이어짐 → 이번 실행에서는 더 묻지 않고 보류 규칙을 따름."""


# ── 하루 한도가 찬 모델 기억(2026-10-16) ──
#  10/15 밤: 첫 번째 모델이 하루 한도(500회)를 다 쓴 뒤에도 매 호출마다 그 모델을 먼저 불러 429 → 60초 대기 → 다음 모델.
#  이 대기가 쌓여 대형사고감시가 매시간 10분 제한에 걸려 끊겼음. 이제 '하루 한도' 429를 받으면 그 모델은
#  구글 한도가 다시 차는 시각(미국 태평양 시간 자정)까지 건너뜀. (1분 한도 429는 예전처럼 60초 기다림)
EXHAUSTED_FILE = os.path.join(BASE, "gemini_소진.json")


def _reset_at():
    """구글 무료 한도가 다시 차는 시각(태평양 시간 다음 자정 + 5분)의 epoch 초."""
    try:
        from zoneinfo import ZoneInfo
        from datetime import timedelta
        now = datetime.now(ZoneInfo("America/Los_Angeles"))
        return (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0).timestamp()
    except Exception:
        return time.time() + 6 * 3600


def _exhausted():
    try:
        with open(EXHAUSTED_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}
    now = time.time()
    return {m: t for m, t in d.items() if isinstance(t, (int, float)) and t > now}


def _mark_exhausted(model):
    d = _exhausted()
    d[model] = _reset_at()
    try:
        with open(EXHAUSTED_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _models(cfg):
    """오늘 한도가 남은 모델만(설정 순서대로)."""
    ex = _exhausted()
    return [m for m in (cfg.get("models") or DEFAULT_MODELS) if m not in ex]


def _daily_429(e, model):
    """429 가 '하루 한도'이면 True. 응답 글에 PerDay 가 있거나, 기록상 오늘 성공 호출이 한도에 거의 닿았으면 하루 한도로 봄."""
    try:
        msg = e.read().decode("utf-8", "replace")
    except Exception:
        msg = ""
    if re.search(r"PerDay|per day|daily", msg, re.I):
        return True
    if re.search(r"PerMinute|per minute", msg, re.I):
        return False
    try:
        import gemini_사용량 as gu
        limit, _warn = gu.model_limit(model, gu.load_limit())
        day = gu.pacific_now().strftime("%Y-%m-%d")
        with open(gu._path(day), "r", encoding="utf-8") as f:
            ok = sum(1 for ln in f if ln.rstrip("\n").split("\t")[1:2] == [model] and ln.rstrip("\n").endswith("\tok"))
        return limit > 0 and ok >= limit - 10
    except Exception:
        return False


def _call(model, key, prompt, kind="정책"):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": SCHEMAS.get(kind, SCHEMAS["정책"]),
        },
    }
    req = Request(API.format(model=model), data=json.dumps(body).encode("utf-8"),
                  headers={"Content-Type": "application/json", "x-goog-api-key": key},
                  method="POST")
    _pace()                                           # 모든 감시 합쳐 1분에 10회를 넘지 않게 기다림
    sent = datetime.now()                             # 요청을 보낸 시각(기록은 이 시각으로 — 구글이 세는 기준과 같게)
    try:
        with urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        _count(model, kind, f"HTTP {e.code}", sent)
        raise
    except Exception as e:
        _count(model, kind, type(e).__name__, sent)
        raise
    _count(model, kind, "ok", sent)
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        return json.loads(text)
    except Exception:
        raise AIError("응답 형식 이상")


def _ask(kind, items, cfg):
    """기사 묶음 1개를 물어봄 → {id: (answer, reason)} + 사용한 모델. 실패 시 AIError."""
    key = cfg.get("api_key", "")
    if not key:
        raise AIError("API 키 없음(gemini_config.json)")
    prompt = {"정책": POLICY_PROMPT, "정책확인": CONFIRM_PROMPT, "산사태": LANDSLIDE_PROMPT,
              "장소확인": PLACE_PROMPT}.get(kind, INCIDENT_PROMPT) + \
        (_recent_text(items, cfg.get("_recent", [])) if kind in ("정책", "정책확인") else "") + \
        f"\n\n오늘 날짜(한국): {datetime.now():%Y-%m-%d}\n기사 목록(JSON 한 줄에 1건):\n" + _items_text(items)
    last = "알 수 없음"
    quota_hits = 0
    models = _models(cfg)
    if not models:
        raise QuotaError("모든 모델이 오늘 하루 한도 소진(태평양 시간 자정에 다시 참)")
    for model in models:
        for attempt in range(2):                      # 일시 오류(5xx·네트워크)는 1번 재시도
            try:
                out = _call(model, key, prompt, kind)
                res = {}
                for r in out if isinstance(out, list) else []:
                    try:
                        res[int(r["id"])] = (str(r["answer"]).strip().lower(), str(r.get("reason", "")).strip(),
                                             str(r.get("where", "")).strip(), r)
                    except Exception:
                        continue
                return res, model
            except HTTPError as e:
                code = e.code
                last = {400: "요청 거부(400)", 401: "키 오류(401)", 403: "키 권한 없음(403)",
                        404: f"모델 없음({model})", 429: f"한도 초과({model})"}.get(code, f"HTTP {code}")
                if code in (401, 403):
                    raise AIError(last)               # 키 문제 → 다른 모델도 소용없음
                if code == 429 and _daily_429(e, model):   # 하루 한도 → 이 모델은 오늘 건너뛰고 바로 다음 모델
                    _mark_exhausted(model)
                    quota_hits += 1
                    if model == models[-1]:
                        raise QuotaError(f"한도 초과(429) — {model} 하루 한도 소진")
                    break
                if code == 429:                       # 1분 한도: 60초 기다린 뒤 다음 모델. 그것도 429면 보류.
                    quota_hits += 1
                    if quota_hits >= 2:
                        raise QuotaError(f"한도 초과(429) — {model} 도 한도 초과")
                    time.sleep(QUOTA_WAIT)
                    break
                if code == 404:
                    break                             # 다음 모델로
                time.sleep(3)
            except (URLError, TimeoutError, OSError) as e:
                last = f"연결 실패({type(e).__name__})"
                time.sleep(3)
            except AIError as e:
                last = str(e)
                time.sleep(1)
    raise AIError(last)


# ── 보류 목록(실패 시 다음 실행 때 재시도) ─────────────────────────
def _load_pending():
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_pending(p):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=2)


# 보류(AI 확인 실패) 기사를 다시 묻는 최대 시간 — 종류별.
#  정책은 급하지 않으니 24시간까지. 대형사고·산사태는 늦게 가면 의미가 없으므로 감지 후 6시간이 지나면 보내지 않고 버림.
HOLD_HOURS = {"정책": 24, "대형사고": 6, "산사태": 6}


def take_pending(kind):
    """kind 의 보류 기사를 꺼냄(매 실행마다 다시 물음). 기한이 지난 것은 버리고 기록에 남김."""
    p = _load_pending()
    if not p.get(kind):
        return []
    limit = HOLD_HOURS.get(kind, PENDING_HOURS)
    out, now = [], time.time()
    for it in p[kind]:
        if now - it.get("_held_at", now) <= limit * 3600:
            out.append(it)            # 다시 물어봄(또 실패하면 _hold 가 원래 시각 그대로 다시 넣음)
        elif kind == "정책":
            _log(kind, it, "검사 실패", f"{limit}시간 동안 확인하지 못함({it.get('_held_why') or 'AI 확인 실패'}) — 더 시도하지 않음", "")
        else:
            _log(kind, it, "버림", f"감지 후 {limit}시간이 지나도록 확인하지 못함({it.get('_held_why') or 'AI 확인 실패'})"
                                   " — 늦은 알림이 되므로 보내지 않고 버림", "")
    p[kind] = []
    _save_pending(p)
    return out


def _hold(kind, items, why=""):
    """보류 목록에 넣음. why = 왜 미뤘는지(버릴 때 기록에 그대로 씀) — 2026-10-17"""
    if not items:
        return
    for it in items:
        if why:
            it["_held_why"] = why
    p = _load_pending()
    lst = p.get(kind, [])
    have = {x.get("guid") for x in lst}
    for it in items:
        if it.get("guid") in have:
            continue
        x = {k: v for k, v in it.items() if isinstance(v, (str, int, float, list, dict))}
        x.setdefault("_held_at", time.time())
        lst.append(x)
    p[kind] = lst[-200:]
    _save_pending(p)


# ── 기록 ─────────────────────────────────────────────────────────
def _log(kind, it, verdict, reason, model):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"AI판단기록_{datetime.now():%Y-%m-%d}.log")
    mark = {"yes": "✅ yes", "no": "❌ no", "보류": "⏸ 보류", "버림": "🗑 버림", "검사 실패": "⚠️ 검사 실패", "후속": "↩️ 후속(발송 안 함)", "판정 불일치": "⚠️ 판정 불일치(발송 안 함)", "장소 불명": "❓ 장소 불명(발송 안 함)"}.get(verdict, verdict)
    line = (f"{datetime.now():%H:%M} [{kind}] {mark} | [{it.get('country','')}] {it.get('ko','')}"
            f"\n        원문: {it.get('title','')}"
            f"\n        이유: {reason}" + (f"  (모델 {model})" if model else "")
            + f"\n        링크: {it.get('link', '') or '(없음)'}"
            + (f"\n        인용: {it['_place_quote']}" if verdict == "장소 불명" and it.get("_place_quote") else "") + "\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def _same_country(where, country):
    w = (where or "").replace(" ", "")
    c = (country or "").replace(" ", "")
    if not w or not c:
        return False
    alias = {"미합중국": "미국", "USA": "미국", "영국(UK)": "영국", "터키": "튀르키예", "Türkiye": "튀르키예"}
    w = alias.get(w, w)
    return w == c or w.startswith(c)          # 예: '미국(캘리포니아)' → 미국


# ── 바깥에서 쓰는 함수 ─────────────────────────────────────────────
# 정책: AI가 yes 라 해도, 제외 주제 낱말만 있고 유학생 관련 낱말이 하나도 없으면 막음(이중 확인)
_EXCL = re.compile(r"영주|永住|permanent residen|settled status|indefinite leave|green card|시민권|국적 취득|귀화|citizenship|"
                   r"naturali[sz]|Einbürgerung|난민|망명|asylum|refugee|불법 ?체류|불법 ?취업|단속|crackdown|추방|deport", re.I)
_KEEP = re.compile(r"유학|학생|留学|學生|学生|student|étudiant|studierende|studenten|estudiante|sinh viên|du học|นักศึกษา|mahasiswa|"
                   r"어학|수수료|手数料|fee|갱신|更新|renew|체류허가|체류자격|在留資格|residence permit|Aufenthaltstitel|"
                   r"무비자|입국|entry|visa[- ]free|miễn thị thực|ไม่ต้องขอวีซ่า|아르바이트|건강보험|학비|등록금|tuition|"
                   r"운전면허|免許|driv(?:er|ing).{0,3}licen[cs]e|Führerschein|permis de conduire|"
                   # 졸업 후 취업 경로(2026-10-15 — 'H-1B … asylum' 기사가 '망명' 낱말 때문에 막혔음)
                   r"졸업|graduat|H-?1B|\bOPT\b|PGWP|subclass 485|\b485 ?visa|技術・人文知識|就職", re.I)


def _excluded_policy(text):
    return bool(_EXCL.search(text or "")) and not _KEEP.search(text or "")


POLICY_MAX_AGE_DAYS = 7      # 규정 변화가 일어난 지 이보다 오래되면 보내지 않음


def confirm_policy(it, cfg):
    """기사 1건을 다시 확인. (통과 여부, 이유, 주제, 요약). AI 호출이 실패하면 AIError.
    AI의 '예/아니오'는 같은 기사에도 실행마다 흔들려서 쓰지 않고, 칸(나라·실제 변경·한국인 적용·주제·중복)으로 프로그램이 정함."""
    res, _ = _ask("정책확인", [it], cfg)
    ans, why, where, raw = res.get(0, ("no", "재확인 답 누락", "", {}))
    topic = str(raw.get("topic") or "해당없음")
    summary = str(raw.get("summary") or "").strip()
    if not raw:
        return False, "재확인 답 누락", topic, summary
    # 규정을 바꾼 정부(2026-10-17): 표시된 나라와 다르면, 지원 나라일 때 그 나라 소식으로 한 번만 옮겨 다시 확인.
    #  (10/16 이탈리아 신문의 '이탈리아가 스페인 교통편 검문 7일 연장'이 스페인 규정으로 잘못 옮겨졌음)
    actor = str(raw.get("actor") or "").strip() or where
    if actor and actor not in ("모름", "장소 불명") and not _same_country(actor, it.get("country", "")):
        dst = supported_country(actor)
        if dst and not it.get("_actor_moved"):
            src = it.get("country", "")
            it["_actor_moved"] = True
            it.setdefault("moved_from", src)
            it["country"] = dst
            ok, why2, topic2, summary2 = confirm_policy(it, cfg)
            return ok, f"[옮김: 규정을 바꾼 정부 {src} → {dst}] {why2}", topic2, summary2
        return False, f"재확인: 규정을 바꾼 정부가 다른 나라({actor})" + ("" if dst else " — 지원 나라가 아님"), topic, summary
    if not _same_country(where, it.get("country", "")) and not _same_country(actor, it.get("country", "")):
        return False, f"재확인: 나라 불일치({where or '모름'})", topic, summary
    if raw.get("central") is False:
        return False, f"재확인: 규정 변화가 기사의 중심 내용이 아님(여러 주제 가운데 한 대목) — {why}", topic, summary
    entry = str(raw.get("entry_country") or "").strip()
    if topic == "입국규정" and entry and entry not in ("모름",) and not _same_country(entry, it.get("country", "")):
        return False, f"재확인: 입국규정이 {entry}에 들어갈 때 적용되는 것(이 나라 입국 규정 아님) — {why}", topic, summary
    if not raw.get("changed"):
        return False, f"재확인: 정부가 규정을 실제로 바꾸는 소식이 아님 — {why}", topic, summary
    if not raw.get("korean"):
        return False, f"재확인: 한국인에게 적용되지 않음(특정 다른 나라 국민 대상) — {why}", topic, summary
    if raw.get("tourist_fee"):
        return False, f"재확인: 관광객 전용 세금·수수료 변화 — {why}", topic, summary
    if topic == "해당없음":
        return False, f"재확인: 유학생 가족 대상 주제가 아님 — {why}", topic, summary
    if raw.get("dup"):
        return False, f"이미 보낸 소식과 같은 내용 — {why}", topic, summary
    # 규정 변화가 일어난 날(2026-10-17): 7일보다 오래됐으면 지난 소식 → 보내지 않음. 모르면 지금처럼 판정하되 '날짜 불명' 표시.
    ed = str(raw.get("event_date") or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", ed)
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        d = None
    if d and (datetime.now() - d).days > POLICY_MAX_AGE_DAYS:
        return False, f"재확인: 규정 변화가 일어난 날({ed})이 {POLICY_MAX_AGE_DAYS}일보다 오래된 지난 소식 — {why}", topic, summary
    if not d:
        why = f"{why} [날짜 불명]"
    else:
        why = f"{why} [변화일 {ed}]"
    return True, why, topic, summary


def supported_country(where):
    """AI가 적은 나라 이름 → 우리 지원 나라 이름. 지원 나라가 아니면 None."""
    try:
        from 나라정보 import COUNTRY_INFO
    except Exception:
        return None
    return next((n for n in COUNTRY_INFO if _same_country(where, n)), None)


def judge_moved(moved, cfg, recent, results, passed, write_log, hold_on_fail):
    """다른 나라 뉴스에 실린 규정 기사를 '실제 대상 나라'의 소식으로 옮겨 다시 판정.
    moved: [(기사, 원래 뉴스 나라, 대상 나라)]. 대상 나라 감시가 이미 보낸 같은 소식이면 중복으로 묶음."""
    for it, src, dst in moved:
        it["country"], it["moved_from"] = dst, src
        tag = f"[옮김: {src} 뉴스 → {dst}]"
        try:
            ok, why, topic, summary = confirm_policy(it, cfg)
        except AIError as e:
            it["country"] = src                       # 보류 → 다음 실행 때 원래 나라로 다시 1차부터
            it.pop("moved_from", None)
            results[it.get("guid")] = ("보류", f"옮겨서 재판정 실패: {e}")
            if write_log:
                _log("정책", it, "보류", f"{tag} AI 확인 실패 → 안 보냄: {e}", "")
            if hold_on_fail:
                _hold("정책", [it], f"AI 확인 실패: {e}")
            continue
        if ok and _excluded_policy(f"{it.get('title', '')} {it.get('ko', '')} {summary}"):
            ok, why = False, "제외 주제(영주권·시민권·단속 등)만 다룸"
        dst = it.get("country", dst)                  # 재확인에서 '규정을 바꾼 나라'로 다시 옮겨졌을 수 있음
        ans = "yes" if ok else "no"
        why = f"{tag} " + (f"[{topic}] {why}" if ok else why)
        if ok:
            it["ai"] = {"summary": summary, "topic": topic}
            passed.append(it)
            if summary:
                recent.append({"country": dst, "summary": summary, "date": f"{datetime.now():%m/%d}", "t": time.time()})
        results[it.get("guid")] = (ans, why)
        if write_log:
            _log("정책", it, ans, why + (f" ⇒ {summary}" if ok and summary else ""), "")


# ── 대형사고: 기준을 넘은 후보는 1건씩 다시 물어 '같은 답'일 때만 통과(2026-10-14) ──
#  숫자(사망·실종, 부상)·나라·발생일을 다시 받아 1차 답과 비교. 같으면 통과, 다르면 세 번째로 물어 2번 이상 나온 답을 씀.
#  세 답이 모두 다르면 보내지 않고 '판정 불일치'로 기록만 남김. 대형 여부는 언제나 코드가 숫자로 계산.
def _inc_key(where, raw):
    country = supported_country(where) or (where or "").replace(" ", "")
    return (int(raw.get("deaths") or 0), int(raw.get("injured") or 0), country, str(raw.get("date") or "").strip())


def _main_country_conflict(where_blind, where_main):
    """가리고 물은 나라와, 평소처럼 물은 답의 나라가 '둘 다 확정인데 서로 다르면' True."""
    if not where_main or where_main in ("모름", "장소 불명"):
        return False
    return _country_key(where_blind) != _country_key(where_main)


def _norm_place(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or ""))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[\s\-_.,'’`\"()]+", "", t).lower()


def place_unknown(where, raw, it):
    """장소를 믿을 수 없으면 True: AI가 '장소 불명'이라 했거나, 근거 낱말(place_text)이 제목·관련 보도에 실제로 없을 때."""
    if not where or where in ("모름", "장소 불명") or "불명" in where:
        return True
    hay = _norm_place(f"{it.get('title', '')} {it.get('ko', '')} {it.get('context', '')}")
    # 근거 지명이 여러 개("Pakistan, Punjab")로 올 수 있으므로 낱말별로 확인 — 하나라도 실제로 적혀 있으면 인정
    parts = [_norm_place(p) for p in re.split(r"[,/;·、\s]+", str(raw.get("place_text") or ""))]
    parts = [p for p in parts if len(p) >= 2]
    return not any(p in hay for p in parts)


# ── 장소 근거 인용 확인(2026-10-15) ──
#  AI가 고른 나라를 그대로 믿지 않고, AI가 옮겨 적은 '원문 인용문'이 ① 실제 기사 글(제목·관련 보도)에 있고
#  ② 그 안에 그 나라 이름이나 그 나라의 도시·주 이름이 있을 때만 장소로 인정. 아니면 '장소 불명'.
#  (언론사 이름은 기사 글에서 떼고 확인 — 'Bangkok Post' 같은 이름이 장소 근거가 되지 않게)
_NAMES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "나라이름.json")
_names_cache = {}


def country_names(country):
    """그 나라의 여러 언어 이름(나라이름.json — Wikidata). 파일이 없으면 한국어 이름만."""
    if not _names_cache:
        try:
            with open(_NAMES_FILE, "r", encoding="utf-8") as f:
                _names_cache.update(json.load(f).get("나라") or {})
        except Exception:
            _names_cache["_"] = []
    return [country] + list(_names_cache.get(country) or [])


_CJK = re.compile(r"[\u0e00-\u0e7f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")   # 태국·일본·중국·한국 글자(띄어쓰기 없음)


def _name_in(name, text):
    """나라 이름이 글에 '낱말로' 들어 있는지. 다른 나라의 더 긴 이름 속 글자는 빼고 봄(印度 ⊂ 印度尼西亚)."""
    for other in {n for c in _names_cache for n in (_names_cache.get(c) or [])}:
        if len(other) > len(name) and name in other:
            text = text.replace(other, " ")
    if _CJK.search(name):
        return name in text
    if not re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I):
        return False
    if name.lower() in ("mexico", "méxico") and not re.search(r"(?<!New )(?<!Nuevo )(?<!\w)M[eé]xico(?!\w)", text, re.I):
        return False                           # 미국 뉴멕시코주는 멕시코가 아님
    return True


_ABBR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "도시약칭.json")
_abbr_cache = {}
_KANA = re.compile(r"^[\u30a0-\u30ff]+$")


def _abbr_match(abbr, text):
    """줄임말이 글에 '낱말로' 들어 있는지. (2026-10-15)
    · 가타카나(ロス): 앞뒤에 다른 가타카나가 붙으면 아님(クロス·ロスト·ロスタイム)
    · 한자·태국 글자: 글자 그대로
    · 영어 4글자 이하(LA·NY·SP·KL): 대문자 그대로, 앞뒤에 다른 글자·숫자가 없을 때만(Las·Plan·la 는 아님)
    · 더 긴 영어(Vegas·Jogja): 대소문자 상관없이 낱말 단위"""
    if _KANA.match(abbr):
        return bool(re.search(r"(?<![\u30a0-\u30ff])" + re.escape(abbr) + r"(?![\u30a0-\u30ff])", text))
    if _CJK.search(abbr):
        return abbr in text
    letters = re.sub(r"[^A-Za-z]", "", abbr)
    flags = 0 if len(letters) <= 4 else re.I
    return bool(re.search(r"(?<![\w])" + re.escape(abbr) + r"(?![\w])", text, flags))


def abbr_hit(country, text):
    """그 나라 줄임말이 글에 있으면 (줄임말, 공식 도시, 주) — 없으면 None. 표: 도시약칭.json"""
    if not _abbr_cache:
        try:
            with open(_ABBR_FILE, "r", encoding="utf-8") as f:
                _abbr_cache.update(json.load(f).get("나라") or {})
        except Exception:
            _abbr_cache["_"] = {}
    hits = [(abbr, city, region) for abbr, (city, region) in (_abbr_cache.get(country) or {}).items() if _abbr_match(abbr, text or "")]
    if not hits:
        return None
    # 겹치면 더 긴 것, 같은 길이면 글에서 먼저 나온 것(예: '新北市' 은 北市(타이베이)가 아니라 新北(신베이))
    return max(hits, key=lambda h: (len(h[0]), -(text or "").find(h[0])))


def _place_fields(it, raw):
    """AI가 도시·주를 비워 뒀는데 장소 근거가 줄임말(ロス 등)이었으면, 줄임말 표의 도시·주로 채움(지역 필터 좌표용)."""
    city, region = str(raw.get("city") or "").strip(), str(raw.get("region") or "").strip()
    hint = it.get("_place_hint")
    if hint and not city and not region:
        city, region = hint[0] or "", hint[1] or ""
    return {"city": city, "region": region}


def _place_in_country(name, country):
    """지명이 그 나라의 주·도·현 또는 도시면 True. (행정구역.json → open-meteo 지명 검색)"""
    try:
        import 지역필터 as rf
        if rf.find_admin(country, name, loose=True):
            return True
        cc = rf.COUNTRY_CODE.get(country)
        if not cc:
            return False
        for lang in ["en"] + (["ja"] if country == "일본" else []):
            if rf._search(name, cc, lang):
                return True
    except Exception:
        return False
    return False


def quote_ok(where, raw, text):
    """(인정 여부, 이유). text = 언론사 이름을 뗀 기사 글(제목 + 관련 보도)."""
    q = str(raw.get("place_quote") or "").strip().strip('"“”')
    if len(_norm_place(q)) < 3:
        return False, "장소 근거 인용문 없음"
    if _norm_place(q) not in _norm_place(text):
        return False, f"인용문이 기사에 없음({q[:30]})"
    dst = supported_country(where)
    if not dst:
        return True, ""                        # 지원 나라가 아니면 어차피 보내지 않음(기록만)
    if any(_name_in(n, q) for n in country_names(dst)):
        return True, ""
    if dst in ("미국", "영국") and re.search(r"(?<![A-Za-z])(US|UK)(?![A-Za-z])", q):   # 대문자로만(영어 'us' 와 구분)
        return True, ""
    hit = abbr_hit(dst, q)                     # 줄임말·현지 약칭(ロス=로스앤젤레스, KL=쿠알라룸푸르 …)
    if hit:
        raw["_hint"] = (hit[1], hit[2])
        return True, f"줄임말 {hit[0]} = {hit[1] or hit[2]}"
    parts = [p.strip() for p in re.split(r"[,/;·、]+", str(raw.get("place_text") or "")) if p.strip()]
    for p in parts:
        if _norm_place(p) in _norm_place(q) and _place_in_country(p, dst):
            return True, ""
    return False, f"인용문에 {dst} 이름이나 {dst}의 도시·주 이름이 없음({q[:30]})"


def blind_place(it, cfg):
    """언론사·나라를 가린 채 '제목(+관련 보도)'만으로 사고 나라를 물음. 반환: (나라 또는 '', 근거 지명)."""
    title = re.sub(r"\s+[-–|]\s+[^-–|]{1,40}$", "", it.get("title", "")).strip()
    bare = {"country": "", "ko": "", "title": title, "src_name": "", "pub": "", "context": it.get("context", "")}
    res, _m = _ask("장소확인", [bare], cfg)
    ans, why, where, raw = res.get(0, ("no", "", "", {}))
    if not raw:
        raise AIError("장소 확인 답 누락")
    probe = {"title": title, "ko": "", "context": it.get("context", "")}
    if place_unknown(where, raw, probe):
        return "", ""
    it["_place_quote"] = str(raw.get("place_quote") or "").strip()
    ok, why = quote_ok(where, raw, f"{title} || {it.get('context', '')}")
    if ok and raw.get("_hint"):
        it["_place_hint"] = raw["_hint"]
    if not ok:
        it["_quote_fail"] = why                  # 기록용(왜 장소를 인정하지 않았는지)
        return "", ""
    return where, str(raw.get("place_text") or "")


def find_place(kind, it, cfg, where, raw):
    """사고 장소 확정. 반환: (나라, raw, 설명). 끝내 불명이면 나라=''. AI 호출 실패는 AIError.
    ① 언론사·나라를 가리고 제목만으로 장소를 물음 → ② 불명이면 '관련 보도'(같은 사건의 다른 제목들)를 가져와 다시 물음
    ③ 그래도 불명이면 보내지 않음. 확정된 나라는 '가리고 물은 답'을 씀(신문 나라로 짐작한 답은 쓰지 않음)."""
    w, pt = blind_place(it, cfg)
    note = ""
    if not w:
        fn = cfg.get("_context_fn")
        ctx = ""
        if fn:
            try:
                ctx = fn(it) or ""
            except Exception:
                ctx = ""
        if not ctx:
            return "", raw, "제목에 장소가 없고 관련 보도도 찾지 못함" + (f" ({it['_quote_fail']})" if it.get("_quote_fail") else "")
        it["context"] = ctx
        w, pt = blind_place(it, cfg)
        if not w:
            return "", raw, "제목에 장소가 없고, 관련 보도를 보고도 장소를 확정하지 못함" + (f" ({it['_quote_fail']})" if it.get("_quote_fail") else "")
        res, _m = _ask(kind, [it], cfg)                    # 관련 보도를 붙여 숫자·내용도 다시 받음
        ans, why, where2, raw2 = res.get(0, ("no", "", "", {}))
        if raw2:
            raw = raw2
        note = f"제목에 장소가 없어 관련 보도로 확인: {pt[:24]}"
    return w, raw, note


def _country_key(where):
    return supported_country(where) or (where or "").replace(" ", "")


def recheck_incident(it, cfg, where1, raw1):
    """반환: (상태, 나라, raw, 설명). 상태 = '일치'(쓸 답이 정해짐) / '불일치'. AI 호출이 실패하면 AIError.
    두 답의 '나라'가 다르면 세 번째를 묻지 않고 바로 불일치(보내지 않음)."""
    answers = [(where1, raw1)]
    for _ in range(2):                                # 2번째, (필요하면) 3번째
        res, _m = _ask("대형사고", [it], cfg)
        ans, why, where, raw = res.get(0, ("no", "", "", {}))
        if not raw:
            raise AIError("다시 물은 답 누락")
        if where and where not in ("모름", "장소 불명") and _country_key(where) != _country_key(where1):
            return "불일치", "", {}, f"두 답의 나라가 다름({where1 or '불명'} / {where or '불명'})"
        if not answers[1:]:                           # 1차 답의 나라도 확정된 나라(가리고 물은 답)로 맞춰 비교
            answers[0] = (where1, raw1)
        answers.append((where1, raw))
        keys = [_inc_key(w, r) for w, r in answers]
        for w, r in reversed(answers):                # 2번 이상 나온 답이 있으면 그 답(가장 나중에 받은 것)을 씀
            if keys.count(_inc_key(w, r)) >= 2:
                return "일치", w, r, f"{len(answers)}번 물어 같은 답 {keys.count(_inc_key(w, r))}번"
    desc = " / ".join(f"사망·실종 {k[0]}·부상 {k[1]}·{k[2] or '나라 모름'}·발생일 {k[3] or '모름'}" for k in keys)
    return "불일치", "", {}, desc


def settle_country(it, where):
    """장소가 확정된 뒤: (보낼지, 설명). 다른 지원 나라면 그 나라 소식으로 옮기고, 지원 나라가 아니면 기록만."""
    feed = it.get("country", "")
    if _same_country(where, feed):
        return True, ""
    dst = supported_country(where)
    if dst:
        it["moved_from"], it["country"] = feed, dst
        return True, f"[옮김: {feed} 뉴스 → {dst}] "
    return False, f"다른 나라({where}) 사고 — {feed} 사고가 아니고 지원 나라도 아니라 기록만 남김"


def judge(kind, items, cfg=None, use_pending=True, hold_on_fail=True, write_log=True):
    """items(각 dict에 country·ko·title·src_name·pub·guid) 중 AI가 yes 한 것만 돌려줌.
    kind: "정책" 또는 "대형사고".
    - enabled=false → 판단 없이 전부 그대로 돌려줌(예전 동작).
    - 실패 → 해당 기사는 보내지 않고 '보류' 기록 + 다음 실행 때 재시도.
    반환: (통과 목록, 결과 dict{guid: (answer, reason)})"""
    cfg = dict(cfg or load_config())
    if not cfg.get("enabled", True):
        return list(items), {}
    items = list(items)
    if use_pending:
        have = {it.get("guid") for it in items}
        items += [it for it in take_pending(kind) if it.get("guid") not in have]
    # 한 번 실행에 확인할 기사 수 상한(_max_items) — 넘는 것은 보류 목록으로 넘겨 다음 실행에 (2026-10-16)
    cap = int(cfg.get("_max_items") or 0)
    if cap and len(items) > cap:
        later, items = items[cap:], items[:cap]
        _hold(kind, later, f"한 번에 확인하는 수({cap}건) 초과로 다음 실행에 미룸")
        print(f"  (AI판단 {kind}: 이번에는 {cap}건만 확인 — {len(later)}건은 다음 실행에서 확인)")
    deadline = float(cfg.get("_deadline") or 0)       # 이 시각이 지나면 새로 묻지 않고 남은 기사는 다음 실행으로

    def _late(rest):
        """시간이 다 됐으면 rest 를 보류 목록에 넣고 True."""
        if not deadline or time.time() < deadline:
            return False
        for x in rest:
            results[x.get("guid")] = ("보류", "시간 부족 — 다음 실행에서 확인")
        _hold(kind, rest, "실행 시간 예산 초과로 다음 실행에 미룸")
        return True
    recent = _load_recent() if kind == "정책" else []
    cfg["_recent"] = recent
    passed, results = [], {}
    moved = []                                   # 다른 나라 규정 기사: (기사, 원래 뉴스 나라, 대상 나라)
    # 정책은 한 번에 10건씩만 물음(25건씩 물으면 기준을 느슨하게 적용하는 것이 확인됨 — 2026-10-13 시험)
    bsize = POLICY_BATCH if kind == "정책" else BATCH_SIZE
    late_n = 0
    for s in range(0, len(items), bsize):
        chunk = items[s:s + bsize]
        if _late(items[s:]):
            late_n += len(items[s:])
            break
        try:
            if cfg.get("_quota"):
                raise QuotaError(cfg["_quota"])           # 이번 실행에서 이미 한도 초과 → 더 부르지 않고 보류
            res, model = _ask(kind, chunk, cfg)
        except AIError as e:
            if isinstance(e, QuotaError):
                cfg["_quota"] = str(e)
            for it in chunk:
                results[it.get("guid")] = ("보류", str(e))
                if write_log:
                    _log(kind, it, "보류", f"AI 확인 실패 → 안 보냄: {e}", "")
            if hold_on_fail:
                _hold(kind, chunk, f"AI 확인 실패: {e}")
            continue
        for i, it in enumerate(chunk):
            ans, why, where, raw = res.get(i, ("no", "AI 답 누락 → 안전하게 no", "", {}))
            ans = "yes" if ans == "yes" else "no"           # 이상한 답은 전부 no
            # 정책: 다른 나라 규정이 실린 기사 → 버리지 않고 '실제 대상 나라' 소식으로 옮겨 다시 판정(아래 judge_moved)
            if (kind == "정책" and where and where != "모름" and not _same_country(where, it.get("country", ""))
                    and str(raw.get("topic") or "해당없음") != "해당없음"):
                dst = supported_country(where)
                if dst:
                    moved.append((it, it.get("country", ""), dst))
                    continue                              # 결과·기록은 옮겨서 판정한 뒤에 남김
                ans, why = "no", f"다른 나라({where}) 규정 — 지원 나라가 아니라 기록만 남김 — {why}"
            # 이중 확인: AI가 적은 '나라'가 피드 나라와 다르면 yes 여도 no (다른 나라 기사 차단) — 정책만.
            #  대형사고·산사태는 아래에서 장소를 따로 확인(장소 불명 → 관련 보도 확인, 다른 지원 나라면 그 나라로 옮김).
            if kind == "정책" and ans == "yes" and not _same_country(where, it.get("country", "")):
                ans, why = "no", f"나라 불일치(AI 판단 장소: {where or '모름'}) — {why}"
            summary = str(raw.get("summary") or "").strip()
            if kind == "대형사고" and raw and incident_tier(raw) and not raw.get("followup") \
                    and (ans == "yes" or raw.get("incident")):
                # 기준을 넘은 후보 → ① 장소 확인 ② 1건씩 다시 물어 숫자·나라·발생일이 같은지 ③ 어느 나라 소식인지 정함
                if _late([it]):
                    late_n += 1
                    continue
                try:
                    where, raw, pnote = find_place(kind, it, cfg, where, raw)
                    if where:
                        state, where2, raw2, note = recheck_incident(it, cfg, where, raw)
                    else:
                        state = "장소 불명"
                except AIError as e:
                    results[it.get("guid")] = ("보류", f"다시 묻기 실패: {e}")
                    if write_log:
                        _log(kind, it, "보류", f"다시 묻기(AI) 실패 → 안 보냄: {e}", "")
                    if hold_on_fail:
                        _hold(kind, [it], f"AI 확인 실패: {e}")
                    continue
                if state == "장소 불명":
                    results[it.get("guid")] = ("no", f"장소 불명 — {pnote}")
                    if write_log:
                        _log(kind, it, "장소 불명", f"{pnote} → 보내지 않음", model)
                    continue
                if state == "불일치":
                    results[it.get("guid")] = ("no", f"판정 불일치 — {note}")
                    if write_log:
                        _log(kind, it, "판정 불일치", f"다시 물은 답이 다름 → 보내지 않음: {note}", model)
                    continue
                where, raw = where2, raw2
                summary = str(raw.get("summary") or "").strip() or summary
                why = f"({note}{'; ' + pnote if pnote else ''}) {why}"
                if not (str(raw.get("answer") or "").strip().lower() == "yes" or raw.get("incident")):
                    ans, why = "no", f"다시 물었을 때 사고 소식이 아니라고 답함 — {why}"
                else:
                    go, tag = settle_country(it, where)
                    ans = "yes" if go else "no"
                    why = (tag + why) if go else f"{tag} — {why}"
            elif kind == "대형사고" and ans == "yes" and not _same_country(where, it.get("country", "")):
                ans, why = "no", f"나라 불일치(AI 판단 장소: {where or '모름'}) — {why}"
            if ans == "yes" and kind == "대형사고":
                d, inj = int(raw.get("deaths") or 0), int(raw.get("injured") or 0)
                tier = incident_tier(raw)
                if raw.get("followup"):
                    ans, why = "no", f"후속 기사(발생 소식 아님) — {why}"
                elif not tier:
                    ans, why = "no", f"규모 기준 미달(사망·실종 {d}, 부상 {inj}) — {why}"
                else:
                    it["ai"] = {"summary": summary, "deaths": d, "injured": inj, "tier": tier,
                                "transport": bool(raw.get("transport")), "etype": str(raw.get("etype") or "기타"),
                                **_place_fields(it, raw)}
                    why = f"[{tier} · 사망·실종 {d}, 부상 {inj}] {why}"
            if kind == "산사태" and raw:
                # 통과 여부는 AI 말(answer)이 아니라 칸으로 계산. 기준을 넘으면 장소를 확인하고, 1건 다시 물어 나라가 같은지 봄.
                ok_ls, why_ls = landslide_verdict(raw)
                if not ok_ls:
                    ans, why = "no", f"{why_ls} — {why}"
                elif _late([it]):
                    late_n += 1
                    continue
                else:
                    try:
                        where, raw, pnote = find_place(kind, it, cfg, where, raw)
                        state = "장소 불명" if not where else ""
                        where_main = res.get(i, ("", "", "", {}))[2]
                        if where and it.get("context") is None and where_main and where_main not in ("모름", "장소 불명") \
                                and _country_key(where_main) != _country_key(where) and _country_key(where_main) != _country_key(it.get("country", "")):
                            state, pnote = "불일치", f"두 답의 나라가 다름({where} / {where_main})"
                    except AIError as e:
                        results[it.get("guid")] = ("보류", f"다시 묻기 실패: {e}")
                        if write_log:
                            _log(kind, it, "보류", f"다시 묻기(AI) 실패 → 안 보냄: {e}", "")
                        if hold_on_fail:
                            _hold(kind, [it], f"AI 확인 실패: {e}")
                        continue
                    if state:
                        results[it.get("guid")] = ("no", f"{'장소 불명' if state == '장소 불명' else '판정 불일치'} — {pnote}")
                        if write_log:
                            _log(kind, it, "장소 불명" if state == "장소 불명" else "판정 불일치", f"{pnote} → 보내지 않음", model)
                        continue
                    summary = str(raw.get("summary") or "").strip() or summary
                    ok_ls, why_ls = landslide_verdict(raw)                    # 관련 보도로 다시 받은 칸으로 한 번 더 계산
                    d, inj, mv = int(raw.get("deaths") or 0), int(raw.get("injured") or 0), int(raw.get("moved") or 0)
                    other = _other_country_in(summary, it.get("country", "")) if _same_country(where, it.get("country", "")) else ""
                    if not ok_ls:
                        ans, why = "no", f"{why_ls} — {why}"
                    elif other and supported_country(other) != supported_country(where):
                        ans, why = "no", f"나라 불일치(요약에 다른 나라: {other}) — {why}"
                    else:
                        go, tag = settle_country(it, where)
                        if go:
                            ans = "yes"
                            it["ai"] = {"summary": summary, "deaths": d, "injured": inj, "moved": mv,
                                        "moved_kind": str(raw.get("moved_kind") or ""), "evacuation": mv > 0,
                                        "tier": "초대형" if d >= 20 else "산사태",
                                        **_place_fields(it, raw)}
                            why = f"{tag}[산사태 · {why_ls}{'; ' + pnote if pnote else ''}] {why}"
                        else:
                            ans, why = "no", f"{tag} — {why}"
            if ans == "yes" and kind == "정책" and str(raw.get("topic") or "해당없음") == "해당없음":
                ans, why = "no", f"유학생 가족 대상 주제가 아님 — {why}"       # AI가 yes 라 해도 주제가 없으면 막음
            if ans == "yes" and kind == "정책" and _excluded_policy(f"{it.get('title', '')} {it.get('ko', '')} {summary}"):
                ans, why = "no", f"제외 주제(영주권·시민권·단속 등)만 다룸 — {why}"   # 글자로 한 번 더 막음
            if ans == "yes" and kind == "정책":
                why = f"[{raw.get('topic')}] {why}"
                if raw.get("dup"):
                    ans, why = "no", f"이미 보낸 소식과 같은 내용 — {why}"
                else:
                    try:
                        ok2, why2, _topic2, _sum2 = confirm_policy(it, cfg)      # 1건씩 다시 확인(둘 다 통과해야 발송)
                    except AIError as e:
                        results[it.get("guid")] = ("보류", f"재확인 실패: {e}")
                        if write_log:
                            _log(kind, it, "보류", f"재확인(AI) 실패 → 안 보냄: {e}", "")
                        if hold_on_fail:
                            _hold(kind, [it], f"AI 확인 실패: {e}")
                        continue
                    if not ok2:
                        ans, why = "no", why2
                    else:                                         # 재확인에서 붙인 표시(옮김·날짜)를 기록 이유에도 남김
                        mv = re.match(r"^(\[옮김: 규정을 바꾼 정부[^\]]*\])", why2)
                        tag = re.search(r"\[(?:날짜 불명|변화일 [^\]]+)\]", why2)
                        why = (mv.group(1) + " " if mv else "") + why + (" " + tag.group(0) if tag else "")
                if ans == "yes":
                    it["ai"] = {"summary": summary, "topic": str(raw.get("topic") or "")}
                    if summary:
                        recent.append({"country": it.get("country", ""), "summary": summary,
                                       "date": f"{datetime.now():%m/%d}", "t": time.time()})
            results[it.get("guid")] = (ans, why)
            if write_log:
                _log(kind, it, ans, why + (f" ⇒ {summary}" if ans == "yes" and summary else ""), model)
            if ans == "yes":
                passed.append(it)
    if moved:                                    # 대상 나라의 '이미 보낸 소식'(방금 통과한 것 포함)과 견주어 1건씩 판정
        judge_moved(moved, cfg, recent, results, passed, write_log, hold_on_fail)
    if kind == "정책" and write_log:          # 테스트(write_log=False) 때는 '보낸 소식' 기록을 건드리지 않음
        _save_recent(recent)
    return passed, results
