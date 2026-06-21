# -*- coding: utf-8 -*-
"""노트북 2종 생성:
    notebooks/뉴스크롤러.ipynb        (Google Colab 용)
    notebooks/뉴스크롤러_서버.ipynb    (온프레미스 서버 JupyterLab 용)

코드 셀은 동일하고 안내 문구만 환경에 맞게 다릅니다.

생성:  python build_notebook.py
"""
import os
import nbformat as nbf

REPO = "sdkparkforbi/news-crawler"

# ── 환경별로 다른 문구 ──────────────────────────────────────────────
COLAB_INTRO = """# 네이버 뉴스 수집기  (Google Colab용)

검색어 하나로 네이버 뉴스를 **모아서 → 본문까지 채워 → 엑셀(CSV)로** 만들어 줍니다.

## 사용법 (딱 3가지만 하면 됩니다)
1. **위에서부터 셀(회색 칸)을 차례대로 ▶ 실행**합니다. (왼쪽 ▶ 버튼 클릭, 또는 `Shift+Enter`)
2. **`설정` 셀**에서 검색어와 기간만 본인 것으로 바꿉니다.
3. 끝까지 실행하면 결과 파일이 생깁니다.

> 오래 걸려도 괜찮습니다. 중간에 멈추거나 창을 닫아도, **다시 ▶ 실행하면 하던 데부터 이어서** 합니다."""

SERVER_INTRO = """# 네이버 뉴스 수집기  (서버 JupyterLab용)

검색어 하나로 네이버 뉴스를 **모아서 → 본문까지 채워 → 엑셀(CSV)로** 만들어 줍니다.

## 사용법 (딱 3가지만 하면 됩니다)
1. **위에서부터 셀(회색 칸)을 차례대로 ▶ 실행**합니다. (위쪽 ▶ 버튼 클릭, 또는 `Shift+Enter`)
2. **`설정` 셀**에서 검색어와 기간만 본인 것으로 바꿉니다.
3. 끝까지 실행하면 결과 파일이 **서버 홈 폴더의 `뉴스작업`** 안에 생깁니다.

> 오래 걸려도 괜찮습니다. 중간에 멈추거나 커널을 다시 시작해도, **다시 ▶ 실행하면 하던 데부터 이어서** 합니다."""

COLAB_SAVE = '''#@title 2) 저장 폴더  (그냥 ▶ 실행만)
# 결과를 여기에 저장합니다. 중간에 끊겨도 다시 실행하면 이어서 합니다.
# 구글드라이브 연결 팝업이 뜨면 "허용"을 누르세요. → MyDrive/뉴스작업 에 저장됩니다.
nc.setup("뉴스작업")'''

SERVER_SAVE = '''# 2) 저장 폴더  (그냥 ▶ 실행만)
# 결과를 서버의 홈 폴더 아래 "뉴스작업" 에 저장합니다.
# 중간에 끊겨도 다시 실행하면 이어서 합니다.
nc.setup("뉴스작업")'''

COLAB_RESULT = """## 결과물
저장 폴더(구글드라이브 `MyDrive/뉴스작업`)에 생깁니다.

- **`bodies_<검색어>.csv`** ← 더블클릭하면 엑셀로 열립니다. (제목·날짜·언론사·기자·본문)
- `articles_<검색어>.csv`, `bodies_<검색어>.jsonl` — 중간 작업 파일

### 참고
- **1단계에서 어느 날이 10건만 모이고 멈춘 것 같으면**, 그 셀을 다시 ▶ 실행하세요. (네이버가 잠깐 막은 것이라 다시 하면 채워집니다.)
- 본문(`body`) 칸이 비거나 기자(`reporter`)가 빈 기사는 일부 있을 수 있습니다. (속보·유료기사 등 원래 본문이 없는 경우)"""

SERVER_RESULT = """## 결과물
서버 홈 폴더의 `뉴스작업` 안에 생깁니다. **왼쪽 파일 탐색기**에서 보입니다.

- **`bodies_<검색어>.csv`** ← 결과 표 (제목·날짜·언론사·기자·본문)
- `articles_<검색어>.csv`, `bodies_<검색어>.jsonl` — 중간 작업 파일

> **내 PC로 가져오기**: 왼쪽 파일 탐색기에서 `bodies_<검색어>.csv` 를 **오른쪽 클릭 → Download**.

### 참고
- **1단계에서 어느 날이 10건만 모이고 멈춘 것 같으면**, 그 셀을 다시 ▶ 실행하세요. (네이버가 잠깐 막은 것이라 다시 하면 채워집니다.)
- 본문(`body`) 칸이 비거나 기자(`reporter`)가 빈 기사는 일부 있을 수 있습니다. (속보·유료기사 등 원래 본문이 없는 경우)"""


# ── 공통 코드 셀 ────────────────────────────────────────────────────
PREP = f'''# 1) 준비  (그냥 ▶ 실행만)
import os, sys, subprocess

REPO = "{REPO}"

def _sh(c):
    r = subprocess.run(c, shell=True, capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")

def _have_pkg():
    here = os.getcwd()
    for up in [here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))]:
        if up and os.path.isdir(os.path.join(up, "newscrawler")):
            if up not in sys.path: sys.path.insert(0, up)
            return True
    try:
        import newscrawler; return True
    except Exception:
        return False

if not _have_pkg():                     # 코드가 없으면 내려받기
    name = REPO.split("/")[-1]
    print(_sh(f"git clone -q https://github.com/{{REPO}} 2>/dev/null || (cd {{name}} && git pull -q)"))
    if os.path.isdir(name): sys.path.insert(0, name)

print(_sh("pip -q install requests lxml"))
from newscrawler import pipeline as nc
print("준비완료")'''

SETTING_MD = """## 설정 — 여기만 직접 고치세요
따옴표 `" "` **안의 값만** 본인 것으로 바꾸고 ▶ 실행하세요."""

SETTING = '''# 3) 설정
KEYWORD = "고려아연"      # ← 검색어
START   = "2024-09-01"   # ← 시작일 (YYYY-MM-DD)
END     = "2024-09-30"   # ← 종료일 (YYYY-MM-DD)
print(f"검색어 {KEYWORD} | {START} ~ {END}")'''

STEP1_MD = """## 1단계 — 기사 모으기
기간이 길수록 오래 걸립니다. 결과: `articles_<검색어>.csv` (기사 목록)"""
STEP1 = '''nc.collect(KEYWORD, START, END)'''

STEP2_MD = """## 2단계 — 본문 채우기
모은 기사들의 제목·날짜·언론사·기자·본문을 채웁니다. 결과: `bodies_<검색어>.jsonl`"""
STEP2 = '''nc.extract_bodies(KEYWORD, workers=6)'''

STEP3_MD = """## 3단계 — 결과 정리 (표/CSV 만들기)"""
STEP3 = '''nc.summary(KEYWORD)
nc.to_csv(KEYWORD)'''


def build(out, intro, save_cell, result):
    nb = nbf.v4.new_notebook(); cells = []
    md = lambda s: cells.append(nbf.v4.new_markdown_cell(s))
    co = lambda s: cells.append(nbf.v4.new_code_cell(s))
    md(intro)
    co(PREP)
    co(save_cell)
    md(SETTING_MD); co(SETTING)
    md(STEP1_MD);   co(STEP1)
    md(STEP2_MD);   co(STEP2)
    md(STEP3_MD);   co(STEP3)
    md(result)
    nb["cells"] = cells
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3"}
    nbf.write(nb, out)
    print("생성:", out, f"({len(cells)} cells)")


if __name__ == "__main__":
    os.makedirs("notebooks", exist_ok=True)
    build(os.path.join("notebooks", "뉴스크롤러.ipynb"),      COLAB_INTRO,  COLAB_SAVE,  COLAB_RESULT)
    build(os.path.join("notebooks", "뉴스크롤러_서버.ipynb"),  SERVER_INTRO, SERVER_SAVE, SERVER_RESULT)
