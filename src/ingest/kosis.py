import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

KOSIS_API_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"


def fetch_population(obj_l1: str, start_prd: str = "2020", end_prd: str = "2025") -> dict:
    api_key = os.getenv("KOSIS_API_KEY")
    if not api_key:
        raise ValueError("KOSIS_API_KEY가 .env에 설정되어 있지 않습니다.")
    params = {
        "method": "getList",
        "apiKey": api_key,
        "orgId": "216",
        "tblId": "DT_216N_B000J2",
        "itmId": "16216T074713+16216TB000J201+16216TB000J202",
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


if __name__ == "__main__":
    result = fetch_population("15216A1100A37010B000J336")
    print(json.dumps(result, ensure_ascii=False, indent=2))
