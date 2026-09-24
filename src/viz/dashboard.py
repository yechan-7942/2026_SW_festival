"""히트맵·랭킹·정책 카드를 한 화면(HTML)으로 묶은 통합 대시보드.

지금까지는 outputs/figures/(히트맵·랭킹 차트)와 data/processed/policy_cards.parquet가
따로 놀았다 — 결과를 보려면 파일을 여러 개 열어야 했다. 이 모듈은 셋을 하나의
자체완결(self-contained) HTML로 합친다. 차트는 dataviz 스킬 팔레트를 그대로 쓴다
(src.viz.heatmap의 SEQUENTIAL_BLUE/BAR_HUE 재사용 — 같은 색 체계를 두 군데서
따로 정의하지 않기 위해서). 지도는 이미 검증된 gap_heatmap.png를 그대로 임베드한다
— polygon 렌더링을 또 만들 이유가 없다.
"""

import base64
import json
from pathlib import Path

import pandas as pd

from src.viz.heatmap import BAR_HUE, FIGURES_DIR, GAP_SCORES_PATH, load_geo_gap_scores

ADMIN_UNITS_PATH = "data/processed/admin_units.parquet"
POLICY_CARDS_PATH = "data/processed/policy_cards.parquet"
DASHBOARD_OUTPUT_PATH = "outputs/dashboard.html"
HEATMAP_PNG_PATH = f"{FIGURES_DIR}/gap_heatmap.png"

# dataviz 스킬 status palette(references/palette.md) — "고정, 테마 없음"이라 라이트/
# 다크 모드 구분 없이 그대로 쓴다. cluster_id(1=최우선~4=양호)는 이미 우선순위
# 구간이라 상태(state)로 자연스럽게 매핑된다 — 별도 분류 로직 없이 재사용.
STATUS_BY_CLUSTER = {
    1: {"label": "최우선", "hex": "#d03b3b"},
    2: {"label": "주의", "hex": "#ec835a"},
    3: {"label": "보통", "hex": "#fab219"},
    4: {"label": "양호", "hex": "#0ca30c"},
}


def load_dashboard_data(fac_type: str = "보건의료") -> pd.DataFrame:
    """gap_scores + admin_units + policy_cards를 하나의 행정동 단위 테이블로 합친다."""
    gdf = load_geo_gap_scores(fac_type, GAP_SCORES_PATH)[["adm_cd", "adm_nm", "gap_score", "rank", "cluster_id"]]
    cards = pd.read_parquet(POLICY_CARDS_PATH)[["adm_nm", "policy_text"]]
    merged = gdf.merge(cards, on="adm_nm", how="left")
    if merged["policy_text"].isna().any():
        missing = merged.loc[merged["policy_text"].isna(), "adm_nm"].tolist()
        raise ValueError(f"policy_cards.parquet에 없는 행정동: {missing}")
    return merged.sort_values("rank").reset_index(drop=True)


def _split_evidence_line(policy_text: str) -> tuple[str, str]:
    """policy_text를 (본문, '[근거]' 줄)로 나눠 카드에서 근거를 따로 강조할 수 있게 한다.

    generate_policy_card()가 '[근거]' 줄 존재를 이미 가드레일로 보장하므로(src/policy/report.py),
    여기서는 못 찾는 경우를 방어적으로 처리하지 않고 그대로 본문 전체를 반환한다 — 있어야
    할 게 없으면 화면이 어색해지는 게 조용히 넘어가는 것보다 낫다.
    """
    lines = [line.strip() for line in policy_text.splitlines() if line.strip()]
    evidence = next((line for line in lines if line.startswith("[근거]")), "")
    body = "\n".join(line for line in lines if line != evidence)
    return body, evidence


def _embed_png(path: str) -> str:
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def build_dashboard_html(fac_type: str = "보건의료") -> str:
    df = load_dashboard_data(fac_type)
    records = []
    for row in df.itertuples():
        body, evidence = _split_evidence_line(row.policy_text)
        status = STATUS_BY_CLUSTER[row.cluster_id]
        records.append(
            {
                "adm_nm": row.adm_nm,
                "rank": int(row.rank),
                "gap_score": round(float(row.gap_score), 3),
                "status_label": status["label"],
                "status_hex": status["hex"],
                "body": body,
                "evidence": evidence,
            }
        )

    top = records[0]
    avg_score = sum(r["gap_score"] for r in records) / len(records)
    heatmap_src = _embed_png(HEATMAP_PNG_PATH)
    data_json = json.dumps(records, ensure_ascii=False)

    return _TEMPLATE.format(
        data_json=data_json,
        heatmap_src=heatmap_src,
        n_dongs=len(records),
        top_name=top["adm_nm"],
        top_score=top["gap_score"],
        avg_score=f"{avg_score:.2f}",
        bar_hue=BAR_HUE,
        fac_type=fac_type,
    )


def save_dashboard(path: str = DASHBOARD_OUTPUT_PATH, fac_type: str = "보건의료") -> str:
    html = build_dashboard_html(fac_type)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(html, encoding="utf-8")
    return path


_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>포항시 외국인 주민 생활 인프라 격차 진단</title>
<style>
  :root {{
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --gridline: #e1e0d9;
    --border: rgba(11,11,11,0.10);
    --bar-hue: {bar_hue};
    --evidence-bg: #f7f9fc;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      color-scheme: dark;
      --surface-1: #1a1a19;
      --page: #0d0d0d;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #898781;
      --gridline: #2c2c2a;
      --border: rgba(255,255,255,0.10);
      --evidence-bg: #14181f;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--page);
    color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    line-height: 1.5;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; padding: 32px 20px 80px; }}
  header h1 {{ font-size: 22px; margin: 0 0 4px; }}
  header p {{ color: var(--text-secondary); margin: 0 0 24px; font-size: 14px; }}

  .filter-row {{ margin-bottom: 20px; }}
  .filter-row input {{
    width: 100%; max-width: 320px; padding: 9px 12px; font-size: 14px;
    border: 1px solid var(--gridline); border-radius: 8px;
    background: var(--surface-1); color: var(--text-primary);
  }}

  .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 28px; }}
  .stat-tile {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px;
  }}
  .stat-tile .label {{ font-size: 12px; color: var(--text-secondary); }}
  .stat-tile .value {{ font-size: 22px; font-weight: 600; margin-top: 4px; }}

  section {{ margin-bottom: 32px; }}
  h2 {{ font-size: 15px; margin: 0 0 12px; padding-left: 10px; border-left: 4px solid var(--bar-hue); }}

  .heatmap-card {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 12px;
  }}
  .heatmap-card img {{ width: 100%; height: auto; display: block; border-radius: 6px; }}

  .view-toggle {{ display: flex; gap: 6px; margin-bottom: 10px; }}
  .view-toggle button {{
    font-size: 12px; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--gridline);
    background: var(--surface-1); color: var(--text-secondary); cursor: pointer;
  }}
  .view-toggle button.active {{ background: var(--bar-hue); color: #fff; border-color: var(--bar-hue); }}

  .bar-row {{
    display: grid; grid-template-columns: 28px 90px 1fr 56px; align-items: center;
    gap: 10px; padding: 3px 0; position: relative;
  }}
  .bar-row .rank {{ font-size: 12px; color: var(--text-muted); text-align: right; }}
  .bar-row .name {{ font-size: 13px; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .bar-track {{ background: var(--gridline); border-radius: 4px; height: 18px; position: relative; }}
  .bar-fill {{
    background: var(--bar-hue); height: 18px; border-radius: 4px 2px 2px 4px;
    transition: opacity .15s;
  }}
  .bar-row .val {{ font-size: 12px; color: var(--text-secondary); font-variant-numeric: tabular-nums; }}
  .bar-row.dim {{ opacity: 0.25; }}
  .bar-row:hover .bar-fill {{ filter: brightness(1.08); }}

  table.data-table {{ width: 100%; border-collapse: collapse; font-size: 13px; display: none; }}
  table.data-table.visible {{ display: table; }}
  table.data-table th, table.data-table td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--gridline); }}
  table.data-table th {{ color: var(--text-secondary); font-weight: 500; }}
  table.data-table td.num {{ font-variant-numeric: tabular-nums; }}
  .bar-list.hidden {{ display: none; }}

  .status-badge {{
    display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 600;
    padding: 2px 8px; border-radius: 999px; color: #fff;
  }}
  .status-badge .dot {{ width: 6px; height: 6px; border-radius: 50%; background: #fff; opacity: .85; }}

  .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }}
  .card {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px;
    display: none;
  }}
  .card.visible {{ display: flex; flex-direction: column; gap: 8px; }}
  .card .card-head {{ display: flex; justify-content: space-between; align-items: center; }}
  .card .card-head .name {{ font-weight: 600; font-size: 14px; }}
  .card .card-head .rank {{ font-size: 12px; color: var(--text-muted); }}
  .card .score {{ font-size: 12px; color: var(--text-secondary); }}
  .card .body {{ font-size: 13px; color: var(--text-primary); white-space: pre-line; }}
  .card .evidence {{
    font-size: 12px; color: var(--text-secondary); background: var(--evidence-bg);
    border-left: 3px solid var(--bar-hue); padding: 6px 10px; border-radius: 0 6px 6px 0;
  }}
  .empty-state {{ color: var(--text-muted); font-size: 13px; padding: 20px 0; display: none; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>포항시 외국인 주민 생활 인프라 격차 진단</h1>
    <p>2SFCA 접근성 분석 + LLM 정책 리포트 — {fac_type} 도메인, 29개 행정동</p>
  </header>

  <div class="filter-row">
    <input type="text" id="search" placeholder="행정동 이름으로 검색 (예: 구룡포)" autocomplete="off">
  </div>

  <div class="stats">
    <div class="stat-tile"><div class="label">분석 행정동</div><div class="value">{n_dongs}개</div></div>
    <div class="stat-tile"><div class="label">최우선 개선 지역</div><div class="value">{top_name}</div></div>
    <div class="stat-tile"><div class="label">최고 격차 점수</div><div class="value">{top_score}</div></div>
    <div class="stat-tile"><div class="label">평균 격차 점수</div><div class="value">{avg_score}</div></div>
  </div>

  <section>
    <h2>공간 분포</h2>
    <div class="heatmap-card"><img src="{heatmap_src}" alt="포항시 행정동별 의료 접근성 격차 점수 히트맵"></div>
  </section>

  <section>
    <h2>행정동별 격차 점수 순위</h2>
    <div class="view-toggle">
      <button id="btn-chart" class="active" onclick="setView('chart')">차트</button>
      <button id="btn-table" onclick="setView('table')">표</button>
    </div>
    <div id="bar-list" class="bar-list"></div>
    <table class="data-table" id="data-table">
      <thead><tr><th>순위</th><th>행정동</th><th>격차 점수</th><th>상태</th></tr></thead>
      <tbody id="table-body"></tbody>
    </table>
    <div class="empty-state" id="empty-bars">검색 결과가 없습니다.</div>
  </section>

  <section>
    <h2>행정동별 정책 카드</h2>
    <div class="cards" id="card-grid"></div>
    <div class="empty-state" id="empty-cards">검색 결과가 없습니다.</div>
  </section>
</div>

<script>
const DATA = {data_json};
let view = 'chart';

function escapeHtml(s) {{
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}}

function renderBars(filterText) {{
  const list = document.getElementById('bar-list');
  const rows = DATA.map(d => {{
    const match = !filterText || d.adm_nm.includes(filterText);
    const pct = (d.gap_score * 100).toFixed(1);
    return `<div class="bar-row ${{match ? '' : 'dim'}}" title="${{escapeHtml(d.adm_nm)}} — 격차 점수 ${{d.gap_score}} (${{d.rank}}위, ${{d.status_label}})">
      <div class="rank">${{d.rank}}위</div>
      <div class="name">${{escapeHtml(d.adm_nm)}}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${{pct}}%"></div></div>
      <div class="val">${{d.gap_score}}</div>
    </div>`;
  }}).join('');
  list.innerHTML = rows;

  const tbody = document.getElementById('table-body');
  tbody.innerHTML = DATA
    .filter(d => !filterText || d.adm_nm.includes(filterText))
    .map(d => `<tr>
      <td class="num">${{d.rank}}</td>
      <td>${{escapeHtml(d.adm_nm)}}</td>
      <td class="num">${{d.gap_score}}</td>
      <td><span class="status-badge" style="background:${{d.status_hex}}"><span class="dot"></span>${{d.status_label}}</span></td>
    </tr>`).join('');

  const anyMatch = DATA.some(d => !filterText || d.adm_nm.includes(filterText));
  document.getElementById('empty-bars').style.display = anyMatch ? 'none' : 'block';
}}

function renderCards(filterText) {{
  const grid = document.getElementById('card-grid');
  let anyMatch = false;
  grid.innerHTML = DATA.map(d => {{
    const match = !filterText || d.adm_nm.includes(filterText);
    if (match) anyMatch = true;
    return `<div class="card ${{match ? 'visible' : ''}}">
      <div class="card-head">
        <span class="name">${{escapeHtml(d.adm_nm)}}</span>
        <span class="rank">${{d.rank}}위 · ${{d.gap_score}}</span>
      </div>
      <span class="status-badge" style="background:${{d.status_hex}}; width:fit-content"><span class="dot"></span>${{d.status_label}}</span>
      <div class="body">${{escapeHtml(d.body)}}</div>
      ${{d.evidence ? `<div class="evidence">${{escapeHtml(d.evidence)}}</div>` : ''}}
    </div>`;
  }}).join('');
  document.getElementById('empty-cards').style.display = anyMatch ? 'none' : 'block';
}}

function setView(v) {{
  view = v;
  document.getElementById('btn-chart').classList.toggle('active', v === 'chart');
  document.getElementById('btn-table').classList.toggle('active', v === 'table');
  document.getElementById('bar-list').classList.toggle('hidden', v !== 'chart');
  document.getElementById('data-table').classList.toggle('visible', v === 'table');
}}

document.getElementById('search').addEventListener('input', (e) => {{
  const q = e.target.value.trim();
  renderBars(q);
  renderCards(q);
}});

renderBars('');
renderCards('');
</script>
</body>
</html>
"""

if __name__ == "__main__":
    path = save_dashboard()
    print(f"저장됨: {path}")
