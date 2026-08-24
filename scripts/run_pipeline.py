import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingest import kosis  # noqa: E402
from src.ingest.datagokr import load_facilities  # noqa: E402
from src.preprocess import validate  # noqa: E402
from src.preprocess.admin_join import save_facilities_interim  # noqa: E402

# 아직 실제 로직이 없는 단계. reports/m1_structure_proposal.md의 블로커에
# 걸려 있어 그 이유를 그대로 보여준다 — 조용히 건너뛰지 않는다.
NOT_YET_IMPLEMENTED = {
    "access": "SGIS 행정동 경계(geometry) 확보 전까지 2SFCA 접근성 계산 불가",
    "gap": "access 단계 출력이 있어야 격차 점수 계산 가능",
    "nlp": "MDIS 다문화가족실태조사 원본 확보 전까지 수요 신호 추출 불가",
    "policy": "gap/nlp 단계 출력이 있어야 정책 리포트 생성 가능",
    "viz": "gap 단계 출력(히트맵 입력)이 있어야 시각화 가능",
}
ALL_STAGES = ["ingest", "preprocess", *NOT_YET_IMPLEMENTED.keys()]


def run_ingest(config_path: str) -> None:
    print("[ingest] 심평원 병원/약국 원본 로딩...")
    facilities = load_facilities()
    print(f"  전국 {len(facilities)}건 로드")

    print("[ingest] KOSIS 인구 데이터 fetch 시도...")
    try:
        kosis.fetch_all_target_units(config_path=config_path)
        print("  성공")
    except ValueError as e:
        print(f"  건너뜀 — {e}")


def run_preprocess() -> bool:
    print("[preprocess] 법정동 → 행정동 조인 + 좌표 재투영...")
    path = save_facilities_interim()
    print(f"  저장: {path}")

    print("[preprocess] 검증...")
    return validate.run_all()


def main() -> None:
    parser = argparse.ArgumentParser(description="포항 외국인 주민 생활 인프라 격차 진단 파이프라인")
    parser.add_argument("--config", default="config/pipeline.yaml")
    parser.add_argument("--stage", default="all", choices=["all", *ALL_STAGES])
    args = parser.parse_args()

    stages = ALL_STAGES if args.stage == "all" else [args.stage]

    for stage in stages:
        print(f"\n=== {stage} ===")
        if stage == "ingest":
            run_ingest(args.config)
        elif stage == "preprocess":
            if not run_preprocess():
                print("검증 실패 — 파이프라인 중단", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"미구현: {NOT_YET_IMPLEMENTED[stage]}")


if __name__ == "__main__":
    main()
