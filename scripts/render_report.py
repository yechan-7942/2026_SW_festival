"""reports/research_report.md → reports/research_report.pdf 변환.

pandoc+LaTeX 대신 헤드리스 Chrome을 쓴다 — 한글(CJK) 렌더링을 위해 MacTeX
전체(수 GB)를 설치할 필요 없이, macOS에 이미 있는 Chrome과 시스템 폰트
(Apple SD Gothic Neo)만으로 바로 동작한다.
"""

import base64
import shutil
import subprocess
import sys
from pathlib import Path

import markdown

REPO_ROOT = Path(__file__).resolve().parent.parent
MD_PATH = REPO_ROOT / "reports/research_report.md"
PDF_PATH = REPO_ROOT / "reports/research_report.pdf"
FIGURE_DIR = REPO_ROOT / "outputs/figures"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
]

CSS = """
@page { size: A4; margin: 20mm 18mm; }
* { box-sizing: border-box; }
body {
  font-family: "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", sans-serif;
  color: #1a1a1a;
  line-height: 1.65;
  font-size: 11pt;
  word-break: keep-all;
}
h1 { font-size: 20pt; margin-top: 0; border-bottom: 3px solid #1c5cab; padding-bottom: 8px; }
h1 + h3 { color: #52514e; font-weight: 400; margin-top: 6px; }
h2 { font-size: 15pt; margin-top: 28px; border-left: 5px solid #2a78d6; padding-left: 10px; }
h3 { font-size: 12.5pt; margin-top: 20px; color: #184f95; }
p { margin: 8px 0; }
hr { border: none; border-top: 1px solid #d8d7d2; margin: 24px 0; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 10pt; page-break-inside: avoid; }
th, td { border: 1px solid #cfcfc9; padding: 6px 9px; text-align: left; }
th { background: #eef3fb; }
code { background: #f1f0ec; padding: 1px 5px; border-radius: 3px; font-size: 9.5pt; }
pre { background: #f6f5f1; padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 9.5pt; page-break-inside: avoid; }
img { max-width: 92%; display: block; margin: 10px auto; page-break-inside: avoid; }
blockquote { border-left: 4px solid #2a78d6; margin: 10px 0; padding: 4px 14px; color: #52514e; background: #f7f9fc; }
strong { color: #0d366b; }
"""


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists() or shutil.which(candidate):
            return candidate
    raise RuntimeError("Chrome/Chromium을 찾지 못했습니다 — PDF 변환에 필요합니다 (macOS: Google Chrome 설치).")


def embed_figures(html_body: str) -> str:
    """마크다운의 상대경로 이미지(../outputs/figures/*.png)를 base64로 인라인 삽입."""
    for img_path in FIGURE_DIR.glob("*.png"):
        rel = f"../outputs/figures/{img_path.name}"
        data = base64.b64encode(img_path.read_bytes()).decode("ascii")
        html_body = html_body.replace(f'src="{rel}"', f'src="data:image/png;base64,{data}"')
    return html_body


def build_html() -> str:
    md_text = MD_PATH.read_text(encoding="utf-8")
    body = embed_figures(markdown.markdown(md_text, extensions=["tables", "sane_lists"]))
    return f"""<!doctype html>
<html lang="ko">
<head><meta charset="utf-8"><title>연구보고서</title><style>{CSS}</style></head>
<body>{body}</body>
</html>"""


def render(pdf_path: Path = PDF_PATH) -> Path:
    chrome = find_chrome()
    html_path = pdf_path.with_suffix(".html")
    html_path.write_text(build_html(), encoding="utf-8")

    subprocess.run(
        [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    html_path.unlink()
    return pdf_path


if __name__ == "__main__":
    try:
        path = render()
    except RuntimeError as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"저장됨: {path}")
