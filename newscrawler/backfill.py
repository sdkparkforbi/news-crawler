# -*- coding: utf-8 -*-
"""
backfill.py — 빈 필드 교차 보충기 (원문 ↔ 네이버 미러)

extract 결과(jsonl)에서 기자/발행시각/본문이 빈 레코드를, 추출에 쓰지 않은
'반대편 소스'로 다시 떠서 그 필드만 채운다.
  · 미러로 추출됨(source=naver) → 원문 press URL 을 press 규칙으로 재시도
  · 원문으로 추출됨(source=press) → 네이버 미러 URL 을 미러 규칙으로 재시도
또한 일시적 실패(403 등)도 같은 URL 로 한 번 더 재시도한다.

그래도 안 채워지는 건 원천적으로 없는 것(회원전용·속보스텁·byline 없음)이므로
gaps_<keyword>.csv 로 빼서 사람이 직접 확인/입력할 수 있게 한다.

사용:
  python backfill.py bodies_고려아연.jsonl [--workers 5]
"""
import os, sys, json, csv, time, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import extract_article, make_session, _is_naver, _domain

FILL = ("reporter", "published_at", "body")


def _missing(r):
    m = []
    if not r.get("reporter"):
        m.append("reporter")
    if not r.get("published_at"):
        m.append("published_at")
    if not r.get("ok"):
        m.append("body")
    return m


def _alt_sources(r):
    """채우기용 대체 (url, is_naver) 후보 — 추출에 안 쓴 소스 우선."""
    url = (r.get("url") or "").strip()
    nav = (r.get("naver_url") or "").strip()
    used = r.get("source")
    alts = []
    if used == "naver":
        if url and not _is_naver(url):
            alts.append((url, False))                 # 원문(언론사 규칙)
    else:
        if _is_naver(nav):
            alts.append((nav, True))                  # 미러
    if url and not _is_naver(url):
        alts.append((url, False))                     # 같은 원문 재시도(일시적 실패 대비)
    # 중복 제거
    seen = set(); out = []
    for u, n in alts:
        if u not in seen:
            seen.add(u); out.append((u, n))
    return out


def backfill_one(r):
    miss = _missing(r)
    if not miss:
        return r, []
    sess = make_session()
    filled = []
    for alt_url, isnav in _alt_sources(r):
        try:
            alt = extract_article(alt_url, naver_url=(alt_url if isnav else ""),
                                  session=sess, prefer_naver=isnav)
        except Exception:
            continue
        for f in list(miss):
            if f == "body":
                if alt.get("ok") and alt.get("chars", 0) >= 150:
                    r["body"] = alt.get("body", ""); r["chars"] = alt.get("chars", 0)
                    r["body_method"] = alt.get("body_method", ""); r["ok"] = True
                    filled.append("body"); miss.remove("body")
            elif alt.get(f):
                r[f] = alt[f]; filled.append(f); miss.remove(f)
        if not miss:
            break
        time.sleep(0.3)
    return r, filled


def _reason(r):
    if not r.get("ok"):
        if r.get("chars", 0) and r["chars"] < 150:
            return "본문이 짧은 속보 스텁"
        return "원문에 본문 없음(회원전용/JS차단 등)"
    if not r.get("reporter"):
        return "기명 byline 없음(무기명/통신 전재)"
    if not r.get("published_at"):
        return "발행시각 표기 없음"
    return "확인필요"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--workers", type=int, default=5)
    a = ap.parse_args()

    recs = [json.loads(l) for l in open(a.jsonl, encoding="utf-8")]
    todo_idx = [i for i, r in enumerate(recs) if _missing(r)]
    print(f"전체 {len(recs)} / 빈칸 있는 레코드 {len(todo_idx)}건 → 교차 보충 시도")

    lock = threading.Lock()
    cnt = {"reporter": 0, "published_at": 0, "body": 0}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(backfill_one, recs[i]): i for i in todo_idx}
        done = 0
        for fut in as_completed(futs):
            i = futs[fut]; r, filled = fut.result(); recs[i] = r; done += 1
            with lock:
                for f in filled:
                    cnt[f] += 1
            if done % 20 == 0:
                print(f"  ...{done}/{len(todo_idx)}")

    with open(a.jsonl, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n보충 완료: 기자 +{cnt['reporter']} / 발행시각 +{cnt['published_at']} / 본문 +{cnt['body']}")
    n = len(recs)
    for fld, label in (("reporter", "기자"), ("published_at", "발행시각")):
        got = sum(1 for r in recs if r.get(fld))
        print(f"  {label}: {got}/{n} ({got/n*100:.0f}%)")
    body = sum(1 for r in recs if r.get("ok"))
    print(f"  본문: {body}/{n} ({body/n*100:.0f}%)")

    # 남은 빈칸 → 사람이 직접 채울 목록
    kw = os.path.basename(a.jsonl).replace("bodies_", "").replace(".jsonl", "")
    gaps = os.path.join(os.path.dirname(a.jsonl) or ".", f"gaps_{kw}.csv")
    rows = [r for r in recs if _missing(r)]
    with open(gaps, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["domain", "press", "title", "missing", "reason", "url", "naver_url"])
        for r in rows:
            w.writerow([r.get("domain", ""), r.get("press", ""), r.get("title", ""),
                        "|".join(_missing(r)), _reason(r), r.get("url", ""), r.get("naver_url", "")])
    print(f"\n남은 빈칸 {len(rows)}건 → {os.path.basename(gaps)} (직접 확인/입력용)")


if __name__ == "__main__":
    main()
