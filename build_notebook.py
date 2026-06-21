# -*- coding: utf-8 -*-
"""뉴스크롤러 노트북 생성: notebooks/뉴스크롤러.ipynb (Colab · JupyterLab 공용).

생성:  python build_notebook.py
"""
import os
import nbformat as nbf

nb = nbf.v4.new_notebook(); cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s))
co = lambda s: cells.append(nbf.v4.new_code_cell(s))

md("""# 뉴스크롤러 — 네이버 뉴스 수집 + 본문추출 (Colab · JupyterLab 공용)

검색어 한 건을 **① 일별 수집 → ② 본문·메타 추출 → ③ 요약/CSV** 까지 함수 호출만으로 실행합니다.
수집은 네이버 무한스크롤의 **more-API 커서**를 따라가 전수에 가깝게 모읍니다
(구버전 `&start=` 방식의 ~66% 과소수집 버그 수정).

### 어디서 실행하나
| 환경 | 준비 |
|---|---|
| **Google Colab** | 이 노트북만 업로드 → 아래 1번 셀이 repo를 클론. *private repo면* Colab Secrets(🔑)에 `GH_TOKEN`(GitHub 토큰) 등록 + 노트북 액세스 ON |
| **미들턴 JupyterLab / 로컬** | `git clone <repo>` 후 `news-crawler/notebooks/뉴스크롤러.ipynb` 열어 실행 (1번 셀이 로컬 패키지를 자동 인식) |

> 수집·추출은 오래 걸립니다. **작업폴더(2번 셀)에 저장**하니 끊겨도 다시 Run 하면 이어서 합니다.""")

co('''#@title 1) 코드 준비 + 라이브러리 설치 (환경 자동감지)
import os, sys, subprocess

REPO = "sdkparkforbi/news-crawler"        # ← 본인 repo로 바꿔도 됨 (user/name)

def _sh(c):
    r = subprocess.run(c, shell=True, capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")

def _have_pkg():
    # repo 안(=JupyterLab/로컬)에서 실행하면 newscrawler 가 상위 폴더에 있음
    here = os.getcwd()
    for up in [here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))]:
        if up and os.path.isdir(os.path.join(up, "newscrawler")):
            if up not in sys.path: sys.path.insert(0, up)
            return True
    try:
        import newscrawler  # 이미 설치/경로에 있으면
        return True
    except Exception:
        return False

if not _have_pkg():
    # Colab 등: repo 클론 (private 이면 토큰 필요)
    token = os.environ.get("GH_TOKEN", "")
    try:
        from google.colab import userdata
        token = token or (userdata.get("GH_TOKEN") or "")
    except Exception:
        pass
    auth = f"{token}@" if token else ""
    name = REPO.split("/")[-1]
    print(_sh(f"git clone -q https://{auth}github.com/{REPO} 2>/dev/null || (cd {name} && git pull -q)"))
    if os.path.isdir(name): sys.path.insert(0, name)

print(_sh("pip -q install requests lxml"))
from newscrawler import pipeline as nc
print("준비완료 |", "Colab" if nc.in_colab() else "JupyterLab/로컬")''')

co('''#@title 2) 작업폴더 (수집물 영구저장 · 재접속 시 이어하기)
# Colab     -> 구글드라이브 마운트 후 MyDrive/뉴스작업
# JupyterLab-> 홈 아래 뉴스작업  (절대경로를 직접 줘도 됨)
nc.setup("뉴스작업")''')

md("""## Step 0 — 수집 설정
검색어와 기간만 정합니다. (사진/동영상 탭 제외하고 전체 기사 수집)""")

co('''#@title 3) 설정
KEYWORD = "고려아연"      #@param {type:"string"}
START   = "2024-09-01"   #@param {type:"string"}
END     = "2024-09-30"   #@param {type:"string"}
print(f"검색어 {KEYWORD} | {START} ~ {END}")''')

md("""## Step 1 — 수집 (네이버 일별 · more-API 커서)
재실행하면 미수집일만 이어서 합니다. 결과: `articles_<검색어>.csv`""")
co('''nc.collect(KEYWORD, START, END)''')

md("""## Step 2 — 본문·메타 추출
제목/발행일/언론사/기자/본문/요약/이미지를 채웁니다. 신규 URL만 이어서. 결과: `bodies_<검색어>.jsonl`""")
co('''nc.extract_bodies(KEYWORD, workers=6)''')

md("""## Step 3 — 요약 + 엑셀용 CSV 내보내기""")
co('''nc.summary(KEYWORD)
nc.to_csv(KEYWORD)   # bodies_<검색어>.csv (utf-8-sig, 엑셀에서 바로 열림)''')

md("""## 결과물
작업폴더에 생깁니다:
- `articles_<검색어>.csv` — 수집 인벤토리(날짜·언론사·제목·원문URL·네이버URL)
- `daily_counts_<검색어>.csv` — 일별 건수(재개 체크포인트)
- `bodies_<검색어>.jsonl` — 본문/메타 (추출 원본)
- `bodies_<검색어>.csv` — 엑셀용 정리본

### 팁
- 본문이 비거나 기자가 안 잡힌 매체는 자가학습으로 보강:
  `python -m newscrawler.discover --from-jsonl bodies_<검색어>.jsonl --min-count 1`
  → 다시 `nc.extract_bodies(KEYWORD)` → `python -m newscrawler.backfill bodies_<검색어>.jsonl`
- 수집이 자꾸 하루 10건에서 막히면(스로틀) 잠시 후 다시 실행하면 자동 복구 재시도합니다.""")

nb["cells"] = cells
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3"}
os.makedirs("notebooks", exist_ok=True)
out = os.path.join("notebooks", "뉴스크롤러.ipynb")
nbf.write(nb, out)
print("생성:", out, f"({len(cells)} cells)")
