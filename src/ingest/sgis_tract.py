"""집계구(SGIS 통계지역경계) 인구로 행정동의 인구가중 중심점을 만든다.

2SFCA의 수요점을 행정동 폴리곤의 기하학적 중심점으로 두면, 죽장면처럼 넓고 오목한
행정동에서는 실제 주민이 사는 곳과 어긋난다(reports/m2_two_sfca.md 알려진 한계).
집계구는 행정동보다 훨씬 작아서(구룡포읍 20개), 집계구 중심을 인구로 가중평균하면
실제 인구 분포에 가까운 수요점이 된다.

- 집계구 경계·중심(x, y)은 2025년, 인구는 가장 최근에 제공되는 2024년을 쓴다
  (SGIS 인구 API가 2025년은 아직 비어 있다). 집계구 코드가 두 해에 걸쳐 같은 것을 확인했다.
- 총인구 기준이다(2SFCA의 pop_total 수요와 같은 정의). 집계구 단위 외국인 인구는 공개되지 않는다.
- 인구가 "N/A"(비공개)인 집계구는 가중치 0으로 둔다.
"""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from src.ingest.sgis import SGIS_CRS, get_access_token

BASE = "https://sgisapi.mods.go.kr/OpenAPI3/"
POP_CENTROIDS_PATH = "data/processed/pop_centroids.parquet"


def _get(path: str, params: dict) -> dict:
    token = get_access_token()
    data = requests.get(BASE + path, params={"accessToken": token, **params}).json()
    if data.get("errCd") != 0:
        token = get_access_token(force_refresh=True)
        data = requests.get(BASE + path, params={"accessToken": token, **params}).json()
    if data.get("errCd") != 0:
        raise RuntimeError(f"SGIS {path} 실패({params.get('adm_cd')}): {data.get('errMsg')}")
    return data


def fetch_tracts(adm_cd_sgis: str, boundary_year: str = "2025", pop_year: str = "2024") -> pd.DataFrame:
    """행정동 하나의 집계구별 [tract_cd, x, y, pop]."""
    boundary = _get("boundary/statsarea.geojson", {"year": boundary_year, "adm_cd": adm_cd_sgis})
    pops = _get("stats/population.json", {"year": pop_year, "adm_cd": adm_cd_sgis, "low_search": 1})
    pop_by_tract = {r["adm_cd"]: pd.to_numeric(r.get("tot_ppltn"), errors="coerce") for r in pops["result"]}
    rows = []
    for f in boundary["features"]:
        p = f["properties"]
        pop = pop_by_tract.get(p["adm_cd"])
        rows.append(
            {
                "tract_cd": p["adm_cd"],
                "x": float(p["x"]),
                "y": float(p["y"]),
                "pop": 0.0 if pop is None or pd.isna(pop) else float(pop),
            }
        )
    return pd.DataFrame(rows)


def weighted_centroid(tracts: pd.DataFrame) -> tuple[float, float] | None:
    """인구가중 평균 좌표. 인구 합이 0이면 None(호출부가 기하 중심점으로 대체)."""
    total = tracts["pop"].sum()
    if total <= 0:
        return None
    return (tracts["x"] * tracts["pop"]).sum() / total, (tracts["y"] * tracts["pop"]).sum() / total


def build_pop_centroids() -> pd.DataFrame:
    from src.preprocess.admin_join import load_current_admin_units

    units = load_current_admin_units()[["current_adm_cd_kosis", "adm_nm", "adm_cd_sgis"]]
    records = []
    for r in units.itertuples():
        tracts = fetch_tracts(str(r.adm_cd_sgis))
        centroid = weighted_centroid(tracts)
        if centroid is None:
            continue
        records.append(
            {
                "adm_cd": r.current_adm_cd_kosis,
                "adm_nm": r.adm_nm,
                "x": centroid[0],
                "y": centroid[1],
                "n_tracts": len(tracts),
                "pop_sum": tracts["pop"].sum(),
            }
        )
    return pd.DataFrame(records)


def save_pop_centroids(path: str = POP_CENTROIDS_PATH) -> str:
    df = build_pop_centroids()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return path


if __name__ == "__main__":
    print(save_pop_centroids())
