# 포항 외국인 주민 생활 인프라 격차 진단·정책생성 시스템

**Infrastructure Gap Atlas & AI Policy Co-pilot**

공개 데이터로 포항시 행정동별 외국인 주민의 생활 인프라 격차를 점수로 계산하고, 행정동별 정책 리포트까지 자동으로 만들어주는 프로젝트다.


---

## 뭘 하는 프로젝트인가

공공데이터포털·KOSIS·여가부 결과보고서에서 받은 데이터로 행정동별 격차 점수 히트맵을 그리고, LLM이 행정동별 정책 처방 카드를 써준다. 크게 두 단계다 (원래 계획한 NLP 단계는 MDIS 포기로 빠짐 — `reports/m4_nlp_substitute.md`).

1. **2SFCA 공간분석** — 수요·공급·접근성 결합해서 격차 점수 계산
2. **LLM 리포트** — 격차 점수 + 전국 통계 배경근거 → 행정동별 정책 처방 (`reports/m5_policy_llm.md`)

임계 거리, 가중치 같은 값은 코드에 안 박아넣고 전부 `config/` YAML로 뺐다. 포항 한정이 아니라 다른 도시에도 재사용할 수 있게 하고 싶어서.

---

## 지금 상태

| 마일스톤 | 상태 |
|---|---|
| M0. 데이터 확보 가능한지 확인 | 조건부 통과 |
| M1. 수집·정제 파이프라인 | 완료 |
| M2. 2SFCA 접근성 프로토타입 | 완료 (의료 단일 지수로 확정) |
| M3. 격차 점수 | 완료 (가중치 0.5/0.5로 GM 검수·확정, 2026-09-22) |
| M4. NLP 수요 신호 추출 | 대체 완료 — MDIS 포기, 경상북도(포항 속한 1권역)·여가부(전국) 결과보고서 수치를 LLM 정책 카드의 배경 근거로 주입(`reports/m4_nlp_substitute.md`). 행정동별 차등 신호는 아님 |
| M5. 시각화 + LLM 리포트 | 완료 — 히트맵·랭킹·29개 정책 카드를 `outputs/dashboard.html` 통합 대시보드 하나로 묶음(`reports/m5_policy_llm.md`). 카드 내용은 사람 검수 권장 |
| LLM 분석 보고서 | 완료 — `--stage report`가 파이프라인 산출물로 `outputs/analysis_report.md/.pdf`를 자동 생성. 본문만 LLM이 쓰고, 수치·구 소속은 코드로 대조, 사실 주장은 LLM으로 재대조, 남은 지적은 "검수 필요" 상자로 표시. 표·지도는 코드 생성 |

몇 가지 결정한 것들:
- 상가정보 API에 금융업 데이터가 없어서 M2는 "의료" 단일 지수로만 간다 (2026-08-26)
- 2SFCA는 임계거리에 따라 순위가 꽤 흔들려서, 값 하나로 결론 안 내리고 여러 임계값으로 돌려본다
- 그래도 격차 상위권(구룡포읍·호미곶면·장기면·대송면)은 임계거리 바꿔도 계속 상위권으로 나옴
- MDIS 다문화가족실태조사 원자료는 원격접근서비스(비용 발생, 로컬 다운로드 불가)라 포기 — 여가부 결과보고서(2차자료)로 대체 확보함. 연구진도 "시도별 분석은 시도 안 함"이라 명시할 만큼 지역 분해가 안 되는 자료라, 행정동별 수치가 아니라 정책 리포트용 전국 배경 근거로만 씀 (2026-09-21)
- 격차 점수 가중치는 GM 검수 결과 0.5/0.5(중립값)로 확정 — 상위 4개 동은 수요 가중치 0.4 이상이면 임계거리 1/3/5km 어디서든 5위 안에 남아서 리스크가 낮다고 판단 (2026-09-22; 접근성에 70% 이상 무게를 두면 일부 밀려남 — `reports/m6_validation.md`)

자세한 근거는 `reports/` 안에 마일스톤별로 정리해뒀고, 다 합친 최종본은 `reports/research_report.md`(`.pdf`가 제출본).

---

## 데이터

크롤링·비공개 API 안 쓰고 전부 공개 파일만 쓴다.

| 데이터 | 출처 | 확보 |
|---|---|---|
| 외국인주민현황 | 행안부 / KOSIS | ✅ |
| 주민등록인구 | 행안부 / KOSIS | ✅ |
| 행정동 경계 | 통계청 SGIS | ✅ |
| 다문화가족실태조사 | MDIS 대신 여가부(전국)·경상북도(포항 속한 1권역) 결과보고서 2건 | ✅ (행정동 분해는 없음, 경북 조사는 포항 포함 권역 단위까지 — `reports/m4_nlp_substitute.md`) |
| 의료기관 현황 | 심평원 | ✅ |
| 상가정보 | 소상공인시장진흥공단 | ✅ (금융업 제외) |

원본 데이터는 `data/raw/`에 두는데 gitignore돼있고, 대신 `data/MANIFEST.yaml`에 출처·다운로드 날짜·해시 적어둔다.

---

## 구조

```
pohang-infra-gap/
├── config/       # 임계거리, 가중치, LLM 프롬프트
├── data/
│   ├── raw/      # gitignored
│   ├── interim/
│   └── processed/
├── src/
│   ├── ingest/      
│   ├── preprocess/ 
│   ├── access/      # 2SFCA 
│   ├── gap/         # 격차 점수
│   ├── policy/      # LLM 정책 리포트 (nlp 없이 gap_score + 전국 통계 배경근거로 생성)
│   └── viz/         # 히트맵
├── scripts/      # run_pipeline.py 등
├── outputs/      # 결과물
└── tests/
```


---

## 실행

```bash
uv sync                                              # 환경 설치

uv run python scripts/m0_audit.py --config config/pipeline.yaml     # 데이터 확인
uv run python scripts/run_pipeline.py --stage all                   # 전체 실행 (정책 카드 29개도 API로 재생성·덮어씀)
uv run python scripts/run_pipeline.py --stage all --skip-llm        # LLM 호출(policy·report) 없이 나머지만 — 기존 카드·보고서 보존
uv run python scripts/run_pipeline.py --stage access                # 특정 단계만
uv run python scripts/run_pipeline.py --stage report                # LLM 분석 보고서 (outputs/analysis_report.pdf)
```

의존성은 `pyproject.toml`이 기준이다(`geopandas`, `shapely`, `pyproj`, `scipy`, `pandas`, `pyarrow`, `plotly`, `kaleido`, `openai`(NVIDIA build 호출용), `markdown` 등). 파이썬 3.11 이상.

테스트는 `uv run pytest`. 시설 데이터를 만드는 테스트(`tests/test_facilities.py`)는 심평원 xlsx 로딩과 상가정보 API 호출이 있어 느리다(수 분). 빠른 확인은 `uv run pytest --ignore=tests/test_facilities.py`.

---

## 팀

| 담당 | 전공 |
|---|---|
| 데이터 파이프라인, 공간데이터, 시각화 | 황예찬|
| 2SFCA, NLP, LLM, 정책 분류 | 황예찬|
| 문제 정의, 가중치 설계, 정책 타당성 검증 | 최서진 |

---

## 메모 (삽질했던 것들)

- 행정동 코드는 기관마다 다르고 연도마다 바뀐다 — 기준 연도 고정하고 매핑 테이블 따로 관리 중 (`data/processed/adm_code_map.csv`)
- 좌표계도 파일마다 다름 (상가정보 EPSG:5181, 심평원은 WGS84 도분초...) — `crs.py`에서 통일하고 나서만 거리 계산
- 공공데이터 인코딩(CP949)·컬럼명·결측 표기가 제각각이라 정제에 생각보다 시간 많이 씀
- LLM 리포트는 근거 데이터를 프롬프트에 직접 넣고 출력에도 근거 필드 강제 — 수치 지어내면 안 되니까

---
