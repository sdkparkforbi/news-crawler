# -*- coding: utf-8 -*-
"""분석 노트북 2종 생성 (크롤러 다음 단계):
    notebooks/뉴스분석.ipynb        (Google Colab 용)
    notebooks/뉴스분석_서버.ipynb    (서버 JupyterLab 용)
코드 셀은 동일하고 안내/저장 셀만 환경에 맞게 다릅니다.  생성:  python build_analysis.py
파이프라인: bodies_검색어.csv → 필터링 → 형태소(명사·TFIDF) → 임베딩 4가지 → 지표큐브 → 시각화 → 통계검정 → 버스트
"""
import json, os

# ───────────────────────── 환경별 안내/저장 ─────────────────────────
COLAB_INTRO = r"""# 뉴스 동조화 분석  (Google Colab용)

크롤러가 만든 **`bodies_검색어.csv`** 를 받아, 매체 간 보도 동조화를
**필터링 → 형태소(명사) → 임베딩 4가지 → 시각화 → 통계검정** 순으로 분석합니다.

## 사용법 (3가지만)
1. **위에서부터 셀을 ▶ 차례로 실행**합니다.
2. **`설정` 셀**에서 검색어·사건일·임베딩과 API 키만 입력합니다.
3. 끝나면 구글드라이브 **`내 드라이브/뉴스작업`** 폴더에 그림(PNG)·지표(CSV)가 생깁니다.

> 임베딩은 bge-m3(=`MIDDLETON_API_KEY`)와 text-embedding-3-large(=`OPENAI_API_KEY`)를 씁니다.
> 키가 없는 임베딩은 자동으로 건너뜁니다. 결과는 캐시되어 다시 실행하면 이어서 합니다."""

SERVER_INTRO = r"""# 뉴스 동조화 분석  (서버 JupyterLab용)

크롤러가 만든 **`bodies_검색어.csv`** 를 받아, 매체 간 보도 동조화를
**필터링 → 형태소(명사) → 임베딩 4가지 → 시각화 → 통계검정** 순으로 분석합니다.

## 사용법 (3가지만)
1. **위에서부터 셀을 ▶ 차례로 실행**합니다.
2. **`설정` 셀**에서 검색어·사건일·임베딩과 API 키만 입력합니다.
3. 끝나면 서버 홈 **`~/뉴스작업`** 폴더에 그림(PNG)·지표(CSV)가 생깁니다.

> 임베딩은 bge-m3(=`MIDDLETON_API_KEY`)와 text-embedding-3-large(=`OPENAI_API_KEY`)를 씁니다.
> 키가 없는 임베딩은 자동으로 건너뜁니다. 결과는 캐시되어 다시 실행하면 이어서 합니다."""

COLAB_SAVE = r"""#@title 2) 저장 폴더 (그냥 ▶ 실행) — 구글드라이브 팝업이 뜨면 "허용"
from google.colab import drive
drive.mount('/content/drive')
import os
WORKDIR = '/content/drive/MyDrive/뉴스작업'
os.makedirs(WORKDIR, exist_ok=True); os.chdir(WORKDIR)
print('작업폴더:', WORKDIR)
print('이 폴더에 크롤러가 만든  bodies_검색어.csv  가 있어야 합니다.')"""

SERVER_SAVE = r"""# 2) 저장 폴더 (그냥 ▶ 실행)
import os
WORKDIR = os.path.join(os.path.expanduser('~'), '뉴스작업')
os.makedirs(WORKDIR, exist_ok=True); os.chdir(WORKDIR)
print('작업폴더:', WORKDIR)
print('이 폴더에 크롤러가 만든  bodies_검색어.csv  가 있어야 합니다.')"""

# ───────────────────────── 공통 코드 셀 ─────────────────────────
C_INSTALL = r"""# 1) 라이브러리 설치 (1~2분, 한 번만)
import sys, subprocess
pkgs = "kiwipiepy scikit-learn matplotlib koreanize-matplotlib scipy statsmodels openai requests pandas numpy".split()
subprocess.run([sys.executable,"-m","pip","-q","install",*pkgs])
print("설치 완료")"""

C_CONFIG = r"""# 3) 설정 — 여기만 바꾸세요
검색어     = "고려아연"          # 크롤러에서 쓴 검색어 (bodies_검색어.csv 와 같아야 함)
사건일     = "2024-09-13"        # 시간 원점 (이 날을 0으로 사전/사후를 가름)
이차사건   = "2025-01-23"        # 후속 핵심 사건일(없으면 ""). 분석기간=원점-1년 ~ 이차사건+1년
사용임베딩  = ["bge_tb", "bge_noun", "oai_tb", "oai_noun"]
#   ↑ 4가지 전부. 일부만 쓰려면 줄이세요(키 없는 건 자동 건너뜀).
#   bge_tb=bge-m3·제목본문 · bge_noun=bge-m3·명사 · oai_tb=3large·제목본문 · oai_noun=3large·명사
# ===========================================================
import getpass
KW = 검색어.strip(); EVENT_DATE = 사건일.strip(); EVENT2 = 이차사건.strip()
KEY_MID = ""; OAI = None
if any(m.startswith("bge") for m in 사용임베딩):
    KEY_MID = getpass.getpass("MIDDLETON_API_KEY (bge-m3용, 없으면 그냥 Enter): ").strip()
if any(m.startswith("oai") for m in 사용임베딩):
    _k = getpass.getpass("OPENAI_API_KEY (3large용, 없으면 그냥 Enter): ").strip()
    if _k:
        from openai import OpenAI
        OAI = OpenAI(api_key=_k); del _k
print("검색어:", KW, "· 사건일:", EVENT_DATE)
print("bge-m3 키:", "있음" if KEY_MID else "없음", "· OpenAI 키:", "있음" if OAI else "없음")"""

C_LOAD = r"""# 4) 데이터 로드 & 필터링
import pandas as pd, numpy as np, os
path = f"bodies_{KW}.csv"
assert os.path.exists(path), f"{path} 가 작업폴더에 없습니다. 크롤러를 먼저 돌리세요."
df = pd.read_csv(path, encoding="utf-8-sig")
for c in ["title","body","press","reporter","published_at"]:
    if c not in df.columns: df[c] = ""
for c in ["title","body","press","reporter"]:
    df[c] = df[c].fillna("").astype(str)
df["dt"] = pd.to_datetime(df["published_at"], errors="coerce")
n0 = len(df)
df = df[df["body"].str.len() >= 150]
df = df[df["dt"].notna()]
df = df.drop_duplicates(subset=["press","title","published_at"]).reset_index(drop=True)
EV = pd.Timestamp(EVENT_DATE); EV2 = pd.Timestamp(EVENT2) if EVENT2 else None
WIN0 = EV - pd.DateOffset(years=1); WIN1 = (EV2 if EV2 is not None else EV) + pd.DateOffset(years=1)
df = df[(df.dt>=WIN0)&(df.dt<=WIN1)].reset_index(drop=True)   # 분석기간으로 한정(인덱스=임베딩 순서)
print(f"분석 기간 {WIN0.date()} ~ {WIN1.date()} · 원점 {EV.date()}" + (f" · 2차 {EV2.date()}" if EV2 is not None else ""))
print(f"원본 {n0} → 필터 후 {len(df)} 행 (본문 150자↑·시각유효·중복제거·기간한정)")
print(f"기간 {df.dt.min().date()} ~ {df.dt.max().date()} · 매체 {df.press.nunique()}곳 · 기자 {df.reporter.nunique()}")
print(f"사전(사건 전) {int((df.dt<EV).sum())}건 · 사후 {int((df.dt>=EV).sum())}건")"""

C_NOUN = r"""# 5) 형태소 분석 — 명사 추출 + TF-IDF(0.05) (명사 임베딩 쓸 때만)
import json
NOUN_CACHE = f"_nouns_{KW}.jsonl"
nouns_kept = None
if any("noun" in m for m in 사용임베딩):
    if os.path.exists(NOUN_CACHE):
        k = [json.loads(l)["k"] for l in open(NOUN_CACHE, encoding="utf-8")]
        if len(k) == len(df): nouns_kept = k; print("명사 캐시 사용:", NOUN_CACHE)
    if nouns_kept is None:
        from kiwipiepy import Kiwi
        from sklearn.feature_extraction.text import TfidfVectorizer
        kiwi = Kiwi()
        STOP = set("기자 뉴스 보도 기사 관련 사진 연합뉴스 제공 무단 배포 전재 저작권 오늘 지난 이날 당시 경우 가운데 통해 위해 대해".split())
        bodies = [str(b)[:3000] for b in df["body"]]
        print("Kiwi 명사추출…", len(bodies), "건 (수 분 걸릴 수 있음)")
        toks_all = []
        for toks in kiwi.tokenize(bodies):
            toks_all.append([t.form for t in toks if t.tag in ("NNG","NNP") and len(t.form)>=2 and t.form not in STOP])
        vec = TfidfVectorizer(token_pattern=r"(?u)\b\w\w+\b", min_df=2)
        X = vec.fit_transform([" ".join(t) for t in toks_all]).tocsr()
        inv = {c:t for t,c in vec.vocabulary_.items()}
        nouns_kept = []
        with open(NOUN_CACHE,"w",encoding="utf-8") as f:
            for i in range(len(df)):
                row = X.getrow(i); tf = {inv[c]:w for c,w in zip(row.indices,row.data)}
                kept = [w for w in toks_all[i] if tf.get(w,0.0) >= 0.05]
                if not kept: kept = [w for w,_ in sorted(tf.items(), key=lambda x:-x[1])[:3]]
                nouns_kept.append(kept); f.write(json.dumps({"k":kept}, ensure_ascii=False)+"\n")
        print("명사 추출 완료 · 평균", round(np.mean([len(k) for k in nouns_kept]),1), "개/문서 →", NOUN_CACHE)
else:
    print("명사 임베딩 미사용 → 형태소 분석 건너뜀")"""

C_EMBED = r"""# 6) 임베딩 4가지 (캐시 emb_검색어_*.npy)
import requests, time
METHODS = {
  "bge_tb":  {"name":"① bge-m3·제목본문","kind":"bge","inp":"tb"},
  "bge_noun":{"name":"② bge-m3·명사",    "kind":"bge","inp":"noun"},
  "oai_tb":  {"name":"③ 3large·제목본문","kind":"oai","inp":"tb"},
  "oai_noun":{"name":"④ 3large·명사",    "kind":"oai","inp":"noun"},
}
def texts_for(inp):
    if inp == "tb":
        return [ (str(t)+" "+str(b)[:3000]).strip() or "내용없음" for t,b in zip(df["title"],df["body"]) ]
    return [ (" ".join(k)).strip() or "내용없음" for k in nouns_kept ]
def embed_bge(texts):
    hdr = {"Authorization":"Bearer "+KEY_MID, "Content-Type":"application/json"}; out=[]; B=48
    for i in range(0,len(texts),B):
        ok=None
        for _ in range(4):
            try:
                r = requests.post("https://middleton.p-e.kr/api/embeddings", headers=hdr,
                                  json={"model":"bge-m3:latest","input":texts[i:i+B]}, timeout=120)
                if r.status_code==200: ok=[e["embedding"] for e in r.json()["data"]]; break
            except Exception: pass
            time.sleep(3)
        if ok is None: raise RuntimeError("bge-m3 호출 실패 — 키/서버를 확인하세요")
        out.extend(ok)
        if (i//B)%10==0: print("  bge", min(i+B,len(texts)),"/",len(texts))
    return out
def embed_oai(texts):
    out=[]; B=256
    for i in range(0,len(texts),B):
        ok=None
        for _ in range(5):
            try: ok=[d.embedding for d in OAI.embeddings.create(model="text-embedding-3-large", input=texts[i:i+B]).data]; break
            except Exception as e: print("  재시도", str(e)[:50]); time.sleep(4)
        if ok is None: raise RuntimeError("OpenAI 호출 실패")
        out.extend(ok)
        if (i//B)%4==0: print("  oai", min(i+B,len(texts)),"/",len(texts))
    return out
def norm16(vs):
    A=np.asarray(vs,dtype=np.float32); A/=np.linalg.norm(A,axis=1,keepdims=True)+1e-9; return A.astype(np.float16)
EMB = {}
for key in 사용임베딩:
    m = METHODS[key]
    if m["kind"]=="bge" and not KEY_MID: print("건너뜀(키 없음):", m["name"]); continue
    if m["kind"]=="oai" and OAI is None: print("건너뜀(키 없음):", m["name"]); continue
    cache = f"emb_{KW}_{key}.npy"
    if os.path.exists(cache):
        V=np.load(cache)
        if len(V)==len(df): EMB[key]=V; print("캐시:", m["name"], V.shape); continue
    print("임베딩 시작:", m["name"], f"({len(df)}건)")
    vs = embed_bge(texts_for(m["inp"])) if m["kind"]=="bge" else embed_oai(texts_for(m["inp"]))
    V = norm16(vs); np.save(cache, V); EMB[key]=V; print("완료:", m["name"], V.shape)
assert EMB, "사용 가능한 임베딩이 없습니다(키를 확인하세요)."
print("사용 임베딩:", [METHODS[k]["name"] for k in EMB])"""

C_CUBE = r"""# 7) 지표 큐브 — 4임베딩 × (1·5·10·20일) × (기사·기자매체·매체) × {수·평균·≥0.8/0.9 절대·상대}
WINS=[1,5,10,20]; UNITS=["기사","기자-매체","매체"]
def cents(V, sub, unit):
    if unit=="기사":
        idx = sub.index.values
        if len(idx) > 7000:
            idx = np.sort(np.random.default_rng(0).choice(idx, 7000, replace=False))
        Vd = V[idx].astype(np.float32); Vd/=np.linalg.norm(Vd,axis=1,keepdims=True)+1e-9; return Vd
    key = (sub.press+"|"+sub.reporter) if unit=="기자-매체" else sub.press
    sub = sub.assign(_k=key.values); sub = sub[sub._k.str.replace("|","",regex=False).str.len()>0]
    cs=[]
    for _,g in sub.groupby("_k"):
        Vd = V[g.index.values].astype(np.float32); Vd/=np.linalg.norm(Vd,axis=1,keepdims=True)+1e-9
        cc = Vd.mean(0); nn = np.linalg.norm(cc)
        if nn>0: cs.append(cc/nn)
    return np.vstack(cs) if cs else np.zeros((0,V.shape[1]), np.float32)
rows=[]; base=df.copy(); base["_day"]=(base.dt.dt.normalize()-EV).dt.days
for key in EMB:
    V=EMB[key]; name=METHODS[key]["name"]
    for Wd in WINS:
        base["_w"]=np.floor(base._day/Wd).astype(int)
        for w,g in base.groupby("_w"):
            mid = EV + pd.Timedelta(days=int(w*Wd + Wd/2))
            for u in UNITS:
                C=cents(V,g,u); n=len(C); rec=[name,Wd,u,mid,n,np.nan,0,0,np.nan,np.nan]
                if n>=2:
                    S=np.clip((C@C.T)[np.triu_indices(n,1)],0,1); npair=len(S)
                    rec[5]=float(S.mean()); rec[6]=int((S>=0.8).sum()); rec[7]=int((S>=0.9).sum())
                    rec[8]=rec[6]/npair*100; rec[9]=rec[7]/npair*100
                rows.append(rec)
cube=pd.DataFrame(rows,columns=["method","win","unit","mid","count","mean","abs80","abs90","rel80","rel90"])
cube.to_csv(f"indicators_{KW}.csv",index=False,encoding="utf-8-sig")
print("지표 큐브 저장 →", f"indicators_{KW}.csv", cube.shape)"""

C_FIG = r"""# 8) 시각화 — 지표별 템플릿(행:임베딩 × 열:시간창, 칸마다 3단위)
import matplotlib, matplotlib.pyplot as plt
try: import koreanize_matplotlib
except Exception: pass
RUN=[METHODS[k]["name"] for k in EMB]
UCOL=[("기사","#2c5da8"),("기자-매체","#E0954F"),("매체","#C23A2B")]
WLAB=[(1,"1일"),(5,"5일"),(10,"10일"),(20,"20일")]
def tpl(metric,title,fname,logy=False):
    R=len(RUN); fig,axes=plt.subplots(R,4,figsize=(19,3.3*R+1),squeeze=False,sharex=True)
    for ri,name in enumerate(RUN):
        for ci,(W,wl) in enumerate(WLAB):
            ax=axes[ri][ci]
            for u,co in UCOL:
                T=cube[(cube.method==name)&(cube.win==W)&(cube.unit==u)].sort_values("mid")
                ax.plot(T["mid"],T[metric],lw=0.9,color=co,marker="o",ms=3.0,mfc=co,mec="white",mew=0.5,label=u)
            if logy: ax.set_yscale("symlog")
            ax.axvline(EV,color="black",ls="--",lw=1.0)
            if EV2 is not None: ax.axvline(EV2,color="#7a7a7a",ls=":",lw=1.0)
            ax.grid(alpha=0.13)
            if ri==0: ax.set_title(wl+" 창",fontsize=12)
            if ci==0: ax.set_ylabel(name,fontsize=9)
    axes[0][-1].legend(fontsize=8,loc="upper right")
    fig.suptitle(title,fontsize=14); plt.tight_layout(rect=[0,0,1,0.99])
    plt.savefig(fname,dpi=110); plt.show(); print("저장:",fname)
tpl("count", f"{KW} — ① 기사·단위 수", f"fig_count_{KW}.png")
tpl("mean",  f"{KW} — ② 평균 코사인유사도", f"fig_mean_{KW}.png")
tpl("abs80", f"{KW} — ③ ≥0.8 절대빈도(symlog)", f"fig_abs80_{KW}.png", True)
tpl("abs90", f"{KW} — ③ ≥0.9 절대빈도(symlog)", f"fig_abs90_{KW}.png", True)
tpl("rel80", f"{KW} — ④ ≥0.8 상대빈도(%)", f"fig_rel80_{KW}.png")
tpl("rel90", f"{KW} — ④ ≥0.9 상대빈도(%)", f"fig_rel90_{KW}.png")"""

C_TEST = r"""# 9) 통계 검정 — 매체 단위 사전→사후 (단측 Mann–Whitney) + CUSUM 변화점
from scipy.stats import mannwhitneyu
IND=[("count","매체 수"),("mean","평균 유사도"),("abs80","≥0.8 절대"),("abs90","≥0.9 절대"),("rel80","≥0.8 상대%"),("rel90","≥0.9 상대%")]
def pre_post(name,ind,unit="매체",W=20):
    T=cube[(cube.method==name)&(cube.win==W)&(cube.unit==unit)].dropna(subset=[ind])
    pre=T[T.mid<EV][ind].values; post=T[T.mid>=EV][ind].values
    if len(pre)<3 or len(post)<3: return None
    return pre.mean(), post.mean(), mannwhitneyu(post,pre,alternative="greater").pvalue, len(pre), len(post)
print("=== 매체 단위 사전→사후 (20일창, 단측 Mann–Whitney) ===")
trow=[]
for ind,lbl in IND:
    ps=[]; rep=None
    for name in RUN:
        r=pre_post(name,ind)
        if r: ps.append(r[2]); rep = rep or r
    if not ps: continue
    print(f"{lbl:9s} | {rep[0]:.3g} -> {rep[1]:.3g} | p {min(ps):.1e} ~ {max(ps):.1e} | n {rep[3]}/{rep[4]}")
    trow.append([lbl, rep[0], rep[1], min(ps), max(ps)])
pd.DataFrame(trow,columns=["지표","사전","사후","p_min","p_max"]).to_csv(f"tests_{KW}.csv",index=False,encoding="utf-8-sig")
fig,axes=plt.subplots(2,3,figsize=(16,9))
for k,(ind,lbl) in enumerate(IND):
    ax=axes[k//3][k%3]; x=np.arange(len(RUN)); w=0.38; pre=[];post=[];ps=[]
    for name in RUN:
        r=pre_post(name,ind)
        pre.append(r[0] if r else 0); post.append(r[1] if r else 0); ps.append(r[2] if r else 1)
    ax.bar(x-w/2,pre,w,color="#9bb0c9",label="사전"); ax.bar(x+w/2,post,w,color="#C23A2B",label="사후")
    if ind in ("count","abs80","abs90"): ax.set_yscale("log")
    ax.set_xticks(x); ax.set_xticklabels([str(i+1) for i in range(len(RUN))]); ax.set_title(lbl,fontsize=12); ax.grid(axis="y",alpha=0.15)
    for xi,(b,pp) in enumerate(zip(post,ps)):
        if pp<0.05: ax.text(xi+w/2,b,"***" if pp<1e-3 else "*",ha="center",va="bottom")
    if k==0: ax.legend(fontsize=9)
fig.suptitle(f"{KW} 매체 단위 사전 vs 사후 (임베딩 ①~④ · 단측 Mann–Whitney)",fontsize=13)
plt.tight_layout(rect=[0,0,1,0.97]); plt.savefig(f"fig_tests_{KW}.png",dpi=115); plt.show()

def cusum_detect(name,ind,unit="매체",W=10,K=0.5,H=5):
    T=cube[(cube.method==name)&(cube.win==W)&(cube.unit==unit)].dropna(subset=[ind]).sort_values("mid")
    pre=T[T.mid<EV][ind].values
    if len(pre)<3: return None,[],[]
    mu,sd=pre.mean(),pre.std(ddof=1)
    if sd==0: return None,[],[]
    S=0.0; det=None; xs=[];ys=[]
    for _,r in T.iterrows():
        S=max(0.0,S+(r[ind]-mu)/sd-K); xs.append(r["mid"]); ys.append(S)
        if det is None and S>H: det=r["mid"]
    return det,xs,ys
CI=[("count","매체 수"),("mean","평균 유사도"),("abs90","≥0.9 절대"),("rel90","≥0.9 상대")]
print("\n=== CUSUM 변화점 (매체 단위 10일창, h=5σ) ===")
cz=[]; R=len(RUN); fig,axes=plt.subplots(R,4,figsize=(18,3*R+1),squeeze=False,sharex=True)
for ri,name in enumerate(RUN):
    line=[name]
    for ci,(ind,lbl) in enumerate(CI):
        det,xs,ys=cusum_detect(name,ind); ax=axes[ri][ci]
        ax.plot(xs,ys,color="#2c5da8",lw=1.2); ax.axhline(5,color="#C23A2B",ls="--",lw=1); ax.axvline(EV,color="black",ls="--",lw=1)
        if EV2 is not None: ax.axvline(EV2,color="#7a7a7a",ls=":",lw=1)
        if det is not None: ax.axvline(det,color="#E0954F",ls=":",lw=1.4)
        ax.grid(alpha=0.13)
        if ri==0: ax.set_title(lbl,fontsize=11)
        if ci==0: ax.set_ylabel(name,fontsize=9)
        line.append(det.strftime("%Y-%m-%d") if det is not None else "—")
    cz.append(line)
fig.suptitle(f"{KW} CUSUM 변화점 (매체 단위 10일창)",fontsize=13)
plt.tight_layout(rect=[0,0,1,0.97]); plt.savefig(f"fig_cusum_{KW}.png",dpi=112); plt.show()
cdf=pd.DataFrame(cz,columns=["임베딩","매체 수","평균 유사도","≥0.9 절대","≥0.9 상대"])
cdf.to_csv(f"cusum_{KW}.csv",index=False,encoding="utf-8-sig"); print(cdf.to_string(index=False))"""

C_BURST = r"""# 10) 발행 타이밍 버스트 — 사건일 60분 몰아쓰기(rapid-fire) 무작위 영모형 검정
ev = df[df.dt.dt.normalize()==EV].copy()
print("사건일", EVENT_DATE, "기사:", len(ev), "건")
def peak60(mins):
    t=np.sort(np.asarray(mins,dtype="int64")); best=1; j=0
    for i in range(len(t)):
        while t[i]-t[j]>60: j+=1
        best=max(best,i-j+1)
    return best
def mins_of(s): return s.values.astype("datetime64[m]").astype("int64")
if len(ev)>=10:
    def null_p(mins,obs,iters=2000):
        t=np.asarray(mins,dtype="int64"); lo,hi=int(t.min()),int(t.max())
        if hi<=lo: return 1.0
        rng=np.random.default_rng(0); c=0
        for _ in range(iters):
            if peak60(rng.integers(lo,hi+1,len(t)))>=obs: c+=1
        return (c+1)/(iters+1)
    cand=[]
    for col in ["press","reporter"]:
        for name,g in ev.groupby(col):
            if str(name).strip()=="" or len(g)<4: continue
            cand.append((col,name,len(g),peak60(mins_of(g.dt))))
    cand=sorted(cand,key=lambda x:-x[3])[:12]
    print("\n상위 몰아쓰기 (60분 최대 발행수):")
    out=[]
    for col,name,n,obs in cand:
        p=null_p(mins_of(ev[ev[col]==name].dt),obs)
        flag="***" if p<1e-3 else ("**" if p<1e-2 else ("*" if p<0.05 else ""))
        print(f"  [{col}] {name}: {n}건 중 60분 최대 {obs}건 · p={p:.4f} {flag}")
        out.append([col,name,n,obs,p])
    pd.DataFrame(out,columns=["단위","이름","사건일건수","peak60","p"]).to_csv(f"burst_{KW}.csv",index=False,encoding="utf-8-sig")
    print("\n저장 → burst_"+KW+".csv  (*** p<0.001 = 우연으로 설명 안 되는 몰아쓰기)")
else:
    print("사건일 기사가 적어 버스트 분석을 건너뜁니다.")"""

C_DONE = r"""# 완료 — 결과 파일 목록
import glob
print("=== 작업폴더 결과 파일 ===")
for f in sorted(set(glob.glob(f"*{KW}*.csv")+glob.glob(f"fig_*{KW}*.png")+glob.glob(f"indicators_{KW}.csv"))):
    print("  ", f)
print("\nCSV(지표·검정·CUSUM·버스트)와 PNG(그림)이 위 폴더에 저장됐습니다.")
print("논문/보고서에는 fig_*.png 와 indicators/tests/cusum/burst_*.csv 를 그대로 쓰면 됩니다.")"""

# ───────────────────────── 노트북 조립 ─────────────────────────
def md(s):   return {"cell_type":"markdown","metadata":{},"source":s.splitlines(keepends=True)}
def code(s): return {"cell_type":"code","metadata":{},"execution_count":None,"outputs":[],"source":s.splitlines(keepends=True)}

def build(intro, save):
    cells = [
        md(intro),
        md("## 1단계 — 설치"), code(C_INSTALL),
        md("## 2단계 — 저장 폴더 (크롤러의 bodies_검색어.csv 가 있는 곳)"), code(save),
        md("## 3단계 — 설정 (검색어·사건일·임베딩·API 키)"), code(C_CONFIG),
        md("## 4단계 — 데이터 로드 & 필터링"), code(C_LOAD),
        md("## 5단계 — 형태소 분석 (명사 + TF-IDF)"), code(C_NOUN),
        md("## 6단계 — 임베딩 4가지 (bge-m3 / text-embedding-3-large × 제목본문 / 명사)"), code(C_EMBED),
        md("## 7단계 — 지표 큐브"), code(C_CUBE),
        md("## 8단계 — 시각화 (지표별 템플릿 그림)"), code(C_FIG),
        md("## 9단계 — 통계 검정 (Mann–Whitney + CUSUM)"), code(C_TEST),
        md("## 10단계 — 발행 타이밍 버스트"), code(C_BURST),
        md("## 완료"), code(C_DONE),
    ]
    return {"cells":cells,
            "metadata":{"kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},
                        "language_info":{"name":"python"}},
            "nbformat":4,"nbformat_minor":5}

os.makedirs("notebooks", exist_ok=True)
for fn, intro, save in [("notebooks/뉴스분석.ipynb", COLAB_INTRO, COLAB_SAVE),
                        ("notebooks/뉴스분석_서버.ipynb", SERVER_INTRO, SERVER_SAVE)]:
    with open(fn,"w",encoding="utf-8") as f:
        json.dump(build(intro,save), f, ensure_ascii=False, indent=1)
    print("생성:", fn)

# 코드 셀 문법 검사
import ast
for name,src in [("INSTALL",C_INSTALL),("CONFIG",C_CONFIG),("LOAD",C_LOAD),("NOUN",C_NOUN),
                 ("EMBED",C_EMBED),("CUBE",C_CUBE),("FIG",C_FIG),("TEST",C_TEST),("BURST",C_BURST),
                 ("DONE",C_DONE),("COLAB_SAVE",COLAB_SAVE),("SERVER_SAVE",SERVER_SAVE)]:
    try: ast.parse(src); print("  문법 OK:", name)
    except SyntaxError as e: print("  !! 문법 오류:", name, e)
