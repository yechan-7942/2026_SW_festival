import os
import json
import yaml
import requests
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
        "objL2": "0",
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
            start_prd=kosis_cfg["start_prd"],
            end_prd=kosis_cfg["end_prd"],
        )
    return results


if __name__ == "__main__":
    all_results = fetch_all_target_units()
    print(json.dumps(all_results, ensure_ascii=False, indent=2))
