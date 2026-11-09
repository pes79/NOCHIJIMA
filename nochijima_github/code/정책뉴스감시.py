# -*- coding: utf-8 -*-
"""
정책뉴스감시.py
"각 나라의 비자·체류·면허·행정 규정 변경 뉴스"를 감지하는 프로그램

■ 누구를 위한 것
  - 여행객이 아니라, 그 나라에 '연결된' 사람들
    (국제커플, 유학생과 부모, 해외에 가족·지인 있는 사람)
  - "몰라서 손해 보는" 실생활 규정 변화를 남보다 먼저 알려주는 것이 목표

■ 신뢰성 3단 장치 (가짜·광고·오보 차단)
  1) 출처 화이트리스트 : 미리 정한 신뢰 언론/정부 도메인 기사만 통과
  2) 주제 키워드       : 비자·재류·외국인·영주·운전면허 등으로 애초에 좁혀 수집
  3) 정책 신호어 태그   : 완화/강화/개정/검토/방침/도입 등 → "📌 정책 변화 신호" 표시
                        (확정 발표뿐 아니라 '검토·조짐' 단계도 잡기 위함)

■ 소스 : 구글뉴스 RSS (나라별 언어로 키워드 검색, 출처 정보 포함)
■ 번역 : 무료 구글 번역(키 불필요). 실서비스는 Papago 업그레이드 권장.
■ 중복 : 이미 알린 기사는 seen_news.json에 기록해 다시 안 알림.
■ 2단계 : 키워드 통과 기사는 AI판단.py(제미나이)가 "이 나라의 실제 규정 변화"인지 최종 확인.
         (gemini_config.json 의 enabled=false 면 예전처럼 키워드만)

※ 나라 추가/변경은 아래 COUNTRIES 목록에 한 덩어리만 추가하면 됩니다.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from urllib.request import Request, urlopen

import 번역도우미 as tr                # 번역 간격·저장·차단 감지
from 나라정보 import local_kst_str   # 현지시각(현지 X / 한국 Y) 변환

# ══════════════════════════════════════════════════════════════════
#  나라 설정  ── 여기만 고치면 나라를 추가/변경할 수 있습니다
# ══════════════════════════════════════════════════════════════════
#  name_kr : 화면에 쓸 한국어 이름
#  lang    : 뉴스 언어코드 (번역할 때 '원본 언어'로도 사용)
#  gl,ceid : 구글뉴스 지역/에디션 (언어와 맞춰야 함)
#  query   : 그 나라 언어로 된 검색 키워드 (OR 로 연결)
#  trusted : 신뢰 소스 목록 (언론사 이름 또는 도메인 일부). 여기 있는 곳 기사만 통과!
COUNTRIES = [
    {
        "name_kr": "일본",
        "lang": "ja", "gl": "JP", "ceid": "JP:ja",
        "query": "ビザ OR 在留資格 OR 在留 OR 外国人 OR 永住 OR 運転免許 OR 入管",
        "trusted": [
            "nhk.or.jp", "nhk", "読売", "yomiuri", "朝日", "asahi",
            "日本経済新聞", "nikkei", "時事", "jiji", "共同", "kyodo",
            "毎日", "mainichi", "産経", "sankei", ".go.jp",
        ],
    },
    {
        "name_kr": "베트남",
        "lang": "vi", "gl": "VN", "ceid": "VN:vi",
        "query": "visa OR thị thực OR cư trú OR người nước ngoài OR thường trú OR bằng lái",
        "trusted": [
            "vnexpress", "tuoitre", "thanhnien", "vietnamplus", "vtv",
            "baochinhphu", "nhandan", "laodong", "vietnamnet", ".gov.vn",
        ],
    },
    {
        "name_kr": "대만",
        "lang": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant",
        "query": "簽證 OR 居留 OR 居留證 OR 外國人 OR 永久居留 OR 駕照 OR 移民署",
        "trusted": [
            "cna.com.tw", "中央社", "pts.org.tw", "公視", "udn",
            "聯合報", "自由時報", "ltn.com.tw", "中時", ".gov.tw",
        ],
    },
    {
        "name_kr": "미국",
        "lang": "en-US", "gl": "US", "ceid": "US:en",
        "query": 'visa OR immigration OR "green card" OR citizenship OR asylum OR deportation OR USCIS OR naturalization OR residency OR "work permit"',
        "trusted": [
            # 통신사 (사실보도의 기준)
            "apnews", "associated press", "reuters",
            # 전국 대표 신문
            "nytimes", "new york times", "washingtonpost", "wsj", "wall street journal",
            "usatoday", "usa today", "latimes", "los angeles times",
            # 공영·방송 뉴스
            "npr", "nbcnews", "nbc news", "abcnews.go.com", "abc news",
            "cbsnews", "cbs news", "pbs.org", "cnn", "cnbc",
            # 정치·정책 전문 (워싱턴 정가 표준)
            "politico", "thehill", "the hill", "axios", "bloomberg",
            # 정부 공식
            "uscis.gov", "state.gov", "dhs.gov", ".gov",
        ],
    },
    # ※ 중국은 제거했습니다 — 구글이 중국 내에서 차단되어 데이터가 부실하고,
    #    신뢰 소스가 관영매체뿐이며 잡히는 뉴스도 '남의 나라' 얘기가 많았음.
    #    (다시 넣고 싶으면 다른 나라 덩어리를 참고해 { ... } 블록을 추가하면 됩니다.)
    {
        "name_kr": "태국",
        "lang": "th", "gl": "TH", "ceid": "TH:th",
        "query": "วีซ่า OR ผู้พำนัก OR ชาวต่างชาติ OR ใบขับขี่ OR ตรวจคนเข้าเมือง OR ถิ่นที่อยู่",
        "trusted": [
            "bangkokpost", "bangkok post", "nationthailand", "the nation",
            "thaipbs", "thai pbs", "mcot", "thaigov", "prd.go.th", ".go.th","ไทยพีบีเอส", "มติชน", "matichon", "ไทยรัฐ", "thairath", "กรุงเทพธุรกิจ", "bangkokbiznews", "ประชาชาติ", "prachachat", "ข่าวสด", "khaosod", "เดลินิวส์", "dailynews", "สำนักข่าวไทย", "อินโฟเควสท์", "infoquest", "โพสต์ทูเดย์", "posttoday", "pptv", "กรมประชาสัมพันธ์"],
    },
    {
        "name_kr": "필리핀",
        "lang": "en-PH", "gl": "PH", "ceid": "PH:en",
        "query": 'visa OR immigration OR "alien registration" OR residency OR "driver''s license" OR "work permit"',
        "trusted": [
            "inquirer", "gmanetwork", "gma news", "abs-cbn", "philstar",
            "manilatimes", "manila times", "manilabulletin", "rappler",
            "pna.gov.ph", "philippine news agency", "immigration.gov.ph",
            "bureau of immigration", ".gov.ph",
        ],
    },
    {
        "name_kr": "호주",
        "lang": "en-AU", "gl": "AU", "ceid": "AU:en",
        "query": 'visa OR immigration OR migration OR residency OR "permanent resident" OR "driver licence"',
        "trusted": [
            "abc.net.au", "australian broadcasting", "sbs.com.au", "sbs news",
            "theguardian", "the guardian", "smh.com.au", "sydney morning herald",
            "theage", "afr.com", "australian financial review", "news.com.au",
            "theaustralian", "homeaffairs.gov.au", ".gov.au"],
    },
    {
        "name_kr": "캐나다",
        "lang": "en-CA", "gl": "CA", "ceid": "CA:en",
        "query": 'visa OR immigration OR "permanent residence" OR "work permit" OR residency OR "driver''s licence"',
        "trusted": [
            "cbc.ca", "cbc news", "ctvnews", "ctv news", "theglobeandmail",
            "globe and mail", "torontostar", "toronto star", "globalnews",
            "global news", "nationalpost", "national post", "cicnews", "cic news",
            "canada.ca", ".gc.ca",
        ],
    },
    {
        "name_kr": "말레이시아",
        "lang": "en-MY", "gl": "MY", "ceid": "MY:en",
        "query": 'visa OR immigration OR "permanent resident" OR "work permit" OR "driving licence" OR expatriate',
        # 기본 검색은 세계 뉴스(미국 비자 등)가 많이 섞여서, 말레이시아 유학생·출입국 소식을 따로 한 번 더 찾음(영어 + 말레이어)
        "extra_feeds": [
            {"query": '"student pass" OR EMGS OR "international students" OR "foreign students" OR "Immigration Department" '
                      'OR "Jabatan Imigresen" Malaysia', "lang": "en-MY", "gl": "MY", "ceid": "MY:en"},
            {"query": '"pelajar antarabangsa" OR "visa pelajar" OR "pas pelajar" OR EMGS OR "Jabatan Imigresen"',
             "lang": "ms", "gl": "MY", "ceid": "MY:ms"},
        ],
        "trusted": [
            "the star", "thestar.com.my", "new straits times", "nst.com.my",
            "bernama", "malay mail", "malaymail", "the edge", "theedgemalaysia",
            "free malaysia today", "freemalaysiatoday", ".gov.my",
            # 2026-10-14 추가: 출입국청·유학생 관리기관(EMGS), 말레이어 주요 언론(유학생 소식은 말레이어 보도가 많음)
            "imi.gov.my", "imigresen", "emgs", "education malaysia",
            "berita harian", "bharian", "astro awani", "astroawani", "sinar harian", "sinarharian", "utusan",
        ],
    },
    {
        "name_kr": "싱가포르",
        "lang": "en-SG", "gl": "SG", "ceid": "SG:en",
        "query": 'visa OR immigration OR "permanent resident" OR "employment pass" OR "work pass" OR "driving licence"',
        "trusted": [
            # ※ 'today'만 쓰면 'Homeland Security Today'(미국) 등에 부분일치 → 'todayonline'만.
            "straits times", "straitstimes", "channelnewsasia", "channel newsasia",
            "business times", "businesstimes", "todayonline", ".gov.sg",
        ],
    },
    {
        "name_kr": "인도네시아",
        "lang": "id-ID", "gl": "ID", "ceid": "ID:id",
        "query": 'visa OR imigrasi OR "izin tinggal" OR KITAS OR "warga negara asing" OR "penduduk tetap"',
        "trusted": [
            "kompas", "tempo", "antara", "antaranews", "jakarta post", "jakartapost",
            "jakarta globe", "jakartaglobe", "detik", "cnn indonesia", "cnnindonesia",
            "imigrasi", ".go.id",
        ],
    },
    {
        "name_kr": "영국",
        "lang": "en-GB", "gl": "GB", "ceid": "GB:en",
        "query": 'visa OR immigration OR "indefinite leave" OR "settled status" OR "work permit" OR residency',
        "trusted": [
            # ※ 'the times'는 'the times of india' 등과 부분일치 위험 → 도메인 'thetimes'로.
            "bbc", "the guardian", "theguardian", "reuters", "thetimes",
            "telegraph", "sky news", "skynews", "financial times", "ft.com",
            "independent.co.uk", "gov.uk",
        ],
    },
    {
        "name_kr": "독일",
        "lang": "de", "gl": "DE", "ceid": "DE:de",
        "query": "Visum OR Aufenthaltstitel OR Einwanderung OR Aufenthaltserlaubnis OR Einbürgerung OR Führerschein",
        "trusted": [
            # 공영방송 (가장 신뢰) — ARD/ZDF/DW + 지역 공영
            "tagesschau", "ard.de", "zdf", "deutsche welle", "dw.com",
            "ndr", "wdr", "rbb", "br.de", "mdr.de", "swr.de", "hr.de",
            "deutschlandfunk", "n-tv", "ntv.de",
            # 전국 대표 신문·시사지
            "der spiegel", "spiegel.de", "die zeit", "zeit.de",
            "süddeutsche", "sueddeutsche", "faz", "faz.net", "welt.de",
            "handelsblatt", "tagesspiegel", "frankfurter rundschau", "fr.de",
            "stern.de", "focus.de", "rnd.de",
            # 통신사·정부
            "dpa", ".bund.de",
        ],
    },
    {
        "name_kr": "프랑스",
        "lang": "fr", "gl": "FR", "ceid": "FR:fr",
        "query": 'visa OR immigration OR "titre de séjour" OR "carte de séjour" OR naturalisation OR "permis de conduire"',
        "trusted": [
            "le monde", "lemonde", "le figaro", "lefigaro", "afp", "france 24",
            "france24", "franceinfo", "france info", "libération", "liberation",
            "les échos", "lesechos", "ouest-france", "la croix", "rfi", ".gouv.fr",
        ],
    },
    {
        "name_kr": "인도",
        "lang": "en-IN", "gl": "IN", "ceid": "IN:en",
        "query": 'visa OR immigration OR "residence permit" OR OCI OR naturalisation OR "driving licence" OR foreigner',
        "trusted": [
            "thehindu", "the hindu", "ptinews", "press trust", "timesofindia",
            "times of india", "indianexpress", "indian express", "ndtv",
            "hindustantimes", "hindustan times", "livemint", "theprint", "the print",
            "reuters", ".gov.in", ".nic.in",
        ],
    },
    {
        "name_kr": "이탈리아",
        "lang": "it", "gl": "IT", "ceid": "IT:it",
        "query": 'visto OR immigrazione OR "permesso di soggiorno" OR cittadinanza OR "patente di guida"',
        "trusted": [
            "ansa.it", "rai.it", "rainews", "corriere.it", "corriere della sera",
            "repubblica.it", "la repubblica", "ilsole24ore", "lastampa.it",
            "la stampa", ".gov.it", ".interno.it",
        ],
    },
    {
        "name_kr": "스페인",
        "lang": "es", "gl": "ES", "ceid": "ES:es",
        "query": 'visado OR inmigración OR "permiso de residencia" OR extranjería OR nacionalidad OR "carné de conducir"',
        "trusted": [
            "elpais.com", "el país", "rtve.es", "efe.com", "agenciaefe",
            "lavanguardia", "elmundo.es", "abc.es", "eldiario.es", ".gob.es",
        ],
    },
    {
        "name_kr": "네덜란드",
        "lang": "nl", "gl": "NL", "ceid": "NL:nl",
        "query": "visum OR immigratie OR verblijfsvergunning OR inburgering OR naturalisatie OR rijbewijs",
        "trusted": [
            "nos.nl", "nrc.nl", "volkskrant", "anp.nl", "parool", "trouw.nl",
            "rtlnieuws", "rtl.nl", "nu.nl", "rijksoverheid.nl", ".overheid.nl",
        ],
    },
    {
        "name_kr": "스위스",
        "lang": "de-CH", "gl": "CH", "ceid": "CH:de",
        "query": "Visum OR Aufenthaltsbewilligung OR Einwanderung OR Einbürgerung OR Führerausweis",
        "trusted": [
            "srf.ch", "swissinfo", "nzz.ch", "tagesanzeiger", "letemps",
            "rts.ch", "24heures", ".admin.ch","aargauerzeitung", "aargauer zeitung", "luzernerzeitung", "luzerner zeitung", "tagblatt", "bernerzeitung", "berner zeitung", "bazonline", "basler zeitung", "derbund", "keystone-sda", "bluewin"],
    },
    {
        "name_kr": "브라질",
        "lang": "pt-BR", "gl": "BR", "ceid": "BR:pt-419",
        "query": 'visto OR imigração OR "autorização de residência" OR estrangeiro OR naturalização OR "carteira de motorista"',
        "trusted": [
            "g1.globo", "oglobo", "o globo", "folha", "estadao", "uol.com.br",
            "cnnbrasil", "agenciabrasil", "agência brasil", "veja", ".gov.br",
        ],
    },
    {
        "name_kr": "멕시코",
        "lang": "es-MX", "gl": "MX", "ceid": "MX:es-419",
        "query": 'visa OR migración OR "residencia temporal" OR "residencia permanente" OR extranjero OR "licencia de conducir"',
        "trusted": [
            "eluniversal.com.mx", "reforma", "milenio", "la jornada", "jornada.com",
            "excelsior", "elfinanciero", "animalpolitico", ".gob.mx",
        ],
    },
    {
        "name_kr": "뉴질랜드",
        "lang": "en-NZ", "gl": "NZ", "ceid": "NZ:en",
        "query": 'visa OR immigration OR residency OR "work visa" OR "resident visa" OR "driver licence"',
        "trusted": [
            "nzherald", "nz herald", "rnz.co.nz", "stuff.co.nz", "newshub",
            "newsroom", "odt.co.nz", "otago daily", ".govt.nz",
        ],
    },
    {
        "name_kr": "러시아",
        "lang": "ru", "gl": "RU", "ceid": "RU:ru",
        "query": 'виза OR ВНЖ OR миграция OR "вид на жительство" OR иностранец OR "водительские права"',
        "trusted": [
            # 국영 TASS는 공식 발표 확인용으로만, 독립매체(Meduza·The Insider·Kommersant) 위주.
            "interfax", "интерфакс", "tass.com", "tass.ru", "тасс", "meduza",
            "theins.ru", "the insider", "kommersant", "коммерсант", "rbc.ru",
            "garant.ru",
        ],
    },
    {
        "name_kr": "스웨덴",
        "lang": "sv", "gl": "SE", "ceid": "SE:sv",
        "query": "visum OR uppehållstillstånd OR invandring OR medborgarskap OR körkort",
        "trusted": [
            "svt.se", "svt nyheter", "sverigesradio", "sveriges radio", "dn.se",
            "dagens nyheter", "svd.se", "svenska dagbladet", "gp.se",
            "riksdagen.se", "regeringen.se",
        ],
    },
    {
        "name_kr": "튀르키예",
        "lang": "tr", "gl": "TR", "ceid": "TR:tr",
        "query": 'vize OR "oturma izni" OR göç OR vatandaşlık OR ehliyet',
        "trusted": [
            "aa.com.tr", "anadolu", "trt.net.tr", "trthaber", "hurriyet",
            "hürriyet", "milliyet", "cumhuriyet", "ntv.com.tr", "sozcu", ".gov.tr",
        ],
    },
    # ──────────────────────────────────────────────────────────────
    # ※ 아래 5개국은 "신뢰 소스 부실"로 제거했습니다 (2026-08, 실측 결과).
    #    나중에 홈페이지 '지원하지 않는 나라' 안내에 이 사유를 적을 것:
    #    · 중국   : 구글이 중국 내에서 차단, 신뢰 소스가 국영매체뿐, 남의 나라 기사 많음
    #    · 홍콩   : 구글뉴스 피드가 미국 이민뉴스로 도배, 현지 대표지(SCMP·RTHK) 안 뜸(신뢰 통과 0건)
    #    · UAE    : 위와 동일 - 미국 뉴스 도배, 현지 대표지(The National 등) 안 뜸(0건)
    #    · 캄보디아 : 위와 동일 - 미국 뉴스 도배, 현지 대표지(Phnom Penh Post 등) 안 뜸(0건)
    #    · 미얀마  : 위와 동일 + 언론 탄압으로 독립매체가 피드에 거의 안 잡힘(0건)
    #    → 이 나라들은 구글뉴스 방식으로는 한계. 나중에 각국 정부/이민국 직접 스크래핑 필요.
    # ──────────────────────────────────────────────────────────────
    {
        "name_kr": "포르투갈",
        "lang": "pt-PT", "gl": "PT", "ceid": "PT:pt-150",
        "query": 'visto OR imigração OR "autorização de residência" OR estrangeiro OR nacionalidade OR "carta de condução"',
        "trusted": [
            "publico.pt", "público", "observador", "rtp.pt", "dn.pt",
            "diário de notícias", "expresso", "jn.pt", "jornal de notícias",
            "eco.pt", "sic notícias", "lusa.pt", ".gov.pt",
        ],
    },
    {
        "name_kr": "폴란드",
        "lang": "pl", "gl": "PL", "ceid": "PL:pl",
        "query": 'wiza OR imigracja OR "zezwolenie na pobyt" OR cudzoziemiec OR obywatelstwo OR "prawo jazdy"',
        "trusted": [
            "tvn24", "wyborcza.pl", "gazeta wyborcza", "rp.pl", "rzeczpospolita",
            "pap.pl", "polska agencja prasowa", "rmf24", "onet.pl", "polsatnews",
            "gazetaprawna", "notesfrompoland", "notes from poland", ".gov.pl",
        ],
    },
    {
        "name_kr": "오스트리아",
        "lang": "de-AT", "gl": "AT", "ceid": "AT:de",
        "query": "Visum OR Aufenthaltstitel OR Einwanderung OR Einbürgerung OR Aufenthaltsbewilligung OR Führerschein",
        "trusted": [
            "orf.at", "derstandard", "diepresse", "die presse", "kurier.at",
            "apa.at", "kleinezeitung", "kleine zeitung", "salzburg.com", ".gv.at","wienerzeitung", "wiener zeitung", "tt.com", "tiroler tageszeitung", "nachrichten.at", "oön", "sn.at", "salzburger nachrichten", "vienna.at", "bmi.gv.at"],
    },
    {
        "name_kr": "벨기에",
        "lang": "nl-BE", "gl": "BE", "ceid": "BE:nl",
        "query": "visum OR immigratie OR verblijfsvergunning OR nationaliteit OR inburgering OR rijbewijs",
        "trusted": [
            "vrt.be", "vrt nws", "standaard.be", "de standaard", "demorgen",
            "de morgen", "hln.be", "nieuwsblad", "rtbf", "lesoir", "le soir",
            "belga.be", ".belgium.be", ".fgov.be",
        ],
    },
    {
        "name_kr": "아르헨티나",
        "lang": "es-419", "gl": "AR", "ceid": "AR:es-419",
        "query": 'visa OR migración OR "residencia temporal" OR "residencia permanente" OR extranjero OR ciudadanía OR "licencia de conducir"',
        "trusted": [
            "clarin.com", "clarín", "lanacion.com.ar", "la nación", "infobae",
            "pagina12", "página/12", "tn.com.ar", "ambito.com", "perfil.com",
            ".gob.ar",
        ],
    },
]

# 정책 '변화 신호'로 볼 단어 (번역된 한국어 제목에서 하나라도 있어야 통과)
#  ※ '판결·소송·단속·추방·구금·유예'는 개별 사건/형사 이슈라 신호어에서 뺌(노이즈).
SIGNAL_WORDS = [
    # 제도 변경(완화/강화 방향)
    "완화", "강화", "엄격", "개정", "신설", "폐지", "의무", "도입", "추진",
    "검토", "방침", "조정", "확대", "축소", "연장", "단축", "변경", "시행",
    "허용", "제한", "요건", "규정", "제도",
    # 법·행정 '제도' 절차 (규칙 자체가 바뀌는 것)
    "법안", "통과", "발효", "서명", "행정명령", "명령",
    "면제", "승인", "거부", "인상", "인하", "상향",
    # 유학생 소식에 자주 쓰이는 말(2026-10-13 추가)
    "중단", "정지", "상한", "의무화", "심사",
]

# ── 노이즈 제외어 ────────────────────────────────────────────────
# 개별 인물·사건·정치 이슈(예: 추방 집행, 구금, 성명, 판결)는 신호어가 있어도 제외.
#  → 우리 타겟은 '규정·제도 변경'이지 '개별 정치 사건'이 아니므로.
EXCLUDE_WORDS = [
    # 개별 인물·사건
    "성명", "구금", "체포", "기소", "석방", "판사", "급습", "작전",
    "용의자", "혐의", "영장", "사망", "부상", "실종", "선원", "아버지", "부인",
    "엄마", "성접대", "도주", "도망", "밀입국", "사기",
    # 국내 정치(우리 타겟인 '이민·비자 규정'과 무관)
    "주지사", "유권자", "시민권 증명", "참전", "군인", "추방 위기", "낙태", "총기",
    # 미국 정치 인물·프레임(정책 규정 변경이 아니라 '정치 뉴스'로 새어 들어옴)
    #  ※ 다른 나라 피드에 미국 정치 기사가 섞여 들어오는 것도 여기서 걸러짐
    "트럼프", "바이든", "행정부", "백악관", "정권", "이민 단속", "단속", "대통령",
    # 정쟁·진영 다툼(규정 변경이 아니라 '정치 싸움' 기사)
    "MAGA", "공화당", "민주당", "지지자", "지지층", "진영", "정쟁",
    "공방", "설전", "소란", "규탄", "성토", "선거운동", "유세",
    # 오피니언·논평(사실 규정 변경이 아니라 '의견' 기사)
    "사설", "칼럼", "오피니언", "기고", "논평",
    # 국방·군사(사용자의 비자·체류·생활과 무관)
    "방위", "자위대", "국방", "군사", "미사일", "전투기", "국방부", "방위성",
    # 의회·정당 정치(규정 변경이 아니라 '정치' 기사)
    #  ※ '의원'(병원 오탐)·'정당'('정당한 사유' 오탐)은 일부러 뺌
    "국회의원", "상원", "하원", "노동당", "보수당", "여당", "야당", "ICE",
    # 유럽 극우 정치인·정당(규정이 아니라 '정치 행사·계획' 기사로 샘)
    "Bardella", "바르델라", "Farage", "파라지", "AfD", "극우",
    # 사용자의 비자·체류와 무관한 사건(어업 분쟁·교통사고 등)
    "어업", "어선", "충돌사고", "추돌",
    # 개인 과속·교통 사건(면허 '규정'이 아니라 개별 사건 → '운전면허' 단어로 샘)
    "제한 속도", "km/h", "㎞", "과속", "음주운전",
    # 스포츠·연예('거주규칙'·컵대회 등 '거주' 단어로 샘)
    "고티아", "UAAP", "플레이어",
    # 주식·금융(비자·체류와 무관)
    "증권거래소", "주가", "폭락",
    # 지방 이민청 내부 잡무(사용자에게 가치 없음)
    "취임", "폴리테크닉", "회계연도", "예산 조정", "조정 회의", "시너지",
    # 자동차·에너지·IT(비자와 무관)
    "Nissan", "열병합", "마이그레이션",
    # 지역 명소·행사(예: '비자 거부로 동물원 축제 취소' 같은 흥밋거리 — 규정 아님)
    "동물원", "놀이공원", "수족관",
    # ※ '침공'은 일부러 안 막음 — 실제 전쟁·안보 위기는 사용자 안전(대피·체류)과 직결되어
    #    통과시켜야 함. 순수 군사 잡음은 위의 '국방·군사·미사일' 등이 이미 거름.
]

# 영어 원문에서 바로 거를 표시(오피니언·정쟁·비뉴스). 번역 전에 원문으로 판단해 더 정확.
EN_NOISE = [
    "opinion", "op-ed", "op ed", "editorial", "comment:", "commentary",
    "analysis:", "explainer", "explained:", "podcast", "watch:", "live:",
    "video:", "photos:", "in pictures", "maga", "gop ", " gop",
    # 국방·군사·의회정치(비자·체류·생활과 무관)
    "labour party", "labor party", "senate", "congress", "lawmaker",
    "military", "navy", "missile", "defense ministry", "self-defense",
    # 유럽 극우 정치인·정당 / 기업 감원(카드사 'Visa Inc.' 오탐 등)
    "far-right", "far right", "bardella", "farage", "afd",
    "layoff", "lays off", "job cuts", "to cut jobs",
]

# 기사 제목이 '다른 나라' 이름으로 시작하면 그 나라 기사(태그 오류)로 판단해 제외.
#  예: 일본 피드에 '영국 노동당…'이 [일본]으로 잘못 태그되어 들어오는 것 방지.
COUNTRY_NAMES = [
    "일본", "미국", "중국", "영국", "프랑스", "독일", "러시아", "인도", "캐나다",
    "호주", "이탈리아", "스페인", "네덜란드", "브라질", "멕시코", "베트남", "대만",
    "태국", "필리핀", "말레이시아", "싱가포르", "인도네시아", "스위스", "뉴질랜드",
    "스웨덴", "튀르키예", "포르투갈", "폴란드", "오스트리아", "벨기에", "아르헨티나",
    "우크라이나", "이스라엘", "이란", "홍콩", "대만", "북한",
]


def is_foreign_topic(ko_title, feed_country):
    """제목이 '피드 나라와 다른 나라' 기사면 True (태그 오류·외국 기사)."""
    t = (ko_title or "").strip()
    for c in COUNTRY_NAMES:
        if c != feed_country and t.startswith(c):
            return True
    # 미국 연방 법원/기관 기사가 다른 나라 피드에 섞임(예: 일본 피드의 '미 연방지법')
    #  ※ '미지재'=미국 지방재판소(일본식 한자 읽기), '미 지재'도 같은 뜻.
    if feed_country != "미국" and any(k in t for k in
            ("미 연방", "연방지법", "연방법원", "연방대법원", "연방 대법원",
             "미 대법원", "미지재", "미 지재", "연방지방법원")):
        return True
    return False

# 미국 주(州) 이름 — '주 + 국내정치' 조합일 때만 제외(연방 이민 뉴스는 살림)
US_STATES = [
    "오하이오", "텍사스", "캘리포니아", "플로리다", "메릴랜드", "버지니아", "조지아",
    "애리조나", "일리노이", "펜실베이니아", "미시간", "뉴저지", "노스캐롤라이나",
    "미네소타", "콜로라도", "네바다", "위스콘신", "미주리", "테네시", "오리건",
]
# 국내 정치 키워드(주 이름과 함께 나오면 국내 정치로 보고 제외)
DOMESTIC_POLITICS = ["유권자", "투표", "선거", "주 의회", "주법", "주민투표", "시민권 증명"]


def is_noise(ko_title, en_title=""):
    """개별 사건·국내 정치·정쟁·오피니언이면 True (전국 단위 이민·비자 규정이 아님)."""
    if any(w in ko_title for w in EXCLUDE_WORDS):
        return True
    # 영어 원문에 오피니언·정쟁 표시가 있으면 제외(번역으로 어색해지기 전에 원문으로 차단)
    en = (en_title or "").lower()
    if any(w in en for w in EN_NOISE):
        return True
    # 미국 주 이름 + 국내 정치 조합 → 제외
    if any(s in ko_title for s in US_STATES) and any(p in ko_title for p in DOMESTIC_POLITICS):
        return True
    # 기업 감원 기사(카드사 'Visa Inc.'가 비자로 오탐) → '직원+해고'가 함께일 때만 제외.
    #  ※ '외국인 부당 해고' 같은 개인 노동 이슈는 '직원'이 없어 살아남음.
    if "직원" in ko_title and "해고" in ko_title:
        return True
    # 카드사 'Visa Inc.' — 이민 '비자'는 늘 '비자'로 번역됨 → 라틴 'Visa'는 회사(대출·결제·AI 등).
    if re.search(r"\bVisa\b", ko_title):
        return True
    return False

# 한 나라에서 한 번에 처리할 최대 기사 수 (번역 호출 과다 방지)
MAX_PER_COUNTRY = 12

# 이보다 오래된 기사는 무시(며칠 지난 소식이 "오늘부터 시행"처럼 다시 나오는 것 방지)
MAX_AGE_DAYS = 3


def is_recent(pub):
    """기사 발행이 최근 MAX_AGE_DAYS일 이내면 True. (날짜를 못 읽으면 통과)"""
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() <= MAX_AGE_DAYS * 86400
    except Exception:
        return True

# ── 엄격 모드 ────────────────────────────────────────────────────
# True  : '정책 신호어'가 있는 기사만 통과 → 잡뉴스(스포츠/사건사고 등) 차단.
#         "사람 검수 없이 자동 발송"에 가장 안전. (강력 추천)
# False : 신뢰 소스의 관련 기사를 모두 보여줌(신호 기사를 위로 정렬).
STRICT_MODE = True

# 번역해서 신호어를 검사할 후보 최대 수(나라별). 번역 호출량을 제한.
MAX_CANDIDATES = 20

SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_news.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# ── 뉴스 가져오기 ────────────────────────────────────────────────
# 유학생 관련 검색어(나라 언어별) — 기존 검색어(비자·체류…)에 덧붙임.
#  비자라는 말이 없는 유학생 소식(아르바이트 시간·건강보험·학비 규정)도 후보에 들어오게.
STUDENT_TERMS = {
    "ja": "留学生", "en": '"international students" OR "student visa"', "zh": "留學生 OR 外籍生 OR 僑外生",
    "vi": '"du học sinh" OR "sinh viên quốc tế"', "th": "นักศึกษาต่างชาติ", "id": '"mahasiswa asing"',
    "de": '"internationale Studierende" OR Studentenvisum', "fr": '"étudiants étrangers"', "it": '"studenti stranieri"',
    "es": '"estudiantes extranjeros" OR "visa de estudiante"', "nl": '"internationale studenten"',
    "pt": '"estudantes estrangeiros"', "ru": '"иностранные студенты"', "sv": '"internationella studenter"',
    "tr": '"yabancı öğrenciler"', "pl": '"studenci zagraniczni"',
}


def search_query(country):
    """구글뉴스 검색어: 나라별 기본 검색어 + 유학생 검색어 + 최근 7일."""
    extra = STUDENT_TERMS.get((country.get("lang") or "en").split("-")[0].lower(), "")
    return country["query"] + (" OR " + extra if extra else "") + " when:7d"


def fetch_news(country):
    """구글뉴스 RSS에서 그 나라 뉴스를 가져와 항목 리스트로 돌려줍니다.
    나라 설정에 extra_feeds(추가 검색)가 있으면 그것도 가져와 합침(같은 기사는 1번만)."""
    items = _fetch_feed(search_query(country), country["lang"], country["gl"], country["ceid"])
    have = {it["guid"] for it in items}
    for f in country.get("extra_feeds") or []:
        try:
            more = _fetch_feed(f["query"] + " when:7d", f["lang"], f["gl"], f["ceid"])
        except Exception:
            continue                                  # 추가 검색이 실패해도 기본 결과는 씀
        for it in more:
            if it["guid"] not in have:
                have.add(it["guid"])
                items.append(it)
    return items


def _fetch_feed(query, lang, gl, ceid):
    url = (
        "https://news.google.com/rss/search?q="
        + quote(query)                     # 최근 7일 기사만(없으면 베트남처럼 옛 기사만 돌아오는 나라가 있었음)
        + f"&hl={lang}&gl={gl}&ceid={ceid}"
    )
    req = Request(url, headers=UA)
    with urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", errors="replace")

    items = []
    for block in re.findall(r"<item>(.*?)</item>", raw, re.DOTALL):
        title = _clean(_tag(block, "title"))
        link = _tag(block, "link")
        pub = _tag(block, "pubDate")
        guid = _tag(block, "guid") or link
        # <source url="도메인">언론사이름</source>
        src_m = re.search(r'<source[^>]*url="([^"]*)"[^>]*>(.*?)</source>', block, re.DOTALL)
        src_url = src_m.group(1) if src_m else ""
        src_name = _clean(src_m.group(2)) if src_m else ""
        items.append({
            "title": title, "link": link, "pub": pub, "guid": guid,
            "src_url": src_url, "src_name": src_name,
        })
    return items


def _tag(block, name):
    m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", block, re.DOTALL)
    if not m:
        return ""
    v = m.group(1)
    v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", v, flags=re.DOTALL)
    return v.strip()


def _clean(text):
    """HTML 태그·엔티티 정리."""
    text = re.sub(r"<[^>]+>", "", text)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", text).strip()


# ── 신뢰 소스 필터 ───────────────────────────────────────────────
# 교육 전문지 — '정책 감시(유학생 주제 판정)'에서만 신뢰 출처로 씀. 대형사고·산사태 등 일반 뉴스에는 쓰지 않음.
EDU_SOURCES = ["timeshighereducation", "times higher education", "thepienews", "the pie news", "icef"]
EDU_COUNTRIES = {"호주", "영국", "미국", "캐나다"}


def policy_trusted(country):
    """정책 감시용 신뢰 목록 = 그 나라 일반 신뢰 언론 (+ 영어권 4개국은 교육 전문지)."""
    return country["trusted"] + (EDU_SOURCES if country["name_kr"] in EDU_COUNTRIES else [])


# 지난 뉴스를 다시 쓰는 학습·해설 코너(2026-10-17) — 신뢰 언론사여도 정책 소식으로 쓰지 않음.
#  예: NHK "ニュースで学ぶ「現代英語」" 가 9/14 미국 법원 판결을 10/17 에 다시 실어 '새 소식'처럼 통과했음.
REWRITE_CORNERS = [
    r"ニュースで学ぶ", r"現代英語", r"Learning English", r"News in Levels", r"Breaking News English",
    r"English in a Minute", r"VOA Learning", r"BBC Learning",
]


def is_rewrite_corner(item):
    """제목(언론사 이름 포함)에 학습·해설 코너 이름이 있으면 True."""
    text = f"{item.get('title', '')} {item.get('src_name', '')}"
    return any(re.search(p, text, re.I) for p in REWRITE_CORNERS)


def is_trusted(item, trusted_list):
    """기사의 출처가 신뢰 목록에 있으면 True."""
    haystack = (item["src_url"] + " " + item["src_name"]).lower()
    return any(tok.lower() in haystack for tok in trusted_list)


# ── 번역 (무료 구글, 키 불필요) ──────────────────────────────────
def translate_to_ko(text, src_lang):
    """제목을 한국어로 번역. 실패하면 원문을 그대로 돌려줍니다. (간격·저장·차단 감지는 번역도우미가 함)"""
    ko = tr.translate(text, src_lang)
    return text if ko is None else ko


# ── 번역이 안 될 때(구글 차단 등) ────────────────────────────────
#  · 번역 못 한 기사는 '이미 본 것'에 넣지 않음 → 다음 실행 때 다시 검사
#  · 대신 나라별 최신 몇 건은 원문 제목 그대로 AI판단에 물어봄(산사태 감시와 같은 방식)
#  · 같은 기사가 24시간 넘게 계속 실패하면 AI판단기록에 '검사 실패'로 남기고 그만 시도
FALLBACK_MAX = 5
# (2026-10-13) 번역 의존 줄이기: AI판단(Gemini)이 켜져 있으면 구글 번역을 거치지 않고 원문 제목을 바로 AI판단에 물음.
#   통과한 소식의 한국어 한 줄(summary)도 같은 호출에서 Gemini가 씀. 구글 무료 번역은 아래 두 경우에만 씀:
#   ① AI판단이 꺼져 있거나 키가 없을 때(예전 방식: 번역 → 키워드)  ② 통과했는데 한국어 요약이 비어 있을 때 제목 번역
AI_DIRECT_MAX = 8      # 나라별로 한 번에 AI에 물어볼 최대 기사 수(나머지는 다음 실행 때)


def ai_direct_on():
    try:
        import AI판단 as ai
        cfg = ai.load_config()
        return bool(cfg.get("enabled", True)) and bool(cfg.get("api_key"))
    except Exception:
        return False


def korean_title(it, lang):
    """AI 요약이 없을 때만: 원문 제목을 구글 번역으로(실패하면 원문 그대로)."""
    if it.get("untranslated"):
        ko = tr.translate(it["title"], lang)
        if ko:
            it["ko"] = ko
            it["untranslated"] = False
    return it.get("ko") or it["title"]
RETRY_HOURS = 24
RETRY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "재검사_대기.json")


def load_retry():
    try:
        with open(RETRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_retry(retry):
    cut = time.time() - 7 * 86400
    with open(RETRY_FILE, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in retry.items() if v >= cut}, f, ensure_ascii=False)


def split_untranslated(kind, country_name, items, lang, seen, retry):
    """번역 못 한 기사들 → (AI에 원문으로 물어볼 것, 다음에 다시 볼 것 수).
       24시간 넘게 실패한 기사는 '검사 실패'로 기록하고 본 것으로 처리."""
    import AI판단 as ai
    now = time.time()
    to_ai, waiting = [], 0
    for it in items:
        first = retry.setdefault(it["guid"], now)
        if now - first > RETRY_HOURS * 3600:
            it["ko"], it["country"] = it["title"], country_name
            ai._log(kind, it, "검사 실패", f"번역이 {RETRY_HOURS}시간 넘게 안 돼 검사하지 못함", "")
            seen.add(it["guid"])
            retry.pop(it["guid"], None)
        elif len(to_ai) < FALLBACK_MAX:
            it["ko"], it["country"], it["signals"] = it["title"], country_name, []
            it["untranslated"] = True
            to_ai.append(it)
            seen.add(it["guid"])                  # AI가 실패하면 AI판단의 '보류'가 다시 물어봄(24시간)
            retry.pop(it["guid"], None)
        else:
            waiting += 1                          # seen 에 넣지 않음 → 다음 실행 때 다시
    return to_ai, waiting


def find_signals(ko_title):
    """번역된 제목에서 정책 신호어를 찾아 리스트로 돌려줍니다."""
    return [w for w in SIGNAL_WORDS if w in ko_title]


# ── 발송용 형식 만들기 ───────────────────────────────────────────

# 번역이 어색하게 나오는 것들 자연스럽게 보정 (필요하면 계속 추가)
TRANSLATION_FIXES = {
    "정치원": "행정원",        # 대만 行政院 오역
    "입관청": "출입국관리청",   # 일본 入管庁
    "입관": "출입국관리",
    "재류": "체류",
    "영주권 자격": "영주 자격",
    # 일본 지명 오역(구글이 한자를 한국음으로 읽음) — 확인된 것만, 발견되는 대로 추가
    "대전구": "오타구",        # 東京 大田区 (Ōta-ku) — 이번 사례
}

# '생활·제도(🚗)'로 볼 단어 (운전면허 등) — 나머지는 '비자·정책(📋)'
LICENSE_WORDS = ["운전면허", "면허", "license", "driver", "駕照", "bằng lái", "ใบขับขี่"]

# 🔴 최고 긴급으로 볼 강한 표현 — 정책은 원래 '브리핑'이라, 정말 큰 변화만 즉시.
#  ※ '금지·폐지·즉시'만으로는 너무 흔해 오탐(예: '체류기간 제한 금지') → '전면 ~'만 긴급.
#     '전면'은 '운전면허' 오탐 방지 위해 반드시 뒤 단어와 붙은 구(전면 폐지/전면 금지)로.
STRONG_WORDS = ["전면 폐지", "전면 금지", "전면적", "대폭 완화", "대폭 강화"]


def fix_translation(text):
    for wrong, right in TRANSLATION_FIXES.items():
        text = text.replace(wrong, right)
    return text


def clean_title(ko_title):
    """번역 제목을 한 줄로 깔끔하게: 사진캡션 접두어·언론사 꼬리표·대괄호 제거."""
    t = fix_translation(ko_title)
    # 맨 앞 '이미지·사진 :', '사진 :', '이미지 :' 같은 사진캡션 접두어 제거
    t = re.sub(r"^\s*(?:이미지|사진|영상|포토|그래픽|자료사진)[\s·・/,]*(?:(?:이미지|사진|영상|포토|그래픽)[\s·・/,]*)*[:：]\s*", "", t)
    t = re.sub(r"[\[【][^\]】]*[\]】]", "", t)          # [ ... ] 【 ... 】 제거
    # 끝에 붙는 '：언론사'(일본식 전각 콜론) 꼬리표 제거
    t = re.sub(r"\s*：\s*[^：]{1,20}$", "", t).strip()
    # 끝에 붙는 ' - 언론사' / ' | 섹션' 같은 짧은 꼬리표를 최대 2번까지 제거
    for _ in range(2):
        t = re.sub(r"\s*[\-–|·]\s*[^\-–|·]{1,25}$", "", t).strip()
    return re.sub(r"\s+", " ", t).strip()


def short_title(clean, limit=34):
    """제목을 헤드라인용으로 짧게. 뜻이 끊기지 않게 쉼표·구절 단위로 자르고 …"""
    if len(clean) <= limit:
        return clean
    cut = clean[:limit]
    # 쉼표·구분점에서 자르면 한 구절이 온전해짐(단어·뜻 중간 끊김 방지)
    best = -1
    for sep in ("，", "、", ",", "·", " "):
        i = cut.rfind(sep)
        if i > best:
            best = i
    if best >= limit * 0.5:        # 너무 앞에서 자르면 정보 손실 → 무시
        cut = cut[:best]
    return cut.rstrip(" ,，、·-–|") + "…"


def short_date(pub):
    """RSS 시각(GMT)을 한국시각 '8월 7일'로."""
    try:
        dt = parsedate_to_datetime(pub).astimezone(timezone(timedelta(hours=9)))
        return f"{dt.month}월 {dt.day}일"
    except Exception:
        return ""


def make_news_block(it, country_name):
    """뉴스 1건을 발송용 메시지 블록으로. (importance, 블록문자열) 반환."""
    # AI가 다듬은 자연스러운 한 줄 요약이 있으면 그걸 제목으로(번역투·잘림 방지)
    summary = (it.get("ai") or {}).get("summary") or ""
    clean = summary or clean_title(korean_title(it, it.get("lang", "auto")))
    head = clean if summary else short_title(clean)
    # 제목이 잘렸을 때만 전체 내용을 한 줄 더 보여줌(중복 방지)
    content = clean if len(clean) > len(head) else ""

    # 카테고리: 운전면허 등은 🚗 생활·제도, 나머지는 📋 비자·정책
    hay = (clean + " " + it["title"]).lower()
    is_life = any(w.lower() in hay for w in LICENSE_WORDS)
    cat_emoji = "🚗" if is_life else "📋"
    care = "현지 거주 중이면 확인해보세요." if is_life else "관련 있다면 미리 확인해보세요."

    src = it["src_name"] or it["src_url"]
    try:
        when = local_kst_str(country_name, parsedate_to_datetime(it["pub"]))
    except Exception:
        when = short_date(it["pub"])
    source_line = f"출처: {src} · {when}".rstrip(" ·")

    # 중요도: 강한 표현이면 🔴, 아니면 🟡
    strong = any(w in clean for w in STRONG_WORDS)

    lines = []
    if strong:  # 🔴 최고 긴급
        lines.append(f"🚨🚨 {cat_emoji} [{country_name}] {head}")
        lines.append("━━━━━━━━━━━━")
    else:       # 🟡 일반
        lines.append(f"{cat_emoji} [{country_name}] {head}")
    if content:
        lines.append(content)
    lines.append(care)
    lines.append(source_line)
    lines.append(f"자세히 ▶ {it['link']}")

    importance = 2 if strong else 1
    return importance, "\n".join(lines)


# ── 중복 방지 파일 ───────────────────────────────────────────────
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
        json.dump({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "seen": ids},
                  f, ensure_ascii=False, indent=2)


# ── 메인 ─────────────────────────────────────────────────────────
def main():
    seen = load_seen()
    scored = []  # (importance, 블록) 리스트 — 나중에 🔴가 위로 오게 정렬
    first = []   # 키워드 1차 통과(AI에게 물어볼 후보)
    retry = load_retry()   # 번역 실패로 다시 볼 기사 {guid: 처음 실패한 시각}
    n_wait = 0
    direct = ai_direct_on()

    for country in COUNTRIES:
        name = country["name_kr"]
        try:
            items = fetch_news(country)
        except Exception:
            # 발송용: 한 나라 실패해도 조용히 넘어가고 다음 주기에 재시도
            continue

        # 1) 신뢰 소스만 남기기  2) 중복 제거
        trusted_new = [
            it for it in items
            if is_trusted(it, policy_trusted(country)) and it["guid"] not in seen
            and is_recent(it["pub"]) and not is_rewrite_corner(it)
        ]
        if not trusted_new:
            for it in items:
                if is_trusted(it, policy_trusted(country)):
                    seen.add(it["guid"])
            continue

        if direct:                                # 번역 없이 원문 제목을 바로 AI판단에
            for it in trusted_new[:AI_DIRECT_MAX]:
                it["ko"], it["signals"], it["country"], it["untranslated"] = it["title"], [], name, True
                it["lang"] = country["lang"]
                seen.add(it["guid"])              # AI가 실패하면 AI판단의 '보류'가 24시간 다시 물어봄
                retry.pop(it["guid"], None)
                first.append(it)
            continue                              # 나머지는 '본 것'에 넣지 않음 → 다음 실행 때

        # 3) 후보를 번역해서 정책 신호어를 검사 (후보 수는 제한)
        candidates, failed = [], []
        for it in trusted_new[:MAX_CANDIDATES]:
            ko = tr.translate(it["title"], country["lang"])
            if ko is None:
                failed.append(it)                 # 번역 실패 → '본 것'에 넣지 않음
                continue
            it["ko"] = ko
            it["signals"] = find_signals(ko)
            seen.add(it["guid"])
            retry.pop(it["guid"], None)
            candidates.append(it)
        for it in trusted_new[MAX_CANDIDATES:]:
            seen.add(it["guid"])
        if failed:                                # 번역이 막혀 있으면 원문 제목을 바로 AI판단에
            to_ai, waiting = split_untranslated("정책", name, failed, country["lang"], seen, retry)
            first += to_ai
            n_wait += waiting

        # 4) 규정 신호어 있고(엄격) + 개별 사건/정치/국방 제외 + 태그오류(외국 기사) 제외
        if STRICT_MODE:
            picked = [it for it in candidates
                      if it["signals"] and not is_noise(it["ko"], it["title"])
                      and not is_foreign_topic(it["ko"], name)]
        else:
            picked = [it for it in candidates
                      if not is_noise(it["ko"], it["title"])
                      and not is_foreign_topic(it["ko"], name)]
        picked.sort(key=lambda it: len(it["signals"]), reverse=True)
        picked = picked[:MAX_PER_COUNTRY]

        for it in picked:
            it["country"] = name
            first.append(it)

    # 5) AI 최종 판단(제미나이) — 키워드 통과 기사만 한 번에 묶어서. 실패하면 안 보내고 보류.
    import AI판단 as ai
    passed, _ = ai.judge("정책", first)
    for it in passed:
        scored.append(make_news_block(it, it["country"]))

    # 발송용: 🔴(긴급) 먼저, 그다음 🟡. 블록 사이는 빈 줄.
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        print("\n\n".join(block for _, block in scored))
    else:
        print("🟢 정책뉴스: 새로 알릴 소식 없음")
    if tr.is_blocked():
        print(f"\n🟢 번역 차단 중 — 원문 제목으로 AI판단에 물음. 다음 실행 때 다시 볼 기사 {n_wait}건")

    save_seen(seen)
    save_retry(retry)
    tr.finish()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
