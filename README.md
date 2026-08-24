# 포항 외국인 주민 생활 인프라 격차 진단·정책생성 시스템

**Infrastructure Gap Atlas & AI Policy Co-pilot**

공개 데이터만으로 포항시 행정동별 외국인 주민의 생활 인프라 격차를 정량화하고, 격차 진단 결과를 행정동 단위 정책 리포트로 자동 생성하는 의사결정 지원 파이프라인.

2026 한동대학교 SW페스티벌 연구보고서 출품작 (SW융합 부문)

---

## 1. 무엇을 하는가

입력은 공공데이터포털·KOSIS·MDIS에서 내려받은 CSV/SHP 파일이고, 출력은 행정동별 격차 점수 히트맵과 LLM이 작성한 행정동별 정책 처방 카드다. 그 사이를 다음 세 층이 잇는다.

| 층 | 기법 | 역할 |
|---|---|---|
| 기반 엔진 | 2SFCA 공간분석 | 수요·공급·접근성을 결합해 격차를 정량화 |
| AI 1층 | NLP 수요 구조화 | 실태조사 응답에서 '체감 결핍' 유형 추출 |
| AI 2층 | LLM 리포트 생성 | 격차 점수 + 결핍 유형 → 행정동별 정책 처방 |

핵심 설계 원칙은 **파라미터 외부화**다. 임계 거리, 인프라 가중치, 대상 행정동 목록은 전부 `config/` 아래 YAML에 있고 코드에는 어떤 상수도 하드코딩하지 않는다. 이는 편의 때문이 아니라, "포항용 분석"이 아니라 "다른 산업도시에 이식 가능한 엔진"이라는 주장을 코드 구조로 증명하기 위해서다.

---

## 2. 개발 상태

| 마일스톤 | 상태 | 산출물 |
|---|---|---|
| M0. 데이터 해상도 검증 게이트 | 조건부 통과 | `reports/m0_data_audit.md` |
| M1. 수집·정제 파이프라인 | 진행 중 — 의료기관·인구·행정동 경계 완료, 상가정보만 남음 | `data/processed/facilities.parquet`, `admin_units.parquet`, `adm_code_map.csv`, `legal_dong_to_admin.csv` |
| M2. 2SFCA 접근성 프로토타입 | 대기 — 입력 데이터(경계+인구) 준비 완료, 2SFCA 로직 미착수 | 의료·금융 2종 접근성 지수 |
| M3. 격차 점수 + 가중치 반영 | 대기 | 행정동별 Gap Score |
| M4. NLP 수요 신호 추출 | 대기 — MDIS 원본 필요 | 결핍 유형 라벨 |
| M5. LLM 리포트 + 시각화 MVP | 대기 | 히트맵 + 처방 카드 |

M0/M1 세부 근거는 `reports/`(`m0_data_audit.md`, `m1_adm_code_map.md`, `m1_legal_dong_mapping.md`, `m1_structure_proposal.md`, `m2_admin_units.md`) 참고. KOSIS/SGIS API 키는 확보되어 인구·행정동 경계 데이터까지 파이프라인에 연결됐고, 남은 외부 블로커는 MDIS 로그인과 상가정보 ingest 코드뿐이다.

> **M0는 차단 게이트다.** 외국인주민현황 데이터가 행정동 단위로 확보되지 않으면 M2 이후의 설계가 통째로 바뀐다. M0를 통과하기 전에는 `src/access/` 이하를 작성하지 않는다.

### M0에서 확인할 것

1. 행안부 「지자체 외국인주민현황」의 최소 공표 단위 — 시군구인가, 읍면동인가
2. ~~포항시 남구·북구의 행정동 목록과 SGIS 경계 파일의 행정동 코드 일치 여부~~ → 확인 완료(29/29 일치, `reports/m2_admin_units.md`)
3. 다문화가족실태조사 '지원 서비스 요구' 항목의 **응답 형식** — 객관식/척도인지, 주관식 텍스트가 존재하는지
4. 상가정보·의료기관 데이터의 좌표계 (EPSG 코드)와 포항시 레코드 수

3번은 특히 중요하다. 해당 항목이 전부 폐쇄형 객관식이면 "NLP 토픽모델링"이라는 서술은 성립하지 않으며, AI 1층의 정의를 다시 써야 한다. (대안: 응답 패턴 기반 잠재클래스 분석 또는 다변량 수요 프로파일링으로 재정의)

---

## 3. 데이터

크롤링·비공개 API를 사용하지 않는다. 전부 파일 형태로 공개된 데이터다.

| 데이터 | 출처 | 용도 | 확보 |
|---|---|---|---|
| 지자체 외국인주민현황 | 행안부 / KOSIS | 외국인 수요 밀도 | ✅ `data/processed/admin_units.parquet` (`pop_foreign`) |
| 주민등록인구 (행정동) | 행안부 / KOSIS | 격차 점수 분모 | ✅ `data/processed/admin_units.parquet` (`pop_total`) |
| 행정동 경계 폴리곤 | 통계청 SGIS | 공간 조인 기준 | ✅ `data/processed/admin_units.parquet` (`geometry`, EPSG:5179) |
| 전국다문화가족실태조사 + 코드북 | MDIS / data.go.kr | 수요 신호 추출 | ⬜ (MDIS 로그인 필요) |
| 의료기관 현황 (좌표) | 심평원 / data.go.kr | 의료 인프라 공급 | ✅ `data/processed/facilities.parquet` |
| 상가(상권)정보 | 소상공인시장진흥공단 | 금융·생활시설 좌표 | ⬜ (API 키 확보, `src/ingest/`에 fetch 코드 미작성) |

**원본 데이터는 커밋하지 않는다.** `data/raw/`는 `.gitignore` 대상이며, 대신 `data/MANIFEST.yaml`에 각 파일의 출처 URL·다운로드 일시·행 수·SHA256 해시를 기록한다. 재현성 확보와 동시에, 심사에서 "데이터를 실제로 다뤘는가"에 대한 증거가 된다.

---

## 4. 리포지토리 구조

```
pohang-infra-gap/
├── README.md
├── CLAUDE.md                  # Claude Code용 프로젝트 컨텍스트
├── pyproject.toml
├── config/
│   ├── pipeline.yaml          # 대상 행정동, 인프라 종류, 임계 거리 d0
│   ├── weights.yaml           # 격차 가중치 ← GM 담당자가 직접 편집
│   └── prompts/               # LLM 프롬프트 템플릿 (버전 관리 대상)
├── data/
│   ├── MANIFEST.yaml
│   ├── raw/                   # gitignored
│   ├── interim/
│   └── processed/
├── src/
│   ├── ingest/                # 수집 — 컴공
│   │   ├── kosis.py
│   │   ├── datagokr.py
│   │   └── sgis.py
│   ├── preprocess/            # 정제 — 컴공
│   │   ├── crs.py             # 좌표계 통일 → EPSG:5179
│   │   ├── admin_join.py      # point-in-polygon 행정동 매칭
│   │   └── validate.py        # 스키마·결측·이상치 검증
│   ├── access/                # 접근성 — AI융합
│   │   ├── catchment.py
│   │   └── two_sfca.py
│   ├── gap/
│   │   ├── score.py           # weights.yaml 적용
│   │   └── cluster.py         # 정책 유형 분류
│   ├── nlp/
│   │   └── demand.py
│   ├── policy/
│   │   └── generate.py        # LLM 리포트 생성
│   └── viz/
│       └── heatmap.py
├── scripts/
│   ├── m0_audit.py            # 데이터 해상도 검증
│   └── run_pipeline.py        # 전체 실행 진입점
├── notebooks/                 # 탐색용. 최종 로직은 src/로 승격
├── outputs/
│   ├── figures/
│   └── reports/
└── tests/
```

`src/` 하위 디렉터리 경계가 팀 역할 경계와 일치한다. 한 사람이 남의 디렉터리를 직접 수정하지 않고 정해진 데이터 포맷으로만 주고받는다. 통합 단계(M5 직전)의 충돌 위험을 줄이기 위한 구조이자, 보고서의 학제 간 협업 서술을 뒷받침하는 증거다.

---

## 5. 인터페이스 계약

단계 간 데이터 포맷을 먼저 고정하고 각자 병렬로 작업한다. 상대 모듈이 완성되기 전에도 더미 데이터로 개발할 수 있다.

```
preprocess → access
  admin_units.parquet   [adm_cd, adm_nm, geometry, pop_total, pop_foreign]
  facilities.parquet    [fac_id, fac_type, lon, lat, capacity]

access → gap
  accessibility.parquet [adm_cd, fac_type, access_index]

gap → policy
  gap_scores.parquet    [adm_cd, fac_type, gap_score, rank, cluster_id]

nlp → policy
  demand_signals.parquet [region_key, demand_type, intensity]

policy → viz
  reports.json          [adm_cd, diagnosis, prescription, evidence[]]
```

`adm_cd`는 통계청 행정동 코드 체계를 단일 기준으로 삼는다. 행안부·SGIS 코드가 다를 경우 `data/processed/adm_code_map.csv`로 매핑하고, 이 매핑 테이블은 수작업 검수 대상이다.

---

## 6. 실행

```bash
# 환경
uv sync                                  # 또는 pip install -e .

# M0 데이터 감사
python scripts/m0_audit.py --config config/pipeline.yaml

# 전체 파이프라인
python scripts/run_pipeline.py --config config/pipeline.yaml --stage all

# 특정 단계만
python scripts/run_pipeline.py --stage access
```

각 단계는 중간 산출물을 `data/interim/`에 캐시하므로 앞 단계를 반복 실행하지 않는다.

### 주요 의존성

`geopandas`, `shapely`, `pyproj`, `scipy` (KD-Tree 근접 탐색), `pandas`, `pyarrow`, `folium` 또는 `plotly` (히트맵), `anthropic` (LLM 리포트).

---

## 7. 방법론 노트

**2SFCA.** 1단계로 각 시설의 공급/수요 비율을 계산하고, 2단계로 각 행정동에서 도달 가능한 시설들의 비율을 합산한다. 기본 모델은 직선거리로 동작하며 도로거리·교통량은 선택적 정밀화 옵션이다. 데이터 확보 실패가 시스템 전체를 멈추지 않게 하려는 설계다.

**임계 거리 d0의 민감도.** 2SFCA 결과는 catchment 임계값에 민감하다. 단일 값으로 결과를 제시하면 "그 숫자는 어떻게 정했나"라는 질문에 답할 수 없다. 따라서 d0를 여러 값(예: 1km/3km/5km)으로 돌린 민감도 분석표를 산출물에 포함하고, 행정동 순위가 뒤집히는지 여부를 보고한다.

**가중치.** `config/weights.yaml`은 GM 담당자가 정주여건 이론에 근거해 값을 정하고 근거를 주석으로 남긴다. 이 파일의 커밋 히스토리가 곧 학제 간 지적 기여의 물리적 기록이다.

**출력의 성격.** 격차 점수는 단정이 아니라 가설이다. 리포트는 "이 행정동이 우선 점검 대상일 가능성이 높다"는 형태로 서술하며, 최종 판단은 현장 검증과 결합되도록 설계한다.

---

## 8. 알려진 함정

행정동 코드는 기관마다 다르고 연도마다 통폐합으로 바뀐다. 반드시 기준 연도를 고정하고 매핑 테이블을 명시적 산출물로 관리한다.

좌표계가 데이터마다 다르다. 상가정보는 EPSG:5181 계열, 심평원 데이터는 WGS84 도분초 문자열로 오는 경우가 있다. `crs.py`에서 단일 좌표계로 통일한 뒤에만 거리를 계산한다. 위경도(4326)에서 유클리드 거리를 계산하면 결과가 틀린다.

공공데이터는 컬럼명·인코딩(CP949)·결측 표기가 일관되지 않다. 정제 단계 일정을 넉넉히 잡는다.

LLM 리포트는 근거 데이터를 프롬프트에 명시적으로 주입하고, 출력에 근거 필드를 강제한다. 수치를 지어내면 프로젝트 신뢰성 전체가 무너진다.

---

## 9. 팀

| 담당 | 전공 | 산출물 → 다음 단계 입력 |
|---|---|---|
| 데이터 파이프라인, 공간데이터 처리, 시각화 | 컴퓨터공학심화 | 정제된 행정동 데이터셋 → 모델 입력 |
| 2SFCA, NLP, LLM 생성, 정책 유형 분류 | AI융합 | 격차·결핍·처방 결과 → 해석·검증 입력 |
| 문제 정의, 정주여건 이론, 가중치 설계, 정책 타당성 검증 | 글로벌매니지먼트 | 가중치 기준 → 모델 파라미터 |

---

## 10. 산출물

연구보고서(PDF), 발표자료(PPTX), 시연 영상(MP4, 선택). 코드는 보고서의 "구체적 개념 설계 및 데이터 분석 사례" 요건을 뒷받침하는 근거다.
