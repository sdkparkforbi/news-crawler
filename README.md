# news-crawler

네이버 뉴스를 **검색어 단위로 일별 수집**하고 **본문·메타를 추출**하는 크롤러.
Google Colab 과 미들턴 서버 JupyterLab(로컬 포함) **양쪽에서 그대로** 동작합니다.

```
검색어 → ① 일별 수집(more-API 커서) → ② 본문/기자/발행일 추출 → ③ 요약/CSV
```

## 핵심: 왜 more-API 인가 (과소수집 버그 수정)

네이버 뉴스 검색의 무한스크롤은 HTML `&start=` 페이징이 아니라
`s.search.naver.com/.../api/tab/more` **커서 API** 로 더 불러옵니다.
구버전 `&start=` 방식은 하루 쿼리에서 ~120건에서 막혀(중복만 반복) **약 66%를 놓쳤습니다.**

| 2024-09-13 '고려아연' | 구버전 `&start=` | 본 크롤러 more-API |
|---|---|---|
| 수집 건수 | 120건 | **408건** |
| 언론사 수 | 53곳 | **147곳** |

검색 결과 HTML에 박힌 첫 more URL을 따라가며 `{collection:[{html}], url:<다음커서>}` 를
끝까지 페이징합니다(네이버 최대 2,000건/쿼리). 지속 수집 시 발생하는 **스로틀(하루 10건에서
잘림)** 은 새 세션 + 백오프로 자동 재시도해 복구합니다.

## 빠른 시작 (노트북)

### A. Google Colab
1. Colab에서 `파일 ▸ 노트북 열기 ▸ GitHub` → `sdkparkforbi/news-crawler` 검색 → `notebooks/뉴스크롤러.ipynb` 열기
   (또는 주소창에 `https://colab.research.google.com/github/sdkparkforbi/news-crawler/blob/main/notebooks/뉴스크롤러.ipynb`)
2. 위에서부터 Run — 1번 셀이 코드를 내려받고, 구글드라이브에 결과를 저장합니다

### B. 미들턴 JupyterLab / 로컬
```bash
git clone https://github.com/sdkparkforbi/news-crawler.git
cd news-crawler
pip install -r requirements.txt
jupyter lab    # notebooks/뉴스크롤러.ipynb 열고 Run
```
노트북 1번 셀이 로컬 `newscrawler` 패키지를 자동 인식합니다(클론·토큰 불필요).

## 빠른 시작 (코드/CLI)

```python
from newscrawler import pipeline as nc
nc.setup("뉴스작업")                              # 작업폴더(체크포인트 영구저장)
nc.collect("고려아연", "2024-09-01", "2024-09-30") # → articles_고려아연.csv
nc.extract_bodies("고려아연", workers=6)           # → bodies_고려아연.jsonl
nc.summary("고려아연"); nc.to_csv("고려아연")       # 요약 + 엑셀용 CSV
```

CLI:
```bash
KW=고려아연 SD=2024-09-01 ED=2024-09-30 python -m newscrawler.collect
python -m newscrawler.extract --batch articles_고려아연.csv --out bodies_고려아연.jsonl --workers 6
```

## 산출물 (모두 현재 작업폴더)
| 파일 | 내용 |
|---|---|
| `articles_<KW>.csv` | 수집 인벤토리: 날짜·언론사·제목·원문URL·네이버URL |
| `daily_counts_<KW>.csv` | 일별 건수(재개 체크포인트) |
| `bodies_<KW>.jsonl` | 본문/메타 추출 원본 |
| `bodies_<KW>.csv` | 엑셀용 정리본(utf-8-sig) |

추출 필드: `title, published_at, modified_at, press, reporter, body, summary, image,
url, naver_url, domain, source, chars, ok`. 발행시각은 `YYYY-MM-DD HH:MM` 정규화,
`ok`는 본문(≥150자) 성공 여부.

## 구성
```
news-crawler/
├── newscrawler/
│   ├── collect.py     네이버 일별 수집 (more-API 커서 · 스로틀 복구)
│   ├── extract.py     URL → 본문/메타 (네이버 미러 우선 + per-press + readability)
│   ├── discover.py    미추출 매체 자가학습(기자/날짜 위치) → learned_rules.json
│   ├── backfill.py    빠진 필드를 반대 소스(원문↔미러) 재크롤로 보강
│   └── pipeline.py    환경(Colab/JupyterLab) 무관 래퍼 + 요약/CSV
├── notebooks/뉴스크롤러.ipynb   양쪽 환경 공용 노트북
├── build_notebook.py            노트북 생성 스크립트
└── requirements.txt
```

## 추출 품질 보강 (선택)
본문이 비거나 기자가 안 잡힌 매체가 있으면:
```bash
python -m newscrawler.discover --from-jsonl bodies_<KW>.jsonl --min-count 1
python -m newscrawler.extract  --batch articles_<KW>.csv --out bodies_<KW>.jsonl
python -m newscrawler.backfill bodies_<KW>.jsonl     # 빠진 필드 교차보강 + gaps_<KW>.csv
```

## 주의
- 페이지네이션은 **순차(referer 체인)** 만 동작 — 동시 샤딩은 즉시 스로틀됩니다.
- 본문추출(extract)은 여러 도메인에 분산되므로 `workers` 병렬이 안전합니다.
- 수집/추출에는 비밀키가 필요 없습니다(공개 repo).

## License
MIT
