# -*- coding: utf-8 -*-
"""
나라정보.py  ── 30개국의 국기·대표도시·좌표·시간대 + 날씨/현지시각 도우미

다른 프로그램(아침브리핑 등)이 가져다 씁니다.
- get_weather("일본")     → 도쿄 오늘 날씨 (open-meteo, 무료·키 불필요)
- local_kst_str("일본")   → "현지 8/25 08:00 (한국 8/25 08:00)"
"""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen

# 나라 → (국기, 대표도시, 위도, 경도, 시간대)
COUNTRY_INFO = {
    "일본": ("🇯🇵", "도쿄", 35.68, 139.69, "Asia/Tokyo"),
    "베트남": ("🇻🇳", "하노이", 21.03, 105.85, "Asia/Ho_Chi_Minh"),
    "대만": ("🇹🇼", "타이베이", 25.03, 121.57, "Asia/Taipei"),
    "태국": ("🇹🇭", "방콕", 13.75, 100.50, "Asia/Bangkok"),
    "필리핀": ("🇵🇭", "마닐라", 14.60, 120.98, "Asia/Manila"),
    "말레이시아": ("🇲🇾", "쿠알라룸푸르", 3.14, 101.69, "Asia/Kuala_Lumpur"),
    "싱가포르": ("🇸🇬", "싱가포르", 1.35, 103.82, "Asia/Singapore"),
    "인도네시아": ("🇮🇩", "자카르타", -6.21, 106.85, "Asia/Jakarta"),
    "미국": ("🇺🇸", "뉴욕", 40.71, -74.01, "America/New_York"),
    "캐나다": ("🇨🇦", "토론토", 43.65, -79.38, "America/Toronto"),
    "호주": ("🇦🇺", "시드니", -33.87, 151.21, "Australia/Sydney"),
    "영국": ("🇬🇧", "런던", 51.51, -0.13, "Europe/London"),
    "독일": ("🇩🇪", "베를린", 52.52, 13.40, "Europe/Berlin"),
    "프랑스": ("🇫🇷", "파리", 48.85, 2.35, "Europe/Paris"),
    "인도": ("🇮🇳", "뉴델리", 28.61, 77.21, "Asia/Kolkata"),
    "이탈리아": ("🇮🇹", "로마", 41.90, 12.50, "Europe/Rome"),
    "스페인": ("🇪🇸", "마드리드", 40.42, -3.70, "Europe/Madrid"),
    "네덜란드": ("🇳🇱", "암스테르담", 52.37, 4.90, "Europe/Amsterdam"),
    "스위스": ("🇨🇭", "취리히", 47.38, 8.54, "Europe/Zurich"),
    "브라질": ("🇧🇷", "상파울루", -23.55, -46.63, "America/Sao_Paulo"),
    "멕시코": ("🇲🇽", "멕시코시티", 19.43, -99.13, "America/Mexico_City"),
    "뉴질랜드": ("🇳🇿", "오클랜드", -36.85, 174.76, "Pacific/Auckland"),
    "러시아": ("🇷🇺", "모스크바", 55.76, 37.62, "Europe/Moscow"),
    "스웨덴": ("🇸🇪", "스톡홀름", 59.33, 18.07, "Europe/Stockholm"),
    "튀르키예": ("🇹🇷", "이스탄불", 41.01, 28.98, "Europe/Istanbul"),
    "포르투갈": ("🇵🇹", "리스본", 38.72, -9.14, "Europe/Lisbon"),
    "폴란드": ("🇵🇱", "바르샤바", 52.23, 21.01, "Europe/Warsaw"),
    "오스트리아": ("🇦🇹", "빈", 48.21, 16.37, "Europe/Vienna"),
    "벨기에": ("🇧🇪", "브뤼셀", 50.85, 4.35, "Europe/Brussels"),
    "아르헨티나": ("🇦🇷", "부에노스아이레스", -34.60, -58.38, "America/Argentina/Buenos_Aires"),
}

# 날씨 코드(WMO) → (한국어, 이모지)
WMO = {
    0: ("맑음", "☀️"), 1: ("대체로 맑음", "🌤️"), 2: ("부분 흐림", "⛅"), 3: ("흐림", "☁️"),
    45: ("안개", "🌫️"), 48: ("안개", "🌫️"),
    51: ("이슬비", "🌦️"), 53: ("이슬비", "🌦️"), 55: ("이슬비", "🌦️"),
    56: ("어는 이슬비", "🌧️"), 57: ("어는 이슬비", "🌧️"),
    61: ("비", "🌧️"), 63: ("비", "🌧️"), 65: ("강한 비", "🌧️"),
    66: ("어는 비", "🌧️"), 67: ("어는 비", "🌧️"),
    71: ("눈", "🌨️"), 73: ("눈", "🌨️"), 75: ("강한 눈", "🌨️"), 77: ("싸락눈", "🌨️"),
    80: ("소나기", "🌦️"), 81: ("소나기", "🌦️"), 82: ("강한 소나기", "⛈️"),
    85: ("소나기눈", "🌨️"), 86: ("소나기눈", "🌨️"),
    95: ("뇌우", "⛈️"), 96: ("우박 뇌우", "⛈️"), 99: ("우박 뇌우", "⛈️"),
}


# 영어 나라명 → 한국어 키 (GDACS 등 영어 소스가 번역 실패해도 시간대 찾도록)
ENGLISH_TO_KR = {
    "japan": "일본", "vietnam": "베트남", "taiwan": "대만", "thailand": "태국",
    "philippines": "필리핀", "malaysia": "말레이시아", "singapore": "싱가포르",
    "indonesia": "인도네시아", "united states": "미국", "usa": "미국", "canada": "캐나다",
    "australia": "호주", "united kingdom": "영국", "uk": "영국", "germany": "독일",
    "france": "프랑스", "india": "인도", "italy": "이탈리아", "spain": "스페인",
    "netherlands": "네덜란드", "switzerland": "스위스", "brazil": "브라질",
    "mexico": "멕시코", "new zealand": "뉴질랜드", "russia": "러시아", "sweden": "스웨덴",
    "turkey": "튀르키예", "türkiye": "튀르키예", "portugal": "포르투갈", "poland": "폴란드",
    "austria": "오스트리아", "belgium": "벨기에", "argentina": "아르헨티나",
}


# 지원 30개국 밖 나라의 영어 이름 → 한국어 (지진·재난 알림 머리글용. 번역기를 쓰지 않고 이 표만 씀)
WORLD_KR = {
    "russian federation": "러시아", "united states of america": "미국", "viet nam": "베트남",
    "china": "중국", "nepal": "네팔", "north korea": "북한", "south korea": "대한민국",
    "democratic people's republic of korea": "북한", "republic of korea": "대한민국",
    "mongolia": "몽골", "myanmar": "미얀마", "burma": "미얀마", "laos": "라오스",
    "lao people's democratic republic": "라오스", "cambodia": "캄보디아", "bangladesh": "방글라데시",
    "sri lanka": "스리랑카", "pakistan": "파키스탄", "afghanistan": "아프가니스탄", "bhutan": "부탄",
    "maldives": "몰디브", "kazakhstan": "카자흐스탄", "kyrgyzstan": "키르기스스탄", "tajikistan": "타지키스탄",
    "uzbekistan": "우즈베키스탄", "turkmenistan": "투르크메니스탄", "iran": "이란",
    "islamic republic of iran": "이란", "iraq": "이라크", "syria": "시리아", "lebanon": "레바논",
    "israel": "이스라엘", "jordan": "요르단", "saudi arabia": "사우디아라비아", "yemen": "예멘",
    "oman": "오만", "united arab emirates": "아랍에미리트", "qatar": "카타르", "kuwait": "쿠웨이트",
    "georgia": "조지아", "armenia": "아르메니아", "azerbaijan": "아제르바이잔", "cyprus": "키프로스",
    "timor leste": "동티모르", "timor-leste": "동티모르", "east timor": "동티모르", "brunei": "브루나이",
    "papua new guinea": "파푸아뉴기니", "fiji": "피지", "tonga": "통가", "vanuatu": "바누아투",
    "samoa": "사모아", "solomon islands": "솔로몬 제도", "new caledonia": "뉴칼레도니아",
    "micronesia": "미크로네시아", "federated states of micronesia": "미크로네시아", "palau": "팔라우",
    "wallis and futuna": "왈리스 푸투나", "guam": "미국 괌", "northern mariana islands": "북마리아나 제도",
    "greece": "그리스", "albania": "알바니아", "romania": "루마니아", "bulgaria": "불가리아",
    "ukraine": "우크라이나", "ireland": "아일랜드", "iceland": "아이슬란드", "norway": "노르웨이",
    "finland": "핀란드", "denmark": "덴마크", "czechia": "체코", "czech republic": "체코",
    "hungary": "헝가리", "slovakia": "슬로바키아", "slovenia": "슬로베니아", "croatia": "크로아티아",
    "serbia": "세르비아", "montenegro": "몬테네그로", "bosnia and herzegovina": "보스니아 헤르체고비나",
    "north macedonia": "북마케도니아", "lithuania": "리투아니아", "latvia": "라트비아", "estonia": "에스토니아",
    "belarus": "벨라루스", "moldova": "몰도바", "svalbard and jan mayen": "스발바르 얀마옌",
    "chile": "칠레", "peru": "페루", "bolivia": "볼리비아", "colombia": "콜롬비아", "ecuador": "에콰도르",
    "venezuela": "베네수엘라", "paraguay": "파라과이", "uruguay": "우루과이", "suriname": "수리남",
    "guatemala": "과테말라", "honduras": "온두라스", "el salvador": "엘살바도르", "nicaragua": "니카라과",
    "costa rica": "코스타리카", "panama": "파나마", "cuba": "쿠바", "haiti": "아이티", "jamaica": "자메이카",
    "dominican republic": "도미니카 공화국", "puerto rico": "미국 푸에르토리코", "guadeloupe": "과들루프",
    "martinique": "마르티니크", "antigua and barbuda": "앤티가 바부다", "montserrat": "몬트세랫",
    "saint kitts and nevis": "세인트키츠 네비스", "anguilla": "앵귈라",
    "egypt": "이집트", "libya": "리비아", "tunisia": "튀니지", "algeria": "알제리", "morocco": "모로코",
    "sudan": "수단", "south sudan": "남수단", "ethiopia": "에티오피아", "somalia": "소말리아", "kenya": "케냐",
    "tanzania": "탄자니아", "uganda": "우간다", "mozambique": "모잠비크", "angola": "앙골라", "zambia": "잠비아",
    "zimbabwe": "짐바브웨", "namibia": "나미비아", "botswana": "보츠와나", "south africa": "남아프리카공화국",
    "madagascar": "마다가스카르", "malawi": "말라위", "nigeria": "나이지리아", "ghana": "가나",
    "senegal": "세네갈", "mali": "말리", "guinea": "기니", "chad": "차드", "mauritania": "모리타니",
    "cape verde": "카보베르데", "cote d'ivoire": "코트디부아르", "congo": "콩고",
    "democratic republic of congo": "콩고민주공화국", "the democratic republic of congo": "콩고민주공화국",
    "democratic republic of the congo": "콩고민주공화국",
}


def country_ko(name):
    """영어 나라 이름 → 한국어(지원 30개국 + 위 표). 모르면 None. 번역기를 쓰지 않음."""
    key = (name or "").strip().lower()
    if not key:
        return None
    for k in (key, key[4:] if key.startswith("the ") else key):
        if k in ENGLISH_TO_KR:
            return ENGLISH_TO_KR[k]
        if k in WORLD_KR:
            return WORLD_KR[k]
    return None


def _resolve(country):
    """한국어면 그대로, 영어면 한국어로. 못 찾으면 원래대로."""
    if country in COUNTRY_INFO:
        return country
    return ENGLISH_TO_KR.get((country or "").strip().lower(), country)


def flag(country):
    info = COUNTRY_INFO.get(_resolve(country))
    return info[0] if info else "🌍"


def get_weather(country):
    """그 나라 대표도시의 현재 날씨. 실패하면 None. (출처: open-meteo)"""
    info = COUNTRY_INFO.get(_resolve(country))
    if not info:
        return None
    _, city, lat, lon, tz = info
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&current=temperature_2m,weather_code&timezone={tz}")
    try:
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
            d = json.loads(r.read().decode("utf-8"))
        cur = d.get("current", {})
        temp = cur.get("temperature_2m")
        desc, emoji = WMO.get(cur.get("weather_code"), ("날씨정보", "🌡️"))
        return {"city": city, "temp": temp, "desc": desc, "emoji": emoji, "source": "open-meteo"}
    except Exception:
        return None


def local_kst_str(country, dt_utc=None):
    """'현지 8/25 08:00 (한국 8/25 08:00)' 형태. dt_utc 없으면 지금."""
    if dt_utc is None:
        dt_utc = datetime.now(timezone.utc)
    kst = dt_utc.astimezone(ZoneInfo("Asia/Seoul"))
    kst_s = f"{kst.month}/{kst.day} {kst:%H:%M}"
    info = COUNTRY_INFO.get(_resolve(country))
    if not info:
        return f"한국 {kst_s}"
    loc = dt_utc.astimezone(ZoneInfo(info[4]))
    loc_s = f"{loc.month}/{loc.day} {loc:%H:%M}"
    if loc_s == kst_s:          # 한국과 시차 없는 나라(일본 등)면 시각 한 번만
        return f"현지 {loc_s}"
    return f"현지 {loc_s} (한국 {kst_s})"
