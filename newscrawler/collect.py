# -*- coding: utf-8 -*-
"""
네이버 뉴스 '일별 수집기' — more-API 커서 방식 (1단계: 제목+URL 목록 / 본문은 2단계 extract.py)
═══════════════════════════════════════════════════════════════════════════════
하루씩 검색 → 그 날 기사들의 (제목·매체·원문URL·네이버URL)을 모아 저장.

■ 왜 more-API인가 (구버전 &start= 의 ~66% 과소수집 버그 수정)
  네이버 뉴스 검색 결과의 무한스크롤은 HTML `&start=` 페이징이 아니라
  `https://s.search.naver.com/p/newssearch/3/api/tab/more` **커서 API**로 더 불러온다.
  · `&start=` 방식은 하루 쿼리에서 ~120건에서 막힘(중복만 반복). 실측 2024-09-13
    '고려아연': &start= 120건 vs more-API **408건**(언론사 53→147곳). 약 1/3만 수집됐던 것.
  · 검색 결과 HTML 안에 첫 more URL이 JSON-이스케이프(`...api\\/tab\\/more...`)로 박혀 있고,
    응답 JSON은 `{collection:[{html}], url:<다음 more URL>}` 구조. `url` 커서를 따라가며
    `collection[*].html` 을 제목링크로 파싱하면 전수에 가깝게 수집된다(네이버 최대 2,000건/쿼리).

■ 스로틀(차단) 회피·복구
  more-API는 지속 스크래핑 시 구간 스로틀된다. 증상: 그 날이 정확히 10건(첫 페이지)으로
  잘리고 더보기 커서가 막힘. 대응: ① 10건+커서존재면 스로틀 판정 → 새 세션+warm+백오프로
  재시도(최대 4회), ② 예방적으로 N일마다 세션 갱신, ③ 페이지네이션은 순차(referer 체인)만.

■ 체크포인트 2개 (중간에 끊겨도 재실행하면 미수집일만 이어감)
    daily_counts_<KW>.csv  (date,count)                         ← 재개 기준 + 진행률
    articles_<KW>.csv      (date,press,title,url,naver_url)     ← 본문수집용 인벤토리

실행(CLI):  KW=고려아연 SD=2024-01-01 ED=2024-12-31 python -m newscrawler.collect
다음 단계:  python -m newscrawler.extract --batch articles_<KW>.csv --out bodies_<KW>.jsonl
"""
import os, csv, json, time, random, requests
from datetime import datetime, timedelta
from lxml import html as LH

# ───────────────────────── 설정 (환경변수 override) ─────────────────────────
SEARCH_KEYWORD = os.environ.get("KW", "고려아연")
START_DATE     = os.environ.get("SD", "2024-01-01")
END_DATE       = os.environ.get("ED", "2024-12-31")
PHOTO_TYPE     = int(os.environ.get("PHOTO", "0"))   # 0=전체, 1=포토, 2=동영상

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]
BLOCK_PATTERNS = ["비정상적인 접근", "잠시 후 다시", "캡차", "captcha", "unusual traffic", "are you a robot"]
TIT_XPATH   = '//a[@nocr="1" and @data-heatmap-target=".tit"]'   # 제목링크 = 기사 원문 URL
SEARCH_REFERER = "https://search.naver.com/"


# ───────────────────────── 세션 / 워밍업 ─────────────────────────
def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(UA_POOL),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Sec-Ch-Ua": '"Chromium";v="122", "Google Chrome";v="122", "Not(A:Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0", "Sec-Ch-Ua-Platform": '"Windows"',
        "Upgrade-Insecure-Requests": "1", "Connection": "keep-alive",
    })
    return s


def warmup(s):
    """네이버 메인→검색 순서로 쿠키를 적재(봇 판정 완화)."""
    try:
        s.get("https://www.naver.com/", timeout=10); time.sleep(random.uniform(1.2, 2.5))
        s.get("https://search.naver.com/", timeout=10, headers={"Referer": "https://www.naver.com/"})
        time.sleep(random.uniform(1.2, 2.5))
    except Exception as e:
        print("  [warmup 실패]", e)


# ───────────────────────── URL / 파싱 헬퍼 ─────────────────────────
def build_search_url(keyword, day):
    """하루치 검색 페이지(HTML) URL. sort=1(최신순) 필수 — 관련도순은 유사기사를 묶어 과소집계."""
    q   = requests.utils.quote(keyword)
    nso = requests.utils.quote(f"so:r,p:from{day}to{day}")
    dot = f"{day[:4]}.{day[4:6]}.{day[6:]}"
    return ("https://search.naver.com/search.naver?where=news"
            f"&query={q}&sm=tab_opt&sort=1&photo={PHOTO_TYPE}&field=0&pd=3"
            f"&ds={dot}&de={dot}&start=1"
            f"&mynews=0&office_type=0&office_section_code=0&office_category=0"
            f"&service_area=0&nso={nso}")


def find_more_url(text):
    """검색 HTML / more-응답 html 안에서 다음 more 커서 URL을 추출(JSON 이스케이프 해제)."""
    i = text.find('api\\/tab\\/more')          # JSON-이스케이프된 형태 (\\/)
    if i >= 0:
        st = text.rfind('https:', 0, i); en = text.find('"', i)
        if st >= 0 and en > st:
            try:
                return json.loads('"' + text[st:en] + '"')   # \/ & 등 정확히 해제
            except Exception:
                return text[st:en].replace('\\/', '/').replace('\\u0026', '&')
    i = text.find('api/tab/more')               # 혹시 비이스케이프 형태
    if i >= 0:
        st = text.rfind('https:', 0, i); en = text.find('"', i)
        if st >= 0 and en > st:
            return text[st:en]
    return None


def rows_from_doc(doc, day):
    """제목링크 기준으로 카드 단위 정렬: 제목·원문URL·언론사·네이버URL을 함께 뽑는다.
    (평행 리스트 인덱싱은 요소 개수가 달라 라벨이 밀리는 버그가 있어 카드 컨테이너로 묶음)."""
    out = []
    for t in doc.xpath(TIT_XPATH):
        h = t.get("href")
        if not h:
            continue
        press, nav = "", ""
        card = t
        for _ in range(6):
            card = card.getparent()
            if card is None:
                break
            if len(card.xpath('.//a[@data-heatmap-target=".tit"]')) == 1:
                prn = card.xpath('.//*[contains(@class,"sds-comps-profile-info-title-text")]')
                nvn = card.xpath('.//a[@data-heatmap-target=".nav"]/@href')
                if prn or nvn:
                    press = prn[0].text_content().strip() if prn else ""
                    nav = nvn[0] if nvn else ""
                    break
        out.append({"date": day, "url": h, "naver": nav, "press": press,
                    "title": t.text_content().strip()})
    return out


# ───────────────────────── 네트워크 (차단/재시도) ─────────────────────────
def _blocked(text):
    low = text[:8000].lower()
    return any(p in text[:8000] for p in BLOCK_PATTERNS) or any(p in low for p in BLOCK_PATTERNS)


def fetch_html(session, url, referer, max_retries=5):
    """검색 페이지 HTML 요청. (doc, 'ok') 또는 (None, 'blocked')."""
    for attempt in range(max_retries):
        try:
            r = session.get(url, headers={"Referer": referer}, timeout=30)
            if r.status_code in (429, 403, 503, 502):
                wait = min(60 * (2 ** attempt) + random.uniform(0, 30), 600)
                print(f"      [차단 HTTP {r.status_code}] {round(wait)}s"); time.sleep(wait); continue
            if _blocked(r.text):
                wait = min(60 * (2 ** attempt) + random.uniform(0, 30), 600)
                print(f"      [차단 안내문] {round(wait)}s"); time.sleep(wait); continue
            doc = LH.fromstring(r.content)
            if not doc.xpath('//*[@id="main_pack"]') and not doc.xpath('//*[contains(@class,"api_subject_bx")]'):
                wait = 30 + random.uniform(0, 15)
                print(f"      [컨테이너 없음] {round(wait)}s"); time.sleep(wait); continue
            return doc, r.text, "ok"
        except requests.exceptions.RequestException as e:
            wait = 10 + random.uniform(0, 10)
            print(f"      [네트워크 {e}] {round(wait)}s"); time.sleep(wait)
    return None, "", "blocked"


def fetch_more(session, url, referer, max_retries=4):
    """more 커서(XHR) 요청 → JSON dict 또는 None."""
    hdr = {"Accept": "application/json, text/javascript, */*; q=0.01",
           "X-Requested-With": "XMLHttpRequest", "Referer": referer,
           "Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
    for attempt in range(max_retries):
        try:
            r = session.get(url, headers=hdr, timeout=30)
            if r.status_code in (429, 403, 503, 502) or _blocked(r.text):
                time.sleep(min(20 * (2 ** attempt) + random.uniform(0, 10), 300)); continue
            if not r.text.strip():
                return None
            return r.json()
        except requests.exceptions.RequestException:
            time.sleep(8 + random.uniform(0, 8))
        except ValueError:                       # JSON 파싱 실패
            return None
    return None


# ───────────────────────── 하루치 수집 ─────────────────────────
def collect_one_day(session, keyword, day, page_cap=300):
    """하루치 기사 목록을 more-커서로 끝까지 수집.
    반환: (rows, throttled, session). throttled=True면 10건에서 막혀 재시도 필요."""
    search_url = build_search_url(keyword, day)
    doc, html, status = fetch_html(session, search_url, SEARCH_REFERER)
    if status != "ok":
        return [], True, session

    seen, rows = set(), []
    for r in rows_from_doc(doc, day):
        if r["url"] not in seen:
            seen.add(r["url"]); rows.append(r)

    more = find_more_url(html)
    page = 1
    while more:
        page += 1
        j = fetch_more(session, more, search_url)
        if not j:
            # 첫 more에서 막히고 첫 페이지가 10건뿐이면 스로틀 의심
            if page == 2 and len(rows) <= 12:
                return rows, True, session
            break
        coll = j.get("collection") or []
        chunk_html = "".join(c.get("html", "") for c in coll)
        if not chunk_html.strip():
            break
        new = 0
        for r in rows_from_doc(LH.fromstring(chunk_html), day):
            if r["url"] not in seen:
                seen.add(r["url"]); rows.append(r); new += 1
        nxt = j.get("url")
        more = nxt if (isinstance(nxt, str) and nxt.startswith("http")) else find_more_url(chunk_html)
        if new == 0 or not more:
            break
        if page > page_cap:
            print("      [페이지 상한 도달]")
            break
        time.sleep(random.uniform(0.8, 1.6))
    return rows, False, session


def collect_one_day_resilient(session, keyword, day, max_tries=4):
    """스로틀이면 새 세션+warm+백오프로 재시도. (rows, session) 반환."""
    rows, throttled, session = collect_one_day(session, keyword, day)
    tries = 1
    while throttled and tries < max_tries:
        wait = random.uniform(12, 22) * tries
        print(f"      [스로틀 의심 {len(rows)}건] 새 세션 + {round(wait)}s 백오프 (재시도 {tries}/{max_tries-1})")
        time.sleep(wait)
        session = make_session(); warmup(session)
        rows, throttled, session = collect_one_day(session, keyword, day)
        tries += 1
    return rows, session


# ───────────────────────── 체크포인트 IO ─────────────────────────
def safe_csv_write(path, row, mode="a", retries=15):
    """OneDrive/백신이 파일을 잠깐 잠가도 죽지 않게 재시도하며 한 줄 기록."""
    for i in range(retries):
        try:
            with open(path, mode, newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(row)
            return
        except PermissionError:
            time.sleep(min(0.5 + i * 0.7, 8))
    with open(path, mode, newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerow(row)


# ───────────────────────── 메인 루프 ─────────────────────────
def collect(keyword=None, start=None, end=None, outdir=".", refresh_every=6):
    """네이버 일별 수집(재개형). 반환: articles CSV 경로.

    keyword/start/end 미지정 시 환경변수 KW/SD/ED 사용.
    refresh_every: 예방적 세션 갱신 주기(일).
    """
    keyword = keyword or SEARCH_KEYWORD
    start   = start   or START_DATE
    end     = end     or END_DATE
    count_csv = os.path.join(outdir, f"daily_counts_{keyword}.csv")
    art_csv   = os.path.join(outdir, f"articles_{keyword}.csv")

    d0 = datetime.strptime(start, "%Y-%m-%d"); d1 = datetime.strptime(end, "%Y-%m-%d")
    all_days, d = [], d0
    while d <= d1:
        all_days.append(d.strftime("%Y%m%d")); d += timedelta(days=1)

    done = {}
    if os.path.exists(count_csv):
        with open(count_csv, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                done[row["date"]] = int(row["count"])
    else:
        safe_csv_write(count_csv, ["date", "count"], mode="w")
    if not os.path.exists(art_csv):
        safe_csv_write(art_csv, ["date", "press", "title", "url", "naver_url"], mode="w")

    todo = [x for x in all_days if x not in done]
    print(f"검색어 {keyword} | {start}~{end} | sort=최신순 | more-API 커서")
    print(f"전체 {len(all_days)}일 / 완료 {len(done)}일 / 남은 {len(todo)}일\n")

    session = make_session(); warmup(session)
    for i, day in enumerate(todo, 1):
        if i > 1 and (i - 1) % refresh_every == 0:        # 예방적 세션 갱신
            session = make_session(); warmup(session)
        rows, session = collect_one_day_resilient(session, keyword, day)
        for r in rows:
            safe_csv_write(art_csv, [r["date"], r["press"], r["title"], r["url"], r["naver"]])
        done[day] = len(rows)
        safe_csv_write(count_csv, [day, len(rows)])
        run = sum(done.values())
        print(f"[{i}/{len(todo)}] {day}: {len(rows):>4}건   누적 {run:,}")
        time.sleep(random.uniform(2, 4))

    total = sum(done.values())
    print("\n" + "=" * 50)
    print(f"  총 {total:,}건  ({len(done)}일)")
    monthly = {}
    for dt, n in done.items():
        monthly[dt[:6]] = monthly.get(dt[:6], 0) + n
    print("  월별:")
    for ym in sorted(monthly):
        print(f"    {ym}: {monthly[ym]:,}")
    print("=" * 50)
    print(f"  목록 저장: {art_csv}")
    print(f"  다음: python -m newscrawler.extract --batch {art_csv} --out bodies_{keyword}.jsonl")
    return art_csv


def main():
    collect()


if __name__ == "__main__":
    main()
