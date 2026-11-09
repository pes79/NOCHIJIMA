# -*- coding: utf-8 -*-
"""
행정구역_만들기.py — 30개국 '1단계 행정구역(주·도·현)' 목록을 공개 데이터로 만들어
  ① nochijima/행정구역.json  (지역필터용: 이름·코드·지역을 덮는 좌표들)
  ② htmll/signup.html        (가입폼 목록: 한국어·영어 이름) 에 넣습니다.

  py 행정구역_만들기.py

■ 데이터 출처 (기억으로 만들지 않음)
  - GeoNames (download.geonames.org, CC-BY 4.0)
      admin1CodesASCII.txt : 나라별 1단계 행정구역 이름·코드
      admin2Codes.txt      : 대만만 사용(GeoNames의 대만 1단계가 옛 4구역이라 현행 22개 현·시로)
      cities500.txt        : 인구 500명 이상 모든 마을·도시 좌표(+어느 행정구역 소속인지)
  - Wikidata (query.wikidata.org, CC0) : GeoNames ID로 연결된 한국어 이름·별칭, 대표 좌표, ISO 코드

■ 검증
  - Wikidata 대표 좌표가 그 지역 마을들(GeoNames)과 멀리 떨어져 있으면 목록에 표시(이상 의심).
  - 마을 좌표가 하나도 없는 지역은 Wikidata 좌표 1점으로 대체하고 표시.
"""
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import zipfile
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(BASE, "행정구역.json")
SIGNUP = next((p for p in (os.path.join(os.path.dirname(BASE), d, "signup.html")
                           for d in ("놓치지마 html", "htmll")) if os.path.exists(p)),
              os.path.join(os.path.dirname(BASE), "놓치지마 html", "signup.html"))
WORK = os.path.join(tempfile.gettempdir(), "nochijima_geonames")   # 내려받은 원본(임시 폴더, 지워져도 다시 받음)
UA = {"User-Agent": "nochijima-region-builder/1.0 ([연락처])"}
GN = "https://download.geonames.org/export/dump/"

from 지역필터 import COUNTRY_CODE, haversine_km

USE_ADMIN2 = {"TW"}          # GeoNames 대만 1단계(푸젠·타이완성 등 옛 4구역) → 현행 22개 현·시
GRID = 0.12                  # 지역을 덮는 좌표를 약 13km 격자당 1점으로 줄임(파일 크기)


def fetch(name):
    os.makedirs(os.path.join(WORK, os.path.dirname(name)), exist_ok=True)
    path = os.path.join(WORK, name)
    if not os.path.exists(path):
        print("  내려받는 중:", name)
        with urlopen(Request(GN + name, headers=UA), timeout=300) as r, open(path, "wb") as f:
            f.write(r.read())
    return path


def read_admins():
    """{cc: [(code, en, ascii, geonameid)]} — code 예: 'JP.12', 'TW.04.TPE'."""
    want = set(COUNTRY_CODE.values())
    out = {cc: [] for cc in want}
    with open(fetch("admin1CodesASCII.txt"), encoding="utf-8") as f:
        for line in f:
            code, name, ascii_, gid = line.rstrip("\n").split("\t")
            cc = code.split(".")[0]
            if cc in want and cc not in USE_ADMIN2:
                out[cc].append((code, name, ascii_, gid))
    with open(fetch("admin2Codes.txt"), encoding="utf-8") as f:
        for line in f:
            code, name, ascii_, gid = line.rstrip("\n").split("\t")
            cc = code.split(".")[0]
            if cc in USE_ADMIN2:
                out[cc].append((code, name, ascii_, gid))
    return out


def read_cities():
    """{행정구역코드: [(lat, lon, pop, name)]}"""
    want = set(COUNTRY_CODE.values())
    zpath = fetch("cities500.zip")
    out = {}
    with zipfile.ZipFile(zpath) as z, z.open("cities500.txt") as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8"):
            c = line.rstrip("\n").split("\t")
            cc = c[8]
            if cc not in want:
                continue
            code = f"{cc}.{c[10]}" + (f".{c[11]}" if cc in USE_ADMIN2 else "")
            out.setdefault(code, []).append((float(c[4]), float(c[5]), int(c[14] or 0), c[1]))
    return out


def wikidata(gids):
    """GeoNames ID → {ko, alt[], lat, lon, iso} (Wikidata)."""
    res = {}
    gids = list(gids)
    for i in range(0, len(gids), 150):
        part = gids[i:i + 150]
        q = ("SELECT ?gid ?ko ?alt ?coord ?iso ?art WHERE { VALUES ?gid {%s} ?item wdt:P1566 ?gid. "
             "OPTIONAL{?item rdfs:label ?ko FILTER(lang(?ko)='ko')} "
             "OPTIONAL{?art schema:about ?item; schema:isPartOf <https://ko.wikipedia.org/>} "
             "OPTIONAL{?item skos:altLabel ?alt FILTER(lang(?alt)='ko')} "
             "OPTIONAL{?item wdt:P625 ?coord} OPTIONAL{?item wdt:P300 ?iso} }") % " ".join(f'"{g}"' for g in part)
        for attempt in range(3):
            try:
                u = "https://query.wikidata.org/sparql?format=json&query=" + quote(q)
                d = json.loads(urlopen(Request(u, headers=UA), timeout=120).read().decode("utf-8"))
                break
            except Exception as e:
                print("  Wikidata 재시도:", e)
                time.sleep(5)
        else:
            raise SystemExit("Wikidata 조회 실패")
        for b in d["results"]["bindings"]:
            g = b["gid"]["value"]
            r = res.setdefault(g, {"ko": "", "alt": set(), "lat": None, "lon": None, "iso": "", "art": ""})
            if "art" in b and not r["art"]:
                r["art"] = b["art"]["value"].rsplit("/", 1)[-1]
            if "ko" in b and not r["ko"]:
                r["ko"] = b["ko"]["value"]
            if "alt" in b:
                r["alt"].add(b["alt"]["value"])
            if "coord" in b and r["lat"] is None:
                m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", b["coord"]["value"])
                if m:
                    r["lon"], r["lat"] = float(m.group(1)), float(m.group(2))
            if "iso" in b and not r["iso"]:
                r["iso"] = b["iso"]["value"]
        time.sleep(1)
    return res


def ko_redirects(titles):
    """한국어 위키백과 '넘겨주기' 이름(예: 페낭 → 피낭주)을 검색용 별칭으로. {문서제목: {별칭}}"""
    from urllib.parse import unquote
    out = {}
    titles = [unquote(t).replace("_", " ") for t in titles if t]
    for i in range(0, len(titles), 50):
        part = titles[i:i + 50]
        cont = {}
        while True:
            u = ("https://ko.wikipedia.org/w/api.php?action=query&format=json&prop=redirects&rdlimit=max&redirects=1&titles="
                 + quote("|".join(part)) + "".join(f"&{k}={quote(str(v))}" for k, v in cont.items()))
            d = json.loads(urlopen(Request(u, headers=UA), timeout=60).read().decode("utf-8"))
            norm = {n["to"]: n["from"] for n in d.get("query", {}).get("normalized", [])}
            for pg in d.get("query", {}).get("pages", {}).values():
                t = pg.get("title", "")
                for rd in pg.get("redirects", []):
                    a = rd["title"]
                    if re.search(r"[가-힣]", a) and len(a) <= 20 and "(" not in a:
                        out.setdefault(t, set()).add(a)
            if "continue" not in d:
                break
            cont = {k: v for k, v in d["continue"].items()}
        time.sleep(0.5)
    return out


def geonames_ko(cc, gids):
    """(보조) GeoNames 다른이름 파일의 한국어 이름. Wikidata 에 한국어 이름이 없을 때만. {gid: (대표, {전부})}"""
    path = fetch(f"alternatenames/{cc}.zip")
    out = {}
    with zipfile.ZipFile(path) as z, z.open(f"{cc}.txt") as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8"):
            c = line.rstrip("\n").split("\t")
            if len(c) < 4 or c[2] != "ko" or c[1] not in gids:
                continue
            if len(c) > 7 and c[7] == "1":          # 옛 이름(isHistoric) 제외
                continue
            best, allv = out.get(c[1], ("", set()))
            allv.add(c[3])
            if not best or (len(c) > 4 and c[4] == "1"):
                best = c[3]
            out[c[1]] = (best, allv)
    return out


def wikidata_by_name(cc, name, towns):
    """(보조2) 영어 이름으로 Wikidata 검색 → '같은 나라' + '좌표가 그 지역 마을들 근처(80km)'인 것만 한국어 이름 채택.
    예: 2025년 통합된 베트남 성(省)은 새 항목에 한국어 이름이 없어, 같은 이름의 기존 항목(하노이 등)을 씀."""
    q = re.sub(r"\s*\(.*?\)|\s+(Province|City|Region|State|County)$", "", name).strip()
    try:
        u = ("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&language=en&limit=7&search="
             + quote(q))
        ids = [x["id"] for x in json.loads(urlopen(Request(u, headers=UA), timeout=60).read().decode("utf-8")).get("search", [])]
        if not ids:
            return None
        sp = ("SELECT ?item ?ko ?coord WHERE { VALUES ?item {%s} ?item wdt:P17 ?c. ?c wdt:P297 '%s'. "
              "?item rdfs:label ?ko FILTER(lang(?ko)='ko') ?item wdt:P625 ?coord }") % (" ".join("wd:" + i for i in ids), cc)
        d = json.loads(urlopen(Request("https://query.wikidata.org/sparql?format=json&query=" + quote(sp), headers=UA),
                               timeout=60).read().decode("utf-8"))
    except Exception:
        return None
    order = {i: n for n, i in enumerate(ids)}
    best = None
    for b in d["results"]["bindings"]:
        m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", b["coord"]["value"])
        if not m or not towns:
            continue
        lon, lat = float(m.group(1)), float(m.group(2))
        dist = min(haversine_km(lat, lon, t[0], t[1]) for t in towns)
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        if dist <= 80 and (best is None or order[qid] < best[0]):
            best = (order[qid], b["ko"]["value"])
    time.sleep(0.5)
    return best[1] if best else None


def thin(points):
    """격자당 인구 가장 많은 1점만 남김."""
    best = {}
    for lat, lon, pop, name in points:
        k = (round(lat / GRID), round(lon / GRID))
        if k not in best or pop > best[k][2]:
            best[k] = (lat, lon, pop, name)
    return list(best.values())


def rain_points(points, k_max=5, min_gap=80):
    """폭우 감시용 대표 도시(인구 많은 곳부터, 서로 떨어진 곳으로 최대 5곳)."""
    cand = sorted(points, key=lambda p: -p[2])
    big = [p for p in cand if p[2] >= 10000] or cand
    chosen = [big[0]] if big else []
    while len(chosen) < k_max:
        far = max(big, key=lambda p: min(haversine_km(p[0], p[1], c[0], c[1]) for c in chosen), default=None)
        if not far or min(haversine_km(far[0], far[1], c[0], c[1]) for c in chosen) < min_gap:
            break
        chosen.append(far)
    return [[p[3], round(p[0], 4), round(p[1], 4)] for p in chosen]


def main():
    kr_of = {v: k for k, v in COUNTRY_CODE.items()}
    print("① GeoNames 행정구역·마을 읽는 중…")
    admins = read_admins()
    cities = read_cities()
    print("② Wikidata 한국어 이름·좌표 조회 중…")
    wd = wikidata({a[3] for lst in admins.values() for a in lst})
    print("   한국어 위키백과 별칭(넘겨주기) 조회 중…")
    from urllib.parse import unquote
    rd = ko_redirects([w["art"] for w in wd.values() if w.get("art")])
    for w in wd.values():
        if w.get("art"):
            w["alt"] |= rd.get(unquote(w["art"]).replace("_", " "), set())

    # Wikidata 에 한국어 이름이 없는 곳 → GeoNames 한국어 이름으로 보충
    missing = {}
    for cc, lst in admins.items():
        for a in lst:
            if not wd.get(a[3], {}).get("ko"):
                missing.setdefault(cc, set()).add(a[3])
    for cc, gids in missing.items():
        print(f"   {kr_of[cc]}: 한국어 이름 {len(gids)}곳 → GeoNames 한국어 이름으로 보충 시도")
        for g, (best, allv) in geonames_ko(cc, gids).items():
            w = wd.setdefault(g, {"ko": "", "alt": set(), "lat": None, "lon": None, "iso": "", "art": ""})
            w["ko"] = best
            w["alt"] |= allv - {best}
    # 그래도 없으면 → 영어 이름으로 Wikidata 검색(같은 나라·좌표 확인된 것만)
    name_hits = []
    for cc, lst in admins.items():
        for code, name, ascii_, gid in lst:
            w = wd.setdefault(gid, {"ko": "", "alt": set(), "lat": None, "lon": None, "iso": "", "art": ""})
            if not w.get("ko"):
                ko = wikidata_by_name(cc, name, cities.get(code, []))
                if ko:
                    w["ko"] = ko
                    name_hits.append(f"{kr_of[cc]} {name} → {ko}")
    if name_hits:
        print("   영어 이름 검색으로 보충(좌표 확인):", ", ".join(name_hits))
    data, report = {}, []
    for cc, lst in sorted(admins.items(), key=lambda x: kr_of[x[0]]):
        country = kr_of[cc]
        rows, ko_n, warn = [], 0, []
        for code, name, ascii_, gid in lst:
            w = wd.get(gid, {})
            ko = w.get("ko") or ""
            ko_n += bool(ko)
            pts = cities.get(code, [])
            wlat, wlon = w.get("lat"), w.get("lon")
            if pts and wlat is not None:
                d = min(haversine_km(wlat, wlon, p[0], p[1]) for p in pts)
                if d > 150:
                    warn.append(f"{name}: Wikidata 좌표가 마을들과 {d:.0f}km 떨어짐")
            if not pts:
                if wlat is None:
                    warn.append(f"{name}: 좌표 없음(목록엔 표시, 필터는 전부 통과)")
                else:
                    pts = [(wlat, wlon, 0, name)]
                    warn.append(f"{name}: 마을 데이터 없어 대표 좌표 1점 사용")
            thinned = thin(pts)
            rows.append({
                "code": code, "ko": ko or name, "en": name, "ascii": ascii_,
                "alt": sorted(a for a in w.get("alt", set()) if a != ko),
                "iso": w.get("iso", ""), "abbr": code.split(".")[1] if cc == "US" else "",
                "center": [round(wlat, 4), round(wlon, 4)] if wlat is not None else None,
                "towns": len(pts),
                "points": [[round(p[0], 3), round(p[1], 3)] for p in thinned],
                "rain": rain_points(pts) if pts else [],
            })
        rows.sort(key=lambda r: r["ko"])
        data[country] = rows
        report.append((country, cc, len(rows), ko_n, warn))

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"출처": "GeoNames(CC-BY 4.0) + Wikidata(CC0)", "만든날": time.strftime("%Y-%m-%d"),
                   "나라": data}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"③ 저장: 행정구역.json ({os.path.getsize(OUT_JSON)/1e6:.1f}MB)")

    # 가입폼용(이름만): {나라: [[code, 한국어, 영어, 검색용 별칭]]}
    web = {c: [[r["code"], r["ko"], r["en"], " ".join(r["alt"] + ([r["ascii"]] if r["ascii"] != r["en"] else []))]
               for r in rows] for c, rows in data.items()}
    js = "const ADMIN_REGIONS = " + json.dumps(web, ensure_ascii=False, separators=(",", ":")) + ";"
    with open(SIGNUP, "r", encoding="utf-8") as f:
        html = f.read()
    a, b = "/*ADMIN_REGIONS_START*/", "/*ADMIN_REGIONS_END*/"
    if a in html and b in html:
        html = html[:html.index(a) + len(a)] + "\n" + js + "\n" + html[html.index(b):]
        with open(SIGNUP, "w", encoding="utf-8", newline="") as f:
            f.write(html)
        print(f"④ signup.html 목록 갱신 ({len(js)/1000:.0f}KB)")
    else:
        print("④ (signup.html 에 목록 자리표시가 없어 건너뜀)")

    print("\n| 나라 | 코드 | 지역 수 | 한국어 이름 | 확인 필요 |")
    print("|---|---|---|---|---|")
    for country, cc, n, ko_n, warn in report:
        print(f"| {country} | {cc} | {n} | {ko_n}/{n} | {len(warn)} |")
    print("\n[확인 필요 목록]")
    for country, cc, n, ko_n, warn in report:
        for w in warn:
            print(f"  {country}: {w}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
