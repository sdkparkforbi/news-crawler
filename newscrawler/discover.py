# -*- coding: utf-8 -*-
"""
discover.py — per-press 규칙 자동 학습기 (스킬 자가 확장)

extract.py 가 기자/발행시각을 못 잡은 언론사를, 샘플 기사를 직접 떠서
구조(메타·JSON-LD·byline 요소 셀렉터)를 자동 탐지·검증하고
`learned_rules.json` 에 기록한다. extract.py 는 시작 시 이 파일을 읽어
내장 DOMAIN_REPORTER/DOMAIN_DATE 에 자동 병합(내장 우선)하므로,
한 번 학습하면 코드 수정 없이 그 매체가 영구 반영된다.

사용:
  # 추출 결과(jsonl)에서 기자 빈 도메인들을 한꺼번에 자동 학습
  python discover.py --from-jsonl bodies_고려아연.jsonl [--max 40] [--min-count 2]

  # 단일 도메인 수동 학습
  python discover.py <domain> <sample_url1> [sample_url2]

  # 학습된 규칙 보기
  python discover.py --show
"""
import os, sys, re, json, time, argparse
from collections import defaultdict
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import (make_session, _robust_doc, _meta, _jsonld_author,
                     _clean_reporter, _domain, _norm_dt)

LEARNED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "learned_rules.json")
ROLE = re.compile(r"[가-힣]{2,4}\s*(?:기자|특파원|논설위원|에디터)")
BYLINE_HINT = ("report", "writ", "journal", "byline", "editor", "art_journ",
               "name", "author", "byname", "reporter")
META_AUTHORS = ["author", "og:article:author", "dable:author", "dc.creator",
                "article:author", "dc.contributor"]


def _looks_like_name(v, press):
    if not v or (press and v.strip() == press.strip()):
        return False
    v = v.strip()
    return bool(re.fullmatch(r"[가-힣]{2,4}(?:\s*(?:기자|특파원|논설위원))?"
                             r"(?:\s*[,·]\s*[가-힣]{2,4}(?:\s*(?:기자|특파원|논설위원))?)*", v))


def _byline_selector(doc, press):
    """본문/헤더에서 '이름 기자' 를 담은 가장 타이트한 요소 → 재사용 가능한 XPath."""
    best = None
    for el in doc.xpath('//span | //p | //div | //a | //strong | //li | //h4 | //em | //cite'):
        t = " ".join(el.text_content().split())
        if not t or len(t) > 60 or not ROLE.search(t):
            continue
        if press and press in t:
            continue
        cls = (el.get("class") or "").strip()
        eid = (el.get("id") or "").strip()
        key = (cls + " " + eid).lower()
        score = -len(t) + (1500 if any(k in key for k in BYLINE_HINT) else 0)
        if eid:
            sel = '//*[@id="%s"]' % eid
        elif cls:
            toks = cls.split()
            tok = next((x for x in toks if any(k in x.lower() for k in BYLINE_HINT)), toks[0])
            sel = '//*[contains(concat(" ",normalize-space(@class)," ")," %s ")]' % tok
        else:
            continue
        if best is None or score > best[0]:
            best = (score, sel)
    return best[1] if best else None


def _verify_xpath(docs, sel, press):
    """셀렉터가 두 샘플 모두에서 '기자' 포함 짧은 값을 주는지 검증 → 정제값/실패."""
    vals = []
    for doc in docs:
        res = doc.xpath(sel)
        if res and hasattr(res[0], "text_content"):
            t = " ".join(res[0].text_content().split())
        else:
            t = " ".join(str(x).strip() for x in res)
        t = _clean_reporter(t)
        if not t or (press and press in t):
            return None
        vals.append(t)
    return vals if len(vals) == len(docs) else None


def discover_reporter(docs, press):
    """(method, rule, sample_values) 또는 None."""
    # 1) 메타
    for prop in META_AUTHORS:
        vals = [_clean_reporter(_meta(d, [prop])) for d in docs]
        if all(vals) and all(_looks_like_name(v, press) for v in vals):
            return ("meta", prop, vals)
    # 2) JSON-LD author
    vals = [_clean_reporter(_jsonld_author(d)) for d in docs]
    if all(vals) and all(_looks_like_name(v, press) for v in vals):
        return ("jsonld", "", vals)
    # 3) byline 요소 셀렉터 (첫 샘플에서 후보 만들고 전체 검증)
    sel = _byline_selector(docs[0], press)
    if sel:
        vals = _verify_xpath(docs, sel, press)
        if vals:
            return ("xpath", sel, vals)
    return None


def discover_date(docs):
    """메타로 안 잡히는 경우만 규칙 제안. (method, rule, samples) 또는 None."""
    if all(_norm_dt(_meta(d, ["article:published_time", "og:article:published_time"]))
           for d in docs):
        return None    # 내장 메타로 충분
    # 입력 YYYY-MM-DD HH:MM 정규식
    pat = r"입력\s*[:\s]*(\d{4}[-.]\d{2}[-.]\d{2}\s+\d{2}:\d{2})"
    vals = []
    for d in docs:
        m = re.search(pat, d.text_content())
        if not m:
            vals = None; break
        vals.append(_norm_dt(m.group(1)))
    if vals:
        return ("regex", pat, vals)
    return None


def load_learned():
    if os.path.exists(LEARNED):
        try:
            return json.load(open(LEARNED, encoding="utf-8"))
        except Exception:
            pass
    return {"reporter": {}, "date": {}}


def save_learned(lr):
    json.dump(lr, open(LEARNED, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def learn_domain(session, domain, urls):
    docs = []
    for u in urls[:2]:
        try:
            r = session.get(u, headers={"User-Agent": session.headers["User-Agent"]}, timeout=20)
            if r.status_code == 200:
                docs.append(_robust_doc(r))
        except Exception:
            pass
        time.sleep(0.4)
    if not docs:
        return None, None, "fetch_fail"
    press = _meta(docs[0], ["og:site_name"]) or ""
    rep = discover_reporter(docs, press)
    dt = discover_date(docs)
    return rep, dt, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("domain", nargs="?")
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--from-jsonl")
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--min-count", type=int, default=1)
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    lr = load_learned()
    if a.show:
        print(json.dumps(lr, ensure_ascii=False, indent=1)); return

    # 학습 대상 (domain → sample urls)
    targets = {}
    if a.from_jsonl:
        recs = [json.loads(l) for l in open(a.from_jsonl, encoding="utf-8")]
        miss = defaultdict(list)
        for r in recs:
            if r.get("ok") and not r.get("reporter"):
                u = (r.get("url") or "")
                if u.startswith("http"):
                    miss[_domain(u)].append(u)
        targets = {d: us for d, us in miss.items() if len(us) >= a.min_count}
        targets = dict(sorted(targets.items(), key=lambda x: -len(x[1]))[:a.max])
    elif a.domain and a.urls:
        targets = {a.domain: a.urls}
    else:
        ap.print_help(); return

    print("학습 대상 도메인: %d개" % len(targets))
    session = make_session()
    learned_rep = learned_dt = 0
    for i, (dom, urls) in enumerate(targets.items(), 1):
        rep, dt, st = learn_domain(session, dom, urls)
        msg = "[%d/%d] %s" % (i, len(targets), dom)
        if rep:
            lr["reporter"][dom] = [rep[0], rep[1]]; learned_rep += 1
            msg += "  기자<%s>=%s" % (rep[0], rep[2][0])
        else:
            msg += "  기자=미발견(%s)" % st
        if dt:
            lr["date"][dom] = [dt[0], dt[1]]; learned_dt += 1
            msg += "  날짜<%s>" % dt[0]
        print(msg)
        time.sleep(0.5)
    save_learned(lr)
    print("\n학습 완료: 기자 규칙 +%d, 날짜 규칙 +%d  → %s" % (learned_rep, learned_dt, os.path.basename(LEARNED)))
    print("extract.py 가 다음 실행부터 자동 반영합니다.")


if __name__ == "__main__":
    main()
