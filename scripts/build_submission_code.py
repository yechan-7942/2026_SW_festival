"""허용 목록으로 제출용 코드 스냅샷을 만들고 파일 해시·비밀정보 제외를 검사한다."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "pohang-infra-gap"


def build(output_dir: Path) -> Path:
    paths = {ROOT / name for name in ("README.md", "pyproject.toml", "uv.lock")}
    for pattern in ("src/**/*.py", "scripts/*.py", "scripts/*.sh", "tests/*.py", "config/*.yaml",
                    "data/processed/*.parquet", "data/processed/*.csv", "data/processed/*.json",
                    "data/MANIFEST.yaml", "reports/m[0-9]*.md", "reports/portability.md",
                    "reports/pilot_evaluation.md", "reports/facility_policy_link_20261101.md",
                    "outputs/dashboard.html", "outputs/comparison.json", "outputs/comparison_ranks.csv",
                    "outputs/figures/gap_heatmap.png", "outputs/figures/gap_heatmap.html",
                    "outputs/figures/gap_ranking_bar.html", "outputs/facility_scenario/*.json",
                    "outputs/facility_scenario/*.csv", "outputs/facility_scenario/*.md"):
        paths.update(ROOT.glob(pattern))
    # 사람 평가 결과·정답 키·로컬 설정은 코드 패키지에 포함하지 않는다.
    paths = sorted(p for p in paths if p.is_file() and "__pycache__" not in p.parts)
    # dotenv 값은 대조에만 사용하고 로그·manifest에 출력하지 않는다.
    secrets = [v.encode() for v in dotenv_values(ROOT / ".env").values() if v and len(v) >= 12]
    token_pattern = re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY|\b(?:nvapi-|ghp_|github_pat_|sk-proj-)[A-Za-z0-9_-]{16,}")
    entries = {}
    for path in paths:
        name = path.relative_to(ROOT).as_posix()
        content = path.read_bytes()
        if token_pattern.search(content) or any(secret in content for secret in secrets):
            raise ValueError(f"비밀정보 포함 가능성이 있어 패키징 중단: {name}")
        entries[name] = content
    entries[".env.example"] = ("# 오프라인 재현에는 키가 필요하지 않습니다. 실제 수집·생성 시에만 로컬에서 설정하세요.\n"
                               "NVIDIA_NIM_API_KEY=\nSGIS_CONSUMER_KEY=\nSGIS_CONSUMER_SECRET=\n"
                               "KOSIS_API_KEY=\nDATA_GO_KR_API_KEY=\n").encode()
    entries["SUBMISSION_README.md"] = """# 제출용 코드 실행 안내

공개 처리 스냅샷에서 공간분석·시설 추가 시나리오·정책 연결·정적 화면을 재현한다.
원본 재수집, 실제 LLM 응답 재생성, 사람 검토·정책 효과 검증은 이 범위에 포함되지 않는다.

## 설치와 실행

Python 3.11 이상과 uv가 필요하다. 의존성 최초 설치에는 인터넷이 필요하다.

```bash
uv sync --frozen --group dev
uv run python scripts/reproduce_offline.py --output-dir /절대경로/새로운빈폴더
uv run python -m src.policy.siting_evidence
uv run python -m src.viz.dashboard
```

`outputs/dashboard.html`은 브라우저에서 바로 열 수 있다. 정적 화면은 외부 API 없이 동작한다.
재현 폴더는 새 폴더를 사용한다. 입력 파일을 덮어쓰지 않는다.

## 핵심 검사

```bash
uv run pytest tests/test_siting_evidence.py tests/test_siting.py tests/test_dashboard.py tests/test_scenarios.py -q --tb=short
```

외부 API 원본 수집에는 `.env.example`의 키를 로컬 `.env`로 설정해야 한다.
수집 실패 상세 traceback에는 인증 인자가 나올 수 있으므로 외부 연동 검사에는 `--tb=no`를 사용한다.
원본 재수집에는 별도 공개 파일 다운로드도 필요하다. 입력 출처는 `data/MANIFEST.yaml`에 있다.

## 포함·제외 범위

소스·설정·테스트·처리 데이터·대시보드·시나리오 증거와 방법론 문서를 포함한다.
`.env`, Git 이력, 개인 작업 지침, 원본 파일, 캐시, 평가 정답 키·개인 평가 기록은 제외했다.
최종 보고서·발표자료·영상·출품서는 별도 제출 항목이다. 이 ZIP만으로 모든 제출 항목이 완료되는 것은 아니다.
`scripts/ask_claude.sh`는 과거 선택적 자문용이며 실행·재현에 필요하지 않다.

공간 점수·순위·가상 시설 비교는 코드가 계산한다. 기존 LLM 카드에 구룡포읍 계산 근거·검토 순서를 코드로 연결했다.
시설 필요 개수·최적 부지·실제 의료 이용 개선을 산정한 결과가 아니다. 통역·예약·동행은 현장 확인 후 검토할 보완책이다.
""".encode()
    manifest = {"scope": "code-and-processed-input-snapshot", "base_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "includes_working_tree": True, "secret_scan": "passed",
        "files": {name: {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
                  for name, content in entries.items()}}
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    archive = output_dir / "pohang_source_code.zip"
    pending = archive.with_suffix(".pending.zip")
    with ZipFile(pending, "w", ZIP_DEFLATED) as zipped:
        for name, content in entries.items():
            zipped.writestr(f"{PREFIX}/{name}", content)
        zipped.writestr(f"{PREFIX}/submission_manifest.json", manifest_bytes)
    with ZipFile(pending) as zipped:
        assert zipped.testzip() is None
        assert not any(Path(n).name == ".env" or "/data/raw/" in n or "/data/interim/" in n for n in zipped.namelist())
    pending.replace(archive)
    (output_dir / "code_manifest.json").write_bytes(manifest_bytes)
    print(json.dumps({"archive": str(archive), "files": len(entries) + 1, "bytes": archive.stat().st_size,
                      "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "secret_scan": "passed"}, ensure_ascii=False))
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/submission_20261101")
    build(parser.parse_args().output_dir)
