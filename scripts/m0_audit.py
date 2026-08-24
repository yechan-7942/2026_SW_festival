import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from src.ingest.datagokr import load_facilities  # noqa: E402
from src.ingest.kosis import fetch_population  # noqa: E402

# 정성적 판단이 필요해 코드로 재현할 수 없는 M0 항목. 이미 사람이 조사해
# reports/에 근거와 함께 남겨뒀으니 여기서는 그 문서를 가리키기만 한다.
QUALITATIVE_CHECKS = {
    "MDIS 설문 응답 형식 (객관식/주관식)": "reports/m0_data_audit.md 3번 항목 — 원본 조사표 미확보로 미확정",
    "SGIS 경계 파일과의 행정동 코드 대조": "reports/m1_adm_code_map.md — SGIS 키 없어 KOSIS 코드 체계 내부 정합만 확인함",
}


def check_target_dong_count(config: dict) -> bool:
    n = len(config["target_admin_units"])
    print(f"[M0-2] config의 대상 행정동 수: {n}개 (기대값 29 — reports/m1_adm_code_map.md)")
    return n == 29


def check_hira_coordinate_format() -> bool:
    facilities = load_facilities().dropna(subset=["lon", "lat"])
    is_decimal = facilities["lon"].between(120, 132).all() and facilities["lat"].between(30, 40).all()
    print(f"[M0-4] 심평원 좌표가 십진 경위도(EPSG:4326) 범위 내인가: {is_decimal}")
    if not is_decimal:
        print("  경고: 도분초 문자열 등 다른 형식으로 바뀌었을 수 있음 — crs.py 재검토 필요")
    return is_decimal


def check_kosis_dong_level(config: dict):
    """KOSIS_API_KEY가 있을 때만 실제로 재조회해서 읍면동 단위 응답을 확인한다."""
    kosis_cfg = config["kosis"]
    sample_unit = config["target_admin_units"][0]
    try:
        fetch_population(
            obj_l1=sample_unit["adm_cd"],
            org_id=kosis_cfg["org_id"],
            tbl_id=kosis_cfg["tbl_id"],
            itm_id=kosis_cfg["itm_id"],
            start_prd=kosis_cfg["start_prd"],
            end_prd=kosis_cfg["end_prd"],
        )
        print(f"[M0-1] KOSIS {sample_unit['adm_nm']} 읍면동 단위 조회: 성공")
        return True
    except ValueError:
        print("[M0-1] KOSIS_API_KEY 없음 — 재확인 건너뜀 (reports/m0_data_audit.md 기존 감사로 대체)")
        return None
    except Exception as e:
        print(f"[M0-1] KOSIS 조회 실패: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="M0 데이터 해상도 게이트 스모크테스트")
    parser.add_argument("--config", default="config/pipeline.yaml")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    print("M0는 reports/m0_data_audit.md에서 이미 조건부 통과 판정을 받았다.")
    print("이 스크립트는 그중 코드로 재확인 가능한 항목만 스모크테스트한다.\n")

    results = {
        "대상 행정동 29개 일치": check_target_dong_count(config),
        "심평원 좌표 십진 경위도": check_hira_coordinate_format(),
    }
    kosis_result = check_kosis_dong_level(config)
    if kosis_result is not None:
        results["KOSIS 읍면동 단위 접근"] = kosis_result

    print("\n정성적 판단 필요 (코드로 재확인 불가):")
    for name, ref in QUALITATIVE_CHECKS.items():
        print(f"  - {name}: {ref}")

    failures = [name for name, ok in results.items() if not ok]
    print()
    if failures:
        print(f"실패: {failures}")
        sys.exit(1)
    print("자동 검증 가능한 항목 전부 통과.")


if __name__ == "__main__":
    main()
