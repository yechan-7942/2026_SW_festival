import pandas as pd

HOSPITAL_PATH = "data/raw/전국 병의원 및 약국 현황 2026.3/1.병원정보서비스(2026.3.).xlsx"
PHARMACY_PATH = "data/raw/전국 병의원 및 약국 현황 2026.3/2.약국정보서비스(2026.3.).xlsx"

# 심평원 원본 컬럼명 → 파이프라인 표준 컬럼명.
# 주의: 원본 '읍면동' 컬럼은 행정동이 아니라 법정동 단위다. reports/m1_legal_dong_mapping.md 참고.
# capacity는 병원정보서비스의 '총의사수'를 프록시로 쓴다. 약국정보서비스에는
# 이 컬럼이 아예 없고(약사 인원수 미제공), 병원 쪽도 79,562건 중 2,900건이
# 0으로 기록돼 있다(대부분 보건진료소·보건지소 — 의사가 아니라 간호인력이
# 상주하는 시설이라 구조적으로 0이 맞음). 0과 결측을 구분하지 않고 둘 다
# "실제 공급 능력은 알 수 없지만 이 시설은 존재한다"는 뜻으로 보고 최소
# 공급 단위 1로 채운다 — README §7의 "데이터 확보 실패가 시스템 전체를
# 멈추지 않게 한다" 설계 원칙과 같은 이유. 실제 진료 역량 차이는 반영하지
# 못한다는 한계는 남는다.
FACILITY_COLUMNS = {
    "암호화요양기호": "fac_id",
    "종별코드명": "fac_type",
    "읍면동": "legal_dong_nm",
    "시군구코드명": "sigungu_nm",
    "좌표(X)": "lon",
    "좌표(Y)": "lat",
    "총의사수": "capacity",
}
MIN_CAPACITY = 1


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
    combined = combined.rename(columns=FACILITY_COLUMNS)[list(FACILITY_COLUMNS.values())]
    combined["capacity"] = combined["capacity"].where(combined["capacity"] > 0, MIN_CAPACITY)
    return combined
