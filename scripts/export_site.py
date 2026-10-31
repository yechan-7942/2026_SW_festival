"""생성된 대시보드와 정적 호스팅 설정을 배포 폴더에 복사한다."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/site")
    args = parser.parse_args()
    source = ROOT / "outputs/dashboard.html"
    content = source.read_bytes()
    for marker in (b"NVIDIA_NIM_API_KEY", b"nvapi-", b"Authorization: Bearer", b"BEGIN PRIVATE KEY"):
        if marker in content:
            raise ValueError("대시보드에 비밀정보 패턴이 포함돼 배포 내보내기를 중단합니다.")
    target = args.output_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.html").write_bytes(content)
    (target / "vercel.json").write_text(
        json.dumps({"framework": None, "cleanUrls": True}, indent=2) + "\n", encoding="utf-8"
    )
    (target / ".vercelignore").write_text("*\n!index.html\n!vercel.json\n", encoding="utf-8")
    print(f"배포 폴더: {target}")


if __name__ == "__main__":
    main()
