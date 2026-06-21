# -*- coding: utf-8 -*-
"""환경(Colab · JupyterLab · 로컬) 무관 수집·추출 래퍼.

수집(collect) → 본문추출(extract_bodies) 두 단계만 다룬다. 비밀키가 필요 없다
(네이버 검색·본문 크롤만 함). 결과 파일은 모두 *현재 작업폴더*에 떨어지고
체크포인트라 재실행하면 이어서 한다 — 그래서 `setup()` 으로 영구 폴더에서
실행하는 것을 권장(특히 Colab: 구글드라이브, 미들턴: 홈 아래 폴더).
"""
import os, csv, json, glob
from datetime import datetime

from . import collect as _collect
from . import extract as _extract


# ───────────────────────── 환경 감지 / 준비 ─────────────────────────
def in_colab():
    try:
        import google.colab  # noqa: F401
        return True
    except Exception:
        return False


def setup(workdir=None, mount_drive=True):
    """작업폴더로 이동(없으면 생성). 수집물이 영구 저장되어 재접속 시 이어서 한다.

    · Colab        : mount_drive=True면 구글드라이브를 마운트하고
                     /content/drive/MyDrive/<workdir> 로 이동.
    · JupyterLab/로컬: 현재 위치(또는 홈) 아래 <workdir> 로 이동.
    workdir=None 이면 폴더 이동 없이 환경 정보만 출력.
    """
    env = "Google Colab" if in_colab() else "JupyterLab/로컬"
    if workdir:
        if in_colab() and mount_drive:
            try:
                from google.colab import drive
                drive.mount("/content/drive")
                base = "/content/drive/MyDrive"
            except Exception as e:
                print("  [드라이브 마운트 실패, 로컬 폴더 사용]", e)
                base = "/content"
            path = workdir if os.path.isabs(workdir) else os.path.join(base, workdir)
        else:
            path = workdir if os.path.isabs(workdir) else os.path.join(os.path.expanduser("~"), workdir)
        os.makedirs(path, exist_ok=True)
        os.chdir(path)
    print(f"환경: {env} | 작업폴더: {os.getcwd()}")
    return os.getcwd()


# ───────────────────────── 1. 수집 ─────────────────────────
def collect(keyword, start, end, outdir=".", refresh_every=6):
    """네이버 일별 수집(more-API). 재실행 시 미수집일만 이어감. → articles_<KW>.csv"""
    return _collect.collect(keyword, start, end, outdir=outdir, refresh_every=refresh_every)


# ───────────────────────── 2. 본문·메타 추출 ─────────────────────────
def extract_bodies(keyword, workers=6, indir=".", prefer_naver=True):
    """수집 CSV → 본문/기자/발행일 추출. 신규 URL만 이어감. → bodies_<KW>.jsonl"""
    in_csv = os.path.join(indir, f"articles_{keyword}.csv")
    out    = os.path.join(indir, f"bodies_{keyword}.jsonl")
    if not os.path.exists(in_csv):
        raise FileNotFoundError(f"{in_csv} 없음 — 먼저 collect() 를 실행하세요.")
    _extract.run_batch(in_csv, out, workers=workers, prefer_naver=prefer_naver)
    return out


# ───────────────────────── 3. 결과 요약 / CSV 내보내기 ─────────────────────────
def summary(keyword, indir="."):
    """수집·추출 현황 요약(일수·총건수·월별·본문 성공률)을 출력하고 dict 반환."""
    art = os.path.join(indir, f"articles_{keyword}.csv")
    cnt = os.path.join(indir, f"daily_counts_{keyword}.csv")
    bod = os.path.join(indir, f"bodies_{keyword}.jsonl")
    info = {"keyword": keyword}

    if os.path.exists(cnt):
        days = list(csv.DictReader(open(cnt, encoding="utf-8-sig")))
        total = sum(int(d["count"]) for d in days)
        monthly = {}
        for d in days:
            monthly[d["date"][:6]] = monthly.get(d["date"][:6], 0) + int(d["count"])
        info.update(days=len(days), collected=total, monthly=monthly)
        print(f"[{keyword}] 수집: {len(days)}일 · {total:,}건")
        print("  월별:", "  ".join(f"{k}:{v:,}" for k, v in sorted(monthly.items())))
    else:
        print(f"[{keyword}] 아직 수집 기록(daily_counts) 없음")

    if os.path.exists(bod):
        n = ok = chars = 0
        for line in open(bod, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line); n += 1
            ok += 1 if r.get("ok") else 0
            chars += len(r.get("body") or "")
        info.update(extracted=n, body_ok=ok,
                    body_rate=round(ok / n, 3) if n else 0,
                    avg_chars=round(chars / n) if n else 0)
        print(f"  본문추출: {n:,}건 · 성공 {ok:,} ({ok/n*100:.0f}%) · 평균 {chars//n if n else 0:,}자")
    else:
        print(f"  본문추출 전(bodies_{keyword}.jsonl 없음)")
    return info


def to_csv(keyword, indir=".", out=None):
    """bodies_<KW>.jsonl → 보기 좋은 CSV(엑셀용 utf-8-sig). 반환: CSV 경로."""
    bod = os.path.join(indir, f"bodies_{keyword}.jsonl")
    out = out or os.path.join(indir, f"bodies_{keyword}.csv")
    if not os.path.exists(bod):
        raise FileNotFoundError(f"{bod} 없음 — 먼저 extract_bodies() 를 실행하세요.")
    cols = ["no", "published_at", "press", "reporter", "title", "body", "chars",
            "url", "naver_url", "image", "summary", "ok"]
    n = 0
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for line in open(bod, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line); n += 1
            w.writerow({"no": n, "published_at": r.get("published_at", ""),
                        "press": r.get("press", ""), "reporter": r.get("reporter", ""),
                        "title": r.get("title", ""),
                        "body": (r.get("body") or "").replace("\r", " ").replace("\n", " "),
                        "chars": len(r.get("body") or ""), "url": r.get("url", ""),
                        "naver_url": r.get("naver_url", ""), "image": r.get("image", ""),
                        "summary": (r.get("summary") or "").replace("\n", " "),
                        "ok": r.get("ok", "")})
    print(f"내보내기 완료 → {out} ({n:,}행)")
    return out
