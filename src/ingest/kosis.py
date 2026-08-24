import os
import json
import yaml
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

KOSIS_API_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
DEFAULT_CONFIG_PATH = "config/pipeline.yaml"


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_population(
    obj_l1: str,
    org_id: str,
    tbl_id: str,
    itm_id: str,
    obj_l2: str,
    start_prd: str,
    end_prd: str,
) -> dict:
    api_key = os.getenv("KOSIS_API_KEY")
    if not api_key:
        raise ValueError("KOSIS_API_KEY가 .env에 설정되어 있지 않습니다.")
    params = {
        "method": "getList",
        "apiKey": api_key,
        "orgId": org_id,
        "tblId": tbl_id,
        "itmId": itm_id,
        "objL1": obj_l1,
        "objL2": obj_l2,
        "prdSe": "Y",
        "startPrdDe": start_prd,
        "endPrdDe": end_prd,
        "format": "json",
        "jsonVD": "Y",
    }
    response = requests.get(KOSIS_API_URL, params=params)
    if response.status_code != 200:
        raise RuntimeError(f"KOSIS API 요청 실패: status code {response.status_code}")
    try:
        data = response.json()
    except Exception as e:
        raise RuntimeError("응답 JSON 파싱 실패") from e
    return data


def fetch_all_target_units(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    config = load_config(config_path)
    kosis_cfg = config["kosis"]
    results = {}
    for unit in config["target_admin_units"]:
        results[unit["adm_cd"]] = fetch_population(
            obj_l1=unit["adm_cd"],
            org_id=kosis_cfg["org_id"],
            tbl_id=kosis_cfg["tbl_id"],
            itm_id=kosis_cfg["itm_id"],
            obj_l2=kosis_cfg["obj_l2"],
            start_prd=kosis_cfg["start_prd"],
            end_prd=kosis_cfg["end_prd"],
        )
    return results


def population_by_dong(config_path: str = DEFAULT_CONFIG_PATH) -> pd.DataFrame:
    """행정동별 최신연도(end_prd) 총인구·외국인 인구를 [adm_cd, pop_total, pop_foreign]로 정리한다."""
    config = load_config(config_path)
    latest_year = config["kosis"]["end_prd"]
    results = fetch_all_target_units(config_path)
    rows = []
    for adm_cd, records in results.items():
        if not isinstance(records, list):
            raise RuntimeError(f"KOSIS 조회 실패(adm_cd={adm_cd}): {records}")
        latest = [r for r in records if r["PRD_DE"] == latest_year]
        pop_total = next(r["DT"] for r in latest if r["ITM_NM"] == "총인구")
        pop_foreign = next(r["DT"] for r in latest if r["ITM_NM"] == "외국인")
        rows.append({"adm_cd": adm_cd, "pop_total": int(pop_total), "pop_foreign": int(pop_foreign)})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    all_results = fetch_all_target_units()
    print(json.dumps(all_results, ensure_ascii=False, indent=2))
