# -*- coding: utf-8 -*-
"""newscrawler — 네이버 뉴스 수집 + 본문/메타 추출 (Colab · JupyterLab 공용).

기본 흐름:
    from newscrawler import pipeline as nc
    nc.setup("뉴스작업")            # 작업폴더로 이동(체크포인트 영구저장)
    nc.collect("고려아연", "2024-01-01", "2024-12-31")
    nc.extract_bodies("고려아연", workers=6)
    nc.summary("고려아연")
"""
# 서브모듈은 지연 임포트(eager import 시 `python -m newscrawler.extract` 에서
# RuntimeWarning 발생). 필요할 때 `from newscrawler import pipeline` 처럼 가져온다.
__all__ = ["collect", "extract", "discover", "backfill", "pipeline"]
__version__ = "1.0.0"
