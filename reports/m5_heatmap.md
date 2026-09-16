# M5 — 격차 히트맵·랭킹 시각화

작성일: 2026-09-16
근거: README §5 결과물 형태(행정동별 격차 점수 히트맵), `reports/m3_gap_score.md`(gap_scores.parquet)

## 구현

`src/viz/heatmap.py`:

- `load_geo_gap_scores(fac_type)` — `gap_scores.parquet` + `admin_units.parquet`(geometry)를 `adm_cd`로 병합, EPSG:5179 → EPSG:4326 재투영.
- `build_gap_heatmap(fac_type)` — plotly `choropleth_map`(MapLibre 기반, mapbox 토큰 불필요)으로 29개 행정동 코로플레스 지도를 만든다.
- `build_gap_ranking_bar(fac_type, top_n)` — 격차 점수 상위 N개 행정동 가로 막대그래프. 지도만으로는 정확한 순위 비교가 어려워 보완용으로 추가.
- `save_figures()` → `outputs/figures/`에 PNG(보고서 삽입용, `kaleido`로 정적 렌더링)와 HTML(대화형 탐색용) 둘 다 저장.

## 배색 — 개념설계서의 "빨강=취약/초록=양호" 대신 sequential 단일 색조

개념설계서 §5.4는 히트맵을 "빨강=취약, 초록=양호"로 서술했지만, 실제 구현에서는 dataviz 방법론(`references/choosing-a-form.md`)을 따라 **sequential 단일 색조(파랑, 연함→진함)**를 썼다. 이유: `gap_score`는 0(양호)~1(심각) 사이의 **크기(magnitude)** 값이지, 어떤 기준점을 중심으로 양쪽으로 갈리는 **극성(polarity)** 값이 아니다 — 빨강/초록 발산(diverging) 배색은 "기준점 대비 위/아래"를 표현할 때 쓰는 것이라 이 데이터의 성격과 맞지 않는다. 게다가 적록 조합은 색약 사용자에게 구분이 가장 어려운 조합이기도 하다. 단일 색조 sequential 램프는 구조적으로 색약 안전(colorblind-safe)하다.

## 베이스맵 — OSM/CARTO 대신 흰 배경 + 직접 라벨링

첫 시도(OSM, `carto-positron`)에서는 두 가지 문제가 나왔다:
1. 줌·중심을 임의로 잡으면 영덕군·경주시·대구까지 화면에 잡혀 정작 봐야 할 29개 행정동이 화면 일부에 몰린다 → `gdf.total_bounds`(+4% 패딩)로 지도 범위를 행정동 경계에 정확히 맞췄다.
2. 두 베이스맵 모두 이 지역 지명을 로마자(예: "Jukjang-myeon")로 표기해 한국어 보고서에 어색하다 → `map_style="white-bg"`로 베이스맵을 비우고, 각 행정동의 `representative_point()`(오목한 도형에서도 폴리곤 밖으로 라벨이 튀지 않음 — `centroid`와 달리)에 한글 동 이름을 직접 얹었다.

## 알려진 한계

- 포항 도심 밀집 지역(중앙동·죽도동·상대동 등 좁은 행정동이 몰린 구간)은 지도상 라벨이 겹쳐 읽기 어렵다 — 정확한 순위 비교는 `build_gap_ranking_bar`(막대그래프)로 보완해야 한다.
- 현재는 `보건의료` 도메인·기본 임계값(3km) 한 장만 생성한다. `m3_gap_score.md`가 보고한 임계거리 민감도(1/3/5km)를 지도로 나란히 비교하는 기능은 아직 없다 — 필요하면 `build_gap_heatmap`을 감싸 여러 임계값의 `gap_scores`를 각각 렌더링하면 된다.
- "정책 처방 카드"(README §5.4 결과물 형태의 나머지 절반)는 이번 범위에 포함하지 않았다 — LLM 리포트 생성은 `gap_scores.parquet` + `demand_signals.parquet`(M4, MDIS 블로커) 둘 다 필요하다(README §5 인터페이스 계약).

## 산출물

- `outputs/figures/gap_heatmap.png` / `.html`
- `outputs/figures/gap_ranking_bar.png` / `.html`
- `scripts/run_pipeline.py --stage viz`로 재생성 가능
