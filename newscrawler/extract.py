# -*- coding: utf-8 -*-
"""
korean-news-extractor — 한국 뉴스 기사 URL에서 구조화 데이터 추출.

추출 필드: title, published_at, modified_at, press, reporter, body,
           url, source('naver'|'press'), domain, chars, ok, error

전략 (왜 이런 순서인가):
  1) 네이버 미러(n.news.naver.com)가 있으면 그걸 우선 — 모든 언론사가 동일한
     #dic_area 구조 + 표준 메타라 추출이 깨끗하고 안정적이다.
  2) 원문 사이트면: 제목·발행시각·언론사·기자는 메타태그/JSON-LD 로 통일 추출
     (한국 언론 대부분 og:title, article:published_time, JSON-LD 를 노출).
  3) 본문은 (a) 도메인별 오버라이드 → (b) CMS 계열 공통 셀렉터
     (itemprop=articleBody, #article-view-content-div/.article-body) →
     (c) 리더빌리티(텍스트 밀도 최대 블록 자동 탐지) → (d) JSON-LD articleBody →
     (e) og:description 요약. 이렇게 층을 쌓으면 셀렉터를 모르는 새 매체도
     리더빌리티가 받아내므로 122개 도메인 꼬리까지 일반화된다.

CLI:
  python extract.py "<기사 URL>"                 # 단건 → JSON 출력
  python extract.py "<press_url>" --naver "<n.news.naver.com URL>"
  python extract.py --batch articles.csv --out bodies.jsonl [--workers 5]
     (CSV 컬럼: url 필수, naver_url 있으면 우선 사용)
"""
import os, sys, re, json, csv, time, random, argparse, threading, copy
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import ssl
import urllib3
from requests.adapters import HTTPAdapter
from lxml import html as LH

urllib3.disable_warnings()                          # verify=False 사용 시 경고 억제


class _LegacyTLSAdapter(HTTPAdapter):
    """일부 노후 한국 서버(vop.co.kr 등)는 urllib3 v2 의 엄격한 TLS 와
    핸드셰이크 실패(SSLV3_ALERT_HANDSHAKE_FAILURE) → 레거시 재협상 허용 +
    SECLEVEL 완화한 SSL 컨텍스트로 mount."""
    def init_poolmanager(self, *a, **k):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.options |= 0x4            # OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass
        try:
            ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        except Exception:
            pass
        k["ssl_context"] = ctx
        return super().init_poolmanager(*a, **k)

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# ── 도메인별 본문 컨테이너 오버라이드 (구조 조사로 확인된 특수 케이스) ──────────
#    값이 None = 정적 HTML 에 본문 없음(JS 렌더) → 네이버 미러 권장, 없으면 요약만.
DOMAIN_BODY = {
    "ajunews.com":      ['//*[contains(@class,"article_con")]', '//*[@id="articleBody"]'],
    "amenews.kr":       ['//*[contains(@class,"basicView")]'],
    "donga.com":        ['//*[contains(@class,"article_txt")]', '//*[contains(@class,"view_body")]'],
    "edaily.co.kr":     ['//*[contains(@class,"news_body")]'],
    "news.kbs.co.kr":   ['//*[@id="cont_newstext"]', '//*[contains(@class,"detail-body")]'],
    "news.mt.co.kr":    ['//*[@id="textBody"]'],
    "moneys.mt.co.kr":  ['//*[@id="textBody"]'],
    "news.tf.co.kr":    ['//*[@id="readBody"]'],
    "newsis.com":       ['//*[contains(@class,"viewer")]', '//*[contains(@class,"articleView")]'],
    "news.mtn.co.kr":   ['//article'],          # atomic-CSS, 본문이 <article> 안
    "itooza.com":       ['//*[contains(concat(" ",normalize-space(@class)," ")," content ")]'],
    "yna.co.kr":        ['//*[contains(@class,"story-news")]'],
    "biz.chosun.com":   None,   # React 렌더 — 정적 본문 없음
    "chosun.com":       None,
    "it.chosun.com":    None,
    "hankyung.com":     ['//*[@id="articletxt"]', '//*[contains(@class,"article-body")]'],
    "skyedaily.com":    ['//*[@id="gisaview"]', '//*[contains(@class,"articletext2")]', '//*[contains(@class,"articletext")]'],
    "newstomato.com":   ['//*[contains(concat(" ",normalize-space(@class)," ")," rns_text ")]'],
    "dnews.co.kr":      ['//*[contains(@class,"view_contents")]'],
    "kookje.co.kr":     ['//*[contains(@class,"news_article")]'],
    "weekly.donga.com": ['//*[contains(@class,"article_view")]'],
    "shindonga.donga.com": ['//*[contains(@class,"article_view")]'],
    "nongmin.com":      ['//*[contains(@class,"news_txt")]', '//*[contains(@class,"news_content_box")]'],
    "ytn.co.kr":        ['//*[@id="CmAdContent"]', '//*[contains(@class,"paragraph")]'],
    "mk.co.kr":         ['//div[@itemprop="articleBody"]', '//*[contains(@class,"news_cnt_detail_wrap")]'],
    "andongmbc.co.kr":  ['//*[@id="news_doc"]', '//*[contains(@class,"news_cont")]'],
    "tjb.co.kr":        ['//*[contains(concat(" ",normalize-space(@class)," ")," news-txt ")]'],
    "web.ubc.co.kr":    ['//*[contains(@class,"entry-content")]'],
}

# 모바일 전용 JS 셸 → 정적 본문이 있는 데스크톱 URL 재작성
def _desktopize(url):
    if not url:
        return url
    # m.skyedaily.com/news_view.html?ID=  →  www.skyedaily.com/news/news_view.html?ID=
    m = re.match(r"https?://m\.skyedaily\.com/news_view\.html\?ID=(\d+)", url)
    if m:
        return f"http://www.skyedaily.com/news/news_view.html?ID={m.group(1)}"
    return url

# CMS 계열 공통 본문 셀렉터 (위에서부터 시도) — 도메인 오버라이드 없을 때
FAMILY_BODY = [
    '//*[@id="dic_area"]',                       # 네이버
    '//*[@itemprop="articleBody"]',              # 주요지 다수
    '//*[@id="article-view-content-div"]',       # articleView CMS(뉴스ML/i-편한)
    '//*[contains(concat(" ",normalize-space(@class)," ")," article-body ")]',
    '//*[@id="articleBodyContents"]',
    '//*[contains(concat(" ",normalize-space(@class)," ")," article_body ")]',
    '//*[contains(concat(" ",normalize-space(@class)," ")," news_body ")]',
    '//*[contains(concat(" ",normalize-space(@class)," ")," view_con ")]',
]

# 본문 안에서 제거할 잡요소(광고/관련기사/공유/기자정보/캡션/스크립트)
# 본문 안에서 제거할 잡요소. 단어경계(?![A-Za-z]) 필수 — 없으면 'ad' 가
# 'adopseo'(본문 래퍼) 같은 정상 클래스의 접두를 오매칭해 본문을 통째 삭제함.
JUNK_RE = re.compile(
    r"(^|\s)(?:"
    r"(?:ad|ads|banner|promotion|related|recommend|reporter|journalist|byline|"
    r"copyright|sns|share|social|vod|player|caption|footer)(?![A-Za-z])"
    r"|tag_|link_news|photo_caption|end_photo_org"
    r")", re.I)

DATE_TEXT_RE = re.compile(
    r"(?:입력|등록|승인|작성|발행|기사입력)?\s*[:\s]*"
    r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})[일]?"
    r"(?:\s*[(\sT]*(\d{1,2}):(\d{2}))?")


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": random.choice(UA_POOL),
                      "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
    try:
        s.mount("https://", _LegacyTLSAdapter())   # 노후 TLS 서버 호환(vop 등)
    except Exception:
        pass
    s.verify = False                                # 인증서 오류 서버(andongmbc 등) 허용
    return s


def _domain(url):
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _norm_dt(s):
    """다양한 날짜 문자열 → 'YYYY-MM-DD HH:MM' (시각 없으면 날짜만)."""
    if not s:
        return ""
    s = s.strip()
    # UTC(Z/+00:00) 표기는 KST(+9h)로 변환 (biz.chosun 등)
    mu = re.match(r"(20\d{2})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2})?(?:\.\d+)?(?:Z|\+00:?00)", s)
    if mu:
        from datetime import datetime, timedelta
        dt = datetime(int(mu.group(1)), int(mu.group(2)), int(mu.group(3)),
                      int(mu.group(4)), int(mu.group(5))) + timedelta(hours=9)
        return dt.strftime("%Y-%m-%d %H:%M")
    m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?", s)
    if not m:
        # 2자리 연도 'YY.MM/DD HH:MM' / 'YY.MM.DD' (아이투자 등)
        m2 = re.search(r"\b(\d{2})\.(\d{1,2})[/.](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", s)
        if m2:
            hhmm = f" {m2.group(4).zfill(2)}:{m2.group(5)}" if m2.group(4) else ""
            return f"20{m2.group(1)}-{m2.group(2).zfill(2)}-{m2.group(3).zfill(2)}" + hhmm
        m = DATE_TEXT_RE.search(s)
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
    hh, mm = (m.group(4), m.group(5)) if m.lastindex and m.lastindex >= 5 and m.group(4) else (None, None)
    return f"{y}-{mo}-{d}" + (f" {hh.zfill(2)}:{mm}" if hh else "")


def _jsonld(doc):
    """JSON-LD 에서 NewsArticle 류 객체 dict 반환(없으면 {})."""
    for s in doc.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            data = json.loads(s)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and "@graph" in data:
            items = data["@graph"]
        for obj in items:
            if isinstance(obj, dict) and ("articleBody" in obj or
                    str(obj.get("@type", "")).lower().endswith("article")):
                return obj
    return {}


def _brace_obj(s, key):
    """문자열 s 에서 key 뒤의 첫 '{' 부터 균형 맞는 '}' 까지를 JSON 파싱."""
    i = s.find(key)
    if i < 0:
        return None
    i = s.find("{", i)
    if i < 0:
        return None
    depth = 0; instr = False; esc = False
    for j in range(i, len(s)):
        c = s[j]
        if esc:
            esc = False; continue
        if c == "\\":
            esc = True; continue
        if c == '"':
            instr = not instr
        if instr:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[i:j + 1])
                except Exception:
                    return None
    return None


def _arc_body(doc):
    """Arc XP(Fusion.globalContent) 렌더 사이트(조선 계열 등) 본문 복구.
    본문이 정적 DOM 엔 없지만 <script> 안 globalContent JSON 의
    content_elements[type=text] 에 들어있다."""
    for sc in doc.xpath('//script/text()'):
        if "Fusion.globalContent" in sc and "content_elements" in sc:
            d = _brace_obj(sc, "Fusion.globalContent=")
            if isinstance(d, dict):
                parts = [re.sub("<[^>]+>", "", e.get("content", ""))
                         for e in (d.get("content_elements") or [])
                         if isinstance(e, dict) and e.get("type") == "text"]
                txt = "\n\n".join(p.strip() for p in parts if p.strip())
                if len(txt) > 150:
                    return txt
    return ""


def _clean_body(node):
    node = copy.deepcopy(node)            # 원본 doc 훼손 방지: byline/기자 요소가
                                          # 본문 정리 중 삭제돼 이후 기자추출이 실패하던 버그
    for bad in node.xpath('.//script | .//style | .//figcaption | .//iframe'):
        p = bad.getparent()
        if p is not None:
            p.remove(bad)
    for el in node.xpath('.//*[@class or @id]'):
        key = ((el.get("id") or "") + " " + (el.get("class") or ""))
        if JUNK_RE.search(key):
            p = el.getparent()
            if p is not None:
                p.remove(el)
    txt = node.text_content().replace("\xa0", " ")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\n[ \t]+", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _readability(doc):
    """텍스트 밀도가 가장 높은 블록 자동 선택(셀렉터 모르는 매체 폴백)."""
    best, best_score = None, 0
    for el in doc.xpath('//div | //section | //article'):
        text = el.text_content()
        tlen = len(text.strip())
        if tlen < 300:
            continue
        link_len = sum(len(a.text_content()) for a in el.xpath('.//a'))
        if tlen and link_len / tlen > 0.4:        # 링크 범벅(=관련기사/내비) 배제
            continue
        p_len = sum(len(p.text_content()) for p in el.xpath('./p | ./div/p'))
        score = tlen - 2 * link_len + p_len      # 본문 단락 많을수록 가점
        if score > best_score:
            best, best_score = el, score
    return _clean_body(best) if best is not None else ""


def _meta(doc, props):
    for p in props:
        v = doc.xpath(f'//meta[@property="{p}" or @name="{p}"]/@content')
        if v and v[0].strip():
            return v[0].strip()
    return ""


def _extract_body(doc, domain):
    # (a) 도메인 오버라이드 — 매칭 노드 중 텍스트가 가장 긴 것 선택(부분매칭 방지)
    if domain in DOMAIN_BODY:
        sels = DOMAIN_BODY[domain]
        if sels is None:                       # JS 렌더 → 박힌 JSON(Arc XP) 먼저, 없으면 요약
            arc = _arc_body(doc)
            if arc:
                return arc, "arc"
            return _meta(doc, ["og:description", "description"]), "js_summary"
        cands = [n for xp in sels for n in doc.xpath(xp)]
        if cands:
            best = max(cands, key=lambda e: len(e.text_content()))
            if len(best.text_content().strip()) > 150:
                return _clean_body(best), "override"
    # (b) CMS 계열 공통 — 매칭 중 최장 노드
    for xp in FAMILY_BODY:
        n = doc.xpath(xp)
        if n:
            best = max(n, key=lambda e: len(e.text_content()))
            if len(best.text_content().strip()) > 200:
                return _clean_body(best), "family"
    # (c) 리더빌리티
    rb = _readability(doc)
    if len(rb) > 250:
        return rb, "readability"
    # (d) JSON-LD
    jl = _jsonld(doc)
    if jl.get("articleBody"):
        return jl["articleBody"].strip(), "jsonld"
    # (e) Arc XP(Fusion) 박힌 JSON
    arc = _arc_body(doc)
    if arc:
        return arc, "arc"
    # (f) 요약 폴백
    desc = _meta(doc, ["og:description", "description"])
    return desc, "summary"


# ── 언론사별 기자(byline) 추출 규칙 (실측 조사로 확인·검증) ────────────────
#    ('meta', 메타속성) / ('jsonld', None) / ('xpath', XPath) / ('regex', 패턴)
DOMAIN_REPORTER = {
    "hankyung.com":          ("jsonld", None),
    "magazine.hankyung.com": ("xpath", '//div[contains(@class,"writer-info")]//span[contains(@class,"name")]//text()'),
    "sedaily.com":           ("xpath", '//div[contains(@class,"byline")]/p[contains(@class,"writer")]'),
    "biz.chosun.com":        ("jsonld", None),
    "chosun.com":            ("jsonld", None),
    "it.chosun.com":         ("jsonld", None),
    "yna.co.kr":             ("meta", "author"),
    "edaily.co.kr":          ("xpath", '//p[contains(@class,"reporter_name")]//text()'),
    "mk.co.kr":              ("xpath", '//*[contains(@class,"editor_name")]//text()'),
    "news.mt.co.kr":         ("meta", "author"),
    "moneys.mt.co.kr":       ("meta", "author"),
    "biz.sbs.co.kr":         ("jsonld", None),
    "news.einfomax.co.kr":   ("meta", "og:article:author"),
    "view.asiae.co.kr":      ("meta", "article:author"),
    "asiae.co.kr":           ("meta", "article:author"),
    "news1.kr":              ("meta", "dable:author"),
    "news.heraldcorp.com":   ("meta", "author"),
    "bloter.net":            ("xpath", '//article[contains(@class,"writer")]//strong[contains(@class,"name")]//text()'),
    "businesspost.co.kr":    ("xpath", '//div[contains(@class,"author_info")]/span[1]//text()'),
    "news.tf.co.kr":         ("xpath", '//li[contains(@class,"editor")]'),
    "news.dealsitetv.com":   ("xpath", '//*[contains(@class,"nis-reporter-name")]'),
    "topdaily.kr":           ("xpath", '//span[contains(@class,"byline-top")]'),
    "asiatime.co.kr":        ("xpath", '//span[@id="writeName"]//text()'),
    "newsis.com":            ("regex", r"\[[^\]]*=뉴시스\]\s*([가-힣]+(?:\s+[가-힣]+)*)\s*(?:선임기자|기자|특파원|논설위원|편집위원|전문위원|연구원|애널리스트|에디터|칼럼니스트)"),
    "fnnews.com":            ("xpath", '//span[contains(@class,"article-view__reporter")]//text()'),
    "dailian.co.kr":         ("xpath", '//p[contains(@class,"reporter")]'),
    "pinpointnews.co.kr":    ("xpath", '//strong[contains(@class,"name")]//text()'),
    "mk.co.kr":              ("jsonld", None),     # editor_name 비어있는 템플릿 다수 → JSON-LD author
    "joongang.co.kr":        ("jsonld", None),
    "baduk.hangame.com":     ("regex", r"작성자\s*[:：]\s*([가-힣]{2,5})"),
    "beyondpost.co.kr":      ("regex", r"\[비욘드포스트\s+([가-힣]{2,4})\s*기자\]"),
    "stoo.com":              ("regex", r"\[스포츠투데이\s+([가-힣]{2,4})\s*기자\]"),
    "kwangju.co.kr":         ("regex", r"/\s*([가-힣]{2,4})\s*기자\s+[\w.\-]+@"),
    "itooza.com":            ("xpath", '//div[contains(@class,"bd-small") and contains(@class,"writer")]//text()'),
    "tjb.co.kr":             ("regex", r"([가-힣]{2,4})\s*취재기자"),
    "andongmbc.co.kr":       ("regex", r"\d{4}-\d{2}-\d{2}\s*ㅣ\s*([가-힣]{2,4})\s*ㅣ"),
    "web.ubc.co.kr":         ("regex", r"([가-힣]{2,4})\s*기자"),
    "radio.ytn.co.kr":       ("regex", r"진행\s*[:：]\s*([가-힣]{2,4})"),
    "view.asiae.co.kr":      ("xpath", '(//*[@id="txt_area"]//p[contains(.,"무단전재")]/preceding-sibling::p)[last()]//text()'),
}

# 발행시각이 meta article:published_time 으로 안 잡히는 곳만 특수 처리
DOMAIN_DATE = {
    "topdaily.kr":    ("xpath", '//span[contains(@class,"published_at")]'),
    "asiatime.co.kr": ("regex", r"입력\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})"),
    "itooza.com":     ("regex", r"(\d{2}\.\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2})"),
    "skyedaily.com":  ("regex", r"입력\s*(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2})"),
    "paxetv.com":     ("regex", r"(?:승인|입력|등록)\s*(20\d{2}\.\d{2}\.\d{2}(?:\s+\d{2}:\d{2})?)"),
    "kwangju.co.kr":  ("xpath", '//div[contains(@class,"read_time")]'),
    "web.ubc.co.kr":  ("xpath", '//time[contains(@class,"entry-date")]/@datetime'),
    "andongmbc.co.kr":("regex", r"(20\d{2}-\d{2}-\d{2})\s*ㅣ"),
    "tjb.co.kr":      ("xpath", '//div[contains(@class,"article-date")]//time/@datetime'),
}

# newstomato: 기자명이 <b class="hc_name">이름</b> (기자 접미 없음, byline 클래스 패턴과 불일치)
DOMAIN_REPORTER["newstomato.com"] = ("xpath", '//b[contains(@class,"hc_name")]')


def _load_learned():
    """discover.py 가 학습한 규칙을 자동 병합 (내장 규칙 우선)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learned_rules.json")
    try:
        lr = json.load(open(path, encoding="utf-8"))
    except Exception:
        return
    for dom, rule in lr.get("reporter", {}).items():
        DOMAIN_REPORTER.setdefault(dom, tuple(rule))
    for dom, rule in lr.get("date", {}).items():
        DOMAIN_DATE.setdefault(dom, tuple(rule))


_load_learned()


def _jsonld_author(doc):
    au = _jsonld(doc).get("author")
    if isinstance(au, dict):
        return au.get("name", "")
    if isinstance(au, list) and au:
        return ", ".join((a.get("name", "") if isinstance(a, dict) else str(a)) for a in au)
    return au if isinstance(au, str) else ""


def _clean_reporter(s):
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*\([^)]*@[^)]*\)", "", s)         # (email) 괄호 제거
    s = re.sub(r"\s*[\w.\-]+@[\w.\-]+", "", s)        # 이메일 제거
    # 네이버/언론사 UI 잔재 절단(공유버튼·구독 등이 byline 요소에 붙는 경우)
    s = re.split(r"(?:TALK|구독|좋아요|공유하기|공유|프로필|기자페이지|카카오|페이스북|트위터|네이버|메일보내기)", s)[0]
    s = s.strip(" ,·|​")
    # ' 이름 기자' 단위 추출·중복 제거 (byline 이 페이지에 여러 번 렌더되는 경우)
    units = re.findall(r"[가-힣]{2,5}\s*(?:선임기자|기자|특파원|논설위원|편집위원|전문위원|연구원|애널리스트|에디터|칼럼니스트)", s)
    if units:
        uniq = list(dict.fromkeys(re.sub(r"\s+", "", u) for u in units))[:4]
        return ", ".join(re.sub(r"(선임기자|기자|특파원|논설위원|편집위원|전문위원|연구원|애널리스트|에디터|칼럼니스트)$", r" \1", u) for u in uniq)
    return "" if len(s) > 40 else s                  # 이름만 있는 경우는 그대로


def _extract_reporter(doc, domain, body):
    rule = DOMAIN_REPORTER.get(domain)
    name = ""
    if rule:
        kind, val = rule
        if kind == "meta":
            name = _meta(doc, [val])
        elif kind == "jsonld":
            name = _jsonld_author(doc)
        elif kind == "xpath":
            res = doc.xpath(val)
            if res and hasattr(res[0], "text_content"):              # 요소노드(여러 기자)
                parts = [" ".join(e.text_content().split()) for e in res]
                name = ", ".join(dict.fromkeys(p for p in parts if p))
            else:                                                    # text/속성 노드
                name = " ".join(str(x).strip() for x in res if str(x).strip())
        elif kind == "regex":
            m = re.search(val, doc.text_content())
            name = m.group(1) if m else ""
    name = _clean_reporter(name)
    if name:
        return name
    name = _clean_reporter(_jsonld_author(doc))       # 제너릭1: JSON-LD author
    if name:
        return name
    # 제너릭2: byline 요소(class·id·커스텀태그)에서 '이름 기자' — 최단(가장 정확한) 매치
    cands = []
    for el in doc.xpath('//*[contains(@class,"reporter") or contains(@id,"reporter")'
                        ' or contains(@class,"byline") or contains(@id,"byline")'
                        ' or contains(@class,"journalist") or contains(@id,"journalist")'
                        ' or contains(@class,"writer") or contains(@class,"art_journalist")'
                        ' or contains(@class,"editor") or self::reporter or self::byline'
                        ' or self::journalist_name]'):
        t = " ".join(el.text_content().split())
        if t and len(t) < 90:
            m = re.search(r"[가-힣]{2,4}\s*(?:선임기자|기자|특파원|논설위원|편집위원|전문위원|연구원|애널리스트|에디터|칼럼니스트)", t)
            if m:
                cands.append((len(t), re.sub(r"\s+", " ", m.group(0))))
    if cands:
        return min(cands)[1]
    # 제너릭3: 기사 작성자 메타(기자가 아닌 팀·칼럼니스트·AI 등도 포함)
    for prop in ("og:article:author", "dable:author", "article:author", "dc.creator"):
        v = _clean_reporter(_meta(doc, [prop]))
        if v and "http" not in v.lower() and not re.search(r"\b(?:inc|corp|ltd)\b", v.lower()):
            return v
    txt = body or ""                                  # 제너릭4: 본문 앞/뒤 byline 패턴
    for pat in (r"=[^)\]]*?[)\]]\s*([가-힣]{2,4})\s*(?:기자|특파원)",
                r"([가-힣]{2,4})\s*(?:선임기자|기자|특파원|논설위원|편집위원|전문위원|연구원|애널리스트|에디터|칼럼니스트)\s*[=:]?\s*[\w.\-]+@"):
        m = re.search(pat, txt[:300]) or re.search(pat, txt[-250:])
        if m:
            return m.group(1) + " 기자"
    return ""


def _extract_date_override(doc, domain):
    rule = DOMAIN_DATE.get(domain)
    if not rule:
        return ""
    kind, val = rule
    if kind == "xpath":
        res = doc.xpath(val)
        if res:
            t = res[0].text_content() if hasattr(res[0], "text_content") else str(res[0])
            return _norm_dt(t)
    elif kind == "regex":
        m = re.search(val, doc.text_content())
        if m:
            return _norm_dt(m.group(1))
    return ""


def _next_data(doc):
    s = doc.xpath('//script[@id="__NEXT_DATA__"]/text()')
    if not s:
        return {}
    try:
        return json.loads(s[0])
    except Exception:
        return {}


def _news1_extract(doc):
    """news1.kr 은 Next.js 렌더 — 정적 HTML 엔 footer 만 있고 본문/기자/날짜는
    __NEXT_DATA__.props.pageProps.articleView 에 들어있다."""
    j = _next_data(doc)
    av = (((j.get("props") or {}).get("pageProps") or {}).get("articleView")) or {}
    if not av:
        return None
    parts = []
    for b in (av.get("contentArrange") or []):
        if isinstance(b, dict) and b.get("type") == "text":
            t = re.sub(r"<[^>]+>", " ", b.get("content") or "")
            t = re.sub(r"\s+", " ", t).strip()
            if t:
                parts.append(t)
    body = "\n".join(parts)
    rep = (av.get("author") or "").strip()
    if not rep:                                       # reporter_box: [{name:...}] 형태
        names = []
        for x in (av.get("reporter_box") or []):
            if isinstance(x, dict):
                names.append(x.get("name") or x.get("reporter_name") or x.get("reporterNm") or "")
            elif isinstance(x, str):
                names.append(x)
        rep = ", ".join(n for n in names if n)
    published = _norm_dt(av.get("published_time") or "") or _norm_dt(av.get("pubdate_at") or "")
    modified = _norm_dt(av.get("modified_time") or "") or _norm_dt(av.get("updated_at") or "")
    img = av.get("image") or ""
    if isinstance(img, dict):
        img = img.get("src") or img.get("url") or ""
    return dict(title=av.get("title") or "", published_at=published, modified_at=modified,
                press="뉴스1", reporter=_clean_reporter(rep), body=body, source="press",
                body_method="news1_next", image=img, summary=av.get("description") or "")


def _kbs_extract(url, session):
    """KBS TV뉴스는 네이버 미러에 앵커 리드 1문장(≈59자)만 노출되고 정적 원문은
    메뉴 크롬뿐 → 전체 본문은 내부 API(getNewsInfo)의 originNewsContents 에만 있음."""
    m = re.search(r"ncd=(\d+)", url)
    if not m:
        return None
    try:
        r = session.get(f"https://news.kbs.co.kr/api/getNewsInfo?newsCode={m.group(1)}",
                        headers={"Referer": url}, timeout=15)
        d = r.json().get("data") or {}
    except Exception:
        return None
    html = d.get("originNewsContents") or d.get("newsContents") or ""
    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    rep = ""
    for x in (d.get("reporters") or []):
        if isinstance(x, dict) and x.get("name"):
            rep = x["name"] + " 기자"
            break
    published = _norm_dt(d.get("serviceTime") or d.get("regDate") or "")
    return dict(title=d.get("newsTitle") or "", published_at=published, modified_at="",
                press="KBS", reporter=_clean_reporter(rep), body=body, source="press",
                body_method="kbs_api", image="", summary=body[:150])


def parse_naver(doc, url):
    title = "".join(doc.xpath('//*[@id="title_area"]//text()')).strip() or _meta(doc, ["og:title"])
    body_node = (doc.xpath('//*[@id="dic_area"]') or doc.xpath('//*[@id="newsct_article"]')
                 or doc.xpath('//*[@id="articeBody"]') or doc.xpath('//*[@id="comp_news_article"]'))
    body = _clean_body(body_node[0]) if body_node else _meta(doc, ["og:description"])
    times = doc.xpath('//*[contains(@class,"media_end_head_info_datestamp_time")]/@data-date-time')
    published = _norm_dt(times[0]) if times else _norm_dt(_meta(doc, ["article:published_time"]))
    modified = _norm_dt(times[1]) if len(times) > 1 else _norm_dt(_meta(doc, ["article:modified_time"]))
    press = (doc.xpath('//*[contains(@class,"media_end_head_top_logo")]//img/@alt') or
             [_meta(doc, ["og:site_name"])])[0].strip()
    rep = doc.xpath('//*[contains(@class,"media_end_head_journalist_name")]')
    reporter = _clean_reporter(" ".join(rep[0].text_content().split())) if rep else ""
    if not reporter:                                  # 통신/방송 미러: span.byline_s (yonhapnewstv·hani 등)
        bs = doc.xpath('//*[contains(@class,"byline_s")]//text()')
        if bs:
            reporter = _clean_reporter(" ".join(" ".join(bs).split()))
    if not reporter:                                  # 미러에 기자요소 없으면 폴백
        reporter = _clean_reporter(_jsonld_author(doc))
    if not reporter:
        m = re.search(r"([가-힣]{2,4})\s*(?:기자|특파원)", (body or "")[:220] + " " + (body or "")[-220:])
        reporter = (m.group(1) + " 기자") if m else ""
    return dict(title=title, published_at=published, modified_at=modified,
                press=press, reporter=reporter, body=body, source="naver",
                body_method="naver", image=_meta(doc, ["og:image"]),
                summary=_meta(doc, ["og:description"]))


def parse_press(doc, url):
    domain = _domain(url)
    if domain.endswith("news1.kr"):                   # Next.js 전용 어댑터
        _n1 = _news1_extract(doc)
        if _n1 and len(_n1.get("body") or "") >= 60:
            return _n1
    jl = _jsonld(doc)
    title = _meta(doc, ["og:title", "twitter:title"]) or (jl.get("headline") or "")
    title = re.sub(r"\s*[-|·]\s*[^-|·]{1,20}$", "", title).strip() if title else title
    published = (_norm_dt(_meta(doc, ["article:published_time", "og:article:published_time"]))
                 or _norm_dt(jl.get("datePublished", ""))
                 or _extract_date_override(doc, domain))
    modified = (_norm_dt(_meta(doc, ["article:modified_time"]))
                or _norm_dt(jl.get("dateModified", "")))
    press = _meta(doc, ["og:site_name"])
    if not press and isinstance(jl.get("publisher"), dict):
        press = jl["publisher"].get("name", "")
    press = press or domain
    body, method = _extract_body(doc, domain)
    if not published:                      # 폴백1: date/time 요소 중 시각(HH:MM) 포함만 (현재날짜 위젯 배제)
        for _t in doc.xpath('//*[contains(@class,"date") or contains(@class,"time")]//text()')[:15]:
            if re.search(r"update|업데이트|수정|최종", _t, re.I):   # 동적 갱신시각 위젯 배제(paxetv·skyedaily 등)
                continue
            _nd = _norm_dt(_t)
            if _nd and ":" in _nd:
                published = _nd; break
    if not published:                      # 폴백2: 입력/등록/byline 영역의 'YYYY MM DD HH:MM'
        m = re.search(r"(?:입력|등록|발행|작성)?\s*[:\sㅣ|]*"
                      r"(20\d{2}[-./]\d{1,2}[-./]\d{1,2}\s+\d{1,2}:\d{2})", doc.text_content())
        if m:
            published = _norm_dt(m.group(1))
    reporter = _extract_reporter(doc, domain, body)   # per-press 규칙 + 제너릭 폴백
    return dict(title=title, published_at=published, modified_at=modified,
                press=press, reporter=reporter, body=body, source="press",
                body_method=method, image=_meta(doc, ["og:image"]),
                summary=_meta(doc, ["og:description"]))


def _is_naver(u):
    return any(x in (u or "") for x in ("n.news.naver.com", "news.naver.com",
                                        "sports.naver.com", "entertain.naver.com"))


def _robust_doc(resp):
    """charset 오감지로 인한 본문 깨짐(mojibake) 방지.
    lxml 에 '바이트'를 직접 넘기면 일부 사이트(businesspost·asiatime 등)에서
    인코딩을 잘못 잡아 글자가 깨진다. requests 가 헤더로 잡은 인코딩(없거나
    latin-1 이면 chardet 추정)으로 '문자열'을 만들어 넘기면 정확하다."""
    enc = resp.encoding
    if not enc or enc.lower() in ("iso-8859-1", "latin-1"):
        enc = resp.apparent_encoding or "utf-8"
    try:
        return LH.fromstring(resp.content.decode(enc, errors="replace"))
    except (LookupError, ValueError):
        return LH.fromstring(resp.content.decode("utf-8", errors="replace"))


_JUNK_MENU = ("로그인", "회원가입", "구독신청", "회사소개", "기사제보", "마이페이지",
              "전체메뉴", "페이스북", "카카오채널", "인스타그램", "지면보기", "ON AIR",
              "브랜드채널", "최근검색어", "입찰정보", "무단 전재", "무단전재", "재배포 금지")


def _is_junk_body(body):
    """메뉴/footer 크롬이 본문으로 잘못 잡혔는지(거짓 성공) 판정."""
    h = (body or "")[:250]
    return sum(1 for k in _JUNK_MENU if k in h) >= 3


def extract_article(url, naver_url="", session=None, prefer_naver=True, timeout=20):
    s = session or make_session()
    naver_url = (naver_url or "").strip()
    orig_url = (url or "").strip()                     # 머지 키 보존용 원본 URL
    url = _desktopize(orig_url)                         # 모바일 JS 셸 → 데스크톱 URL(가져오기용)
    # 시도 순서: 미러 우선이면 [미러, 원문], 아니면 [원문, 미러].
    # 앞 후보가 실패(차단·빈 본문)하면 다음 후보로 폴백 → 동시 수집 중 안정성↑.
    cands = []
    if prefer_naver and _is_naver(naver_url):
        cands.append((naver_url, True))
        if url and not _is_naver(url):
            cands.append((url, False))
    elif _is_naver(url):
        cands.append((url, True))
    else:
        cands.append((url, False))
        if _is_naver(naver_url):
            cands.append((naver_url, True))
    rec = {"url": orig_url, "naver_url": naver_url, "domain": _domain(url),
           "fetched": "", "ok": False, "chars": 0, "error": ""}
    if "news.kbs.co.kr" in url:                        # KBS는 내부 API로 전체 본문 확보
        krec = _kbs_extract(url, s)
        if krec and len(krec.get("body") or "") >= 60:
            rec.update(krec); rec["fetched"] = "kbs_api"
            rec["chars"] = len(krec["body"]); rec["ok"] = True
            return rec
    for target, naver in cands:
        rec["fetched"] = target
        try:
            r = s.get(target, headers={"Referer": "https://search.naver.com/"}, timeout=timeout)
            if r.status_code != 200:
                rec["error"] = f"HTTP {r.status_code}"
                continue
            # JS 리다이렉트 껍데기(thebell 등) 따라가기: 작은 페이지 + location.href
            if len(r.content) < 3000 and "location." in r.text:
                locs = re.findall(r'location\.(?:href|replace)\s*[=(]\s*["\']([^"\']+)["\']', r.text)
                nonmob = [u for u in locs if "/m/" not in u and not re.search(r"//m\.", u)]
                if nonmob or locs:
                    nxt = urljoin(target, (nonmob or locs)[0])
                    try:
                        r2 = s.get(nxt, headers={"Referer": target}, timeout=timeout)
                        if r2.status_code == 200 and len(r2.content) > len(r.content):
                            r, target = r2, nxt
                    except Exception:
                        pass
            doc = _robust_doc(r)
            parsed = parse_naver(doc, target) if naver else parse_press(doc, target)
            rec.update(parsed)
            rec["chars"] = len(rec.get("body") or "")
            # 크롬(거짓성공) 본문은 ok로 치지 않음 → 다음 후보(미러 등)로 폴백
            rec["ok"] = rec["chars"] >= 150 and not _is_junk_body(rec.get("body"))
            if rec["ok"]:
                rec["error"] = ""
                return rec
        except Exception as e:
            rec["error"] = str(e)[:150]
    return rec


# ───────────────────────── 배치 ─────────────────────────
def run_batch(in_csv, out_jsonl, workers=5, prefer_naver=True):
    rows = list(csv.DictReader(open(in_csv, encoding="utf-8-sig")))
    uniq = {}
    for r in rows:
        u = (r.get("url") or "").strip()
        if u and u not in uniq:
            uniq[u] = r
    rows = list(uniq.values())
    done = set()
    try:
        for line in open(out_jsonl, encoding="utf-8"):
            done.add(json.loads(line)["url"])
    except FileNotFoundError:
        pass
    todo = [r for r in rows if (r.get("url") or "").strip() not in done]
    print(f"총 {len(rows):,} / 완료 {len(done):,} / 남은 {len(todo):,} (워커 {workers})")
    lock = threading.Lock()
    okc = n = 0

    def work(r):
        sess = make_session()
        return extract_article(r.get("url", ""), r.get("naver_url", ""), sess, prefer_naver)

    with ThreadPoolExecutor(max_workers=workers) as ex, open(out_jsonl, "a", encoding="utf-8") as out:
        futs = [ex.submit(work, r) for r in todo]
        for fut in as_completed(futs):
            rec = fut.result(); n += 1
            with lock:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
            okc += 1 if rec["ok"] else 0
            if n % 50 == 0:
                print(f"  {n:,}/{len(todo):,}  성공 {okc:,} ({okc/n*100:.0f}%)")
    print(f"완료: {n:,} 처리, 성공 {okc:,}.  → {out_jsonl}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--naver", default="")
    ap.add_argument("--batch")
    ap.add_argument("--out", default="bodies.jsonl")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--no-naver-first", action="store_true")
    a = ap.parse_args()
    prefer = not a.no_naver_first
    if a.batch:
        run_batch(a.batch, a.out, a.workers, prefer)
    elif a.url:
        rec = extract_article(a.url, a.naver, prefer_naver=prefer)
        rec_print = dict(rec)
        if rec_print.get("body") and len(rec_print["body"]) > 600:
            rec_print["body"] = rec_print["body"][:600] + f"... [+{len(rec['body'])-600}자]"
        print(json.dumps(rec_print, ensure_ascii=False, indent=2))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
