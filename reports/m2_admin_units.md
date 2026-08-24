# M2 — SGIS 행정동 경계 확보 및 `admin_units.parquet` (`data/processed/admin_units.parquet`)

작성일: 2026-08-24
작성자: Claude Code
근거: `reports/m1_legal_dong_mapping.md` 남은 한계 1번 (SGIS 경계 파일 확보 후 2차 검증)

## 배경

M1 단계에서는 SGIS Open API 키가 없어 심평원 시설의 행정동 배정을 법정동 이름 매칭(1차 조인)만으로 처리했고, 좌표 기반 point-in-polygon 2차 검증은 `admin_join.validate_point_in_polygon()`을 `NotImplementedError` 자리표시자로 남겨뒀다. 이번에 SGIS·KOSIS API 키가 발급되면서 이 두 항목을 마저 구현했다.

## SGIS 인증 방식

SGIS는 KOSIS와 인증 구조가 다르다 — 단일 API 키가 아니라 `consumer_key`(서비스ID) + `consumer_secret`(보안Key) 쌍으로 `https://sgisapi.mods.go.kr/OpenAPI3/auth/authentication.json`에서 accessToken을 발급받고, 이후 모든 API 호출에 그 토큰을 실어 보내는 구조다. 발급 초기에 호출마다 새 토큰을 발급받는 방식으로 짰더니, consumer_key당 활성 토큰이 하나뿐이라 근접한 호출들이 서로의 토큰을 무효화시키는 경합이 간헐적으로 발생했다(`인증 정보가 존재하지 않습니다` 에러). `src/ingest/sgis.py`는 이를 프로세스 수명 동안 토큰을 캐싱·재사용하고, 실패 시에만 한 번 강제 재발급하는 방식으로 해결했다.

## 행정동 코드 체계 불일치

SGIS의 `hadmarea.geojson` 엔드포인트는 통계청 행정표준코드(5자리 시군구 + 8자리 읍면동, 예: 포항 남구=`37011`, 북구=`37012`)를 쓴다. 반면 이 프로젝트는 M1에서부터 KOSIS 자체 분류코드(`adm_cd_kosis`, 예: `15216A1100A37010B000J301`)를 단일 기준(`README.md` §5)으로 삼아왔다. 두 체계는 근본적으로 다른 코드 스킴이라 값으로 직접 비교할 수 없어, `(gu, adm_nm)` 이름 매칭으로 크로스워크를 만들었다.

## 방법

1. `src/ingest/sgis.py`로 포항 남구(`37011`)·북구(`37012`) 하위 읍면동 경계를 `low_search=1`로 한 번에 조회 — 남구 14개 + 북구 15개 = 29개, 좌표는 EPSG:5179(실측 확인: 표본 좌표가 프로젝트 `target_crs`와 동일 좌표계 범위에 위치).
2. `data/processed/adm_code_map.csv`의 `status=current` 29행과 SGIS 29개 행정동을 `(gu, adm_nm)`으로 매칭 — **29/29 완전 일치, 미매칭 0건** (양방향 모두 확인: 우리 쪽에 없는 SGIS 동, SGIS에 없는 우리 동 둘 다 없음).
3. 매칭된 SGIS 8자리 코드를 `adm_code_map.csv`에 새 컬럼 `adm_cd_sgis`로 기록(현재 행정동 29행만 채움, 폐지된 7행은 공백 — 더 이상 독립된 SGIS 경계 엔티티가 아니기 때문).
4. `admin_join.load_admin_boundaries()`가 이 크로스워크로 SGIS geometry를 `adm_cd_kosis` 기준으로 변환하고, `build_admin_units()`가 `kosis.population_by_dong()`(최신연도 총인구·외국인 인구)과 조인해 README §5 계약 `[adm_cd, adm_nm, geometry, pop_total, pop_foreign]`을 만족하는 29행 GeoDataFrame을 생성.

## 2차 검증 결과 (point-in-polygon)

`validate_point_in_polygon()`을 실제 구현해 심평원 시설 908건 전부에 대해 (1차) 법정동 이름 매칭 결과와 (2차) 좌표 기반 SGIS 폴리곤 소속을 대조했다.

- **불일치 11건 / 908건 (약 1.2%)**
- 전부 인접한 행정동 간 경계 부근 사례로 보인다(예: 두호동↔장량동, 중앙동↔용흥동/두호동/죽도동). 좌표 자체의 오류라기보다, 심평원이 기재한 주소지(법정동 텍스트)와 실제 등록 좌표가 행정동 경계에 걸쳐 있거나 좌표 정밀도 차이로 인접 동으로 잡히는 경우로 추정된다.
- 비율이 낮아(5% 미만) 1차 이름 매칭 결과를 그대로 채택하고, 이 불일치는 알려진 한계로 기록만 해둔다. `admin_join.py`의 `join_facilities_to_admin_dong()` 결과(1차 조인)를 여전히 `facilities.parquet`의 공식 `adm_cd`로 쓴다 — `validate_point_in_polygon()`은 진단 도구로만 남긴다.

## 산출물

- `data/processed/admin_units.parquet` — 29행, `[adm_cd, adm_nm, geometry, pop_total, pop_foreign]`. 포항시 전체 `pop_total` 합계 496,653명, `pop_foreign` 합계 7,946명 (2025년 KOSIS 기준).
- `data/processed/adm_code_map.csv` — `adm_cd_sgis` 컬럼 추가.
- `src/ingest/sgis.py`, `src/ingest/kosis.py`(`population_by_dong()` 추가), `src/preprocess/admin_join.py`(`load_admin_boundaries()`, `build_admin_units()`, `save_admin_units()`, `validate_point_in_polygon()` 실구현).

## 남은 한계

- `admin_units.parquet`의 `geometry`는 SGIS `hadmarea.geojson` 그대로이며 별도 단순화(simplify)를 하지 않았다 — 2SFCA 계산에서 성능 이슈가 생기면 이후 조정이 필요할 수 있다.
- point-in-polygon 불일치 11건의 정확한 원인(좌표 정밀도 vs 경계 정의 차이)은 개별 레코드를 뜯어보지 않아 확정하지 못했다. M2 2SFCA 프로토타입 작성 중 필요해지면 재조사한다.
