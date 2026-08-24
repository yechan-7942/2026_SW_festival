import pandas as pd

HOSPITAL_PATH = "data/raw/전국 병의원 및 약국 현황 2026.3/1.병원정보서비스(2026.3.).xlsx"
PHARMACY_PATH = "data/raw/전국 병의원 및 약국 현황 2026.3/2.약국정보서비스(2026.3.).xlsx"

# 심평원 원본 컬럼명 → 파이프라인 표준 컬럼명.
# 주의: 원본 '읍면동' 컬럼은 행정동이 아니라 법정동 단위다. reports/m1_legal_dong_mapping.md 참고.
FACILITY_COLUMNS = {
    "암호화요양기호": "fac_id",
    "종별코드명": "fac_type",
    "읍면동": "legal_dong_nm",
    "시군구코드명": "sigungu_nm",
    "좌표(X)": "lon",
    "좌표(Y)": "lat",
}


def load_hospitals() -> pd.DataFrame:
    return pd.read_excel(HOSPITAL_PATH)


def load_pharmacies() -> pd.DataFrame:
    return pd.read_excel(PHARMACY_PATH)


def load_facilities() -> pd.DataFrame:
    """심평원 병원·약국 원본(전국)을 표준 스키마로 정규화해 반환한다.

    지역 필터링·행정동 조인 같은 프로젝트 특화 로직은 여기서 하지 않는다
    (src/preprocess/admin_join.py의 몫). 이 함수는 raw 파일 경로와 원본
    컬럼명만 알면 되는 순수 ingest 계층이다.
    """
    combined = pd.concat([load_hospitals(), load_pharmacies()], ignore_index=True)
    return combined.rename(columns=FACILITY_COLUMNS)[list(FACILITY_COLUMNS.values())]
