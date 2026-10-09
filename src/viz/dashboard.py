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

from src.gap.siting import dashboard_payload
from src.viz.heatmap import BAR_HUE, FIGURES_DIR, GAP_SCORES_PATH, load_geo_gap_scores

ADMIN_UNITS_PATH = "data/processed/admin_units.parquet"
POLICY_CARDS_PATH = "data/processed/policy_cards.parquet"
GAP_ROBUSTNESS_PATH = "data/processed/gap_robustness.parquet"
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
    robustness = pd.read_parquet(GAP_ROBUSTNESS_PATH)
    rank_cols = [c for c in robustness.columns if c.endswith("km_rank")]
    robustness["rank_min"] = robustness[rank_cols].min(axis=1)
    robustness["rank_max"] = robustness[rank_cols].max(axis=1)
    merged = merged.merge(robustness[["adm_cd", "gap_type", "rank_min", "rank_max", "top_in_all"]], on="adm_cd", how="left")
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
                "gap_type": row.gap_type,
                "rank_range": f"{int(row.rank_min)}위" if row.rank_min == row.rank_max else f"{int(row.rank_min)}~{int(row.rank_max)}위",
                "stable_top": bool(row.top_in_all),
                "body": body,
                "evidence": evidence,
            }
        )

    top = records[0]
    avg_score = sum(r["gap_score"] for r in records) / len(records)
    heatmap_src = _embed_png(HEATMAP_PNG_PATH)
    data_json = json.dumps(records, ensure_ascii=False)
    siting_json = json.dumps(dashboard_payload(), ensure_ascii=False)

    return _TEMPLATE.format(
        data_json=data_json,
        siting_json=siting_json,
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
  .sim-controls {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 14px; font-size: 13px; }}
  .sim-controls select {{
    padding: 8px 10px; font-size: 14px; border: 1px solid var(--gridline); border-radius: 8px;
    background: var(--surface-1); color: var(--text-primary);
  }}
  .sim-summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 16px; }}
  .sim-row {{ display: grid; grid-template-columns: 90px 1fr 170px; align-items: center; gap: 10px; padding: 3px 0; font-size: 13px; }}
  .sim-track {{ background: var(--gridline); border-radius: 4px; height: 18px; position: relative; }}
  .sim-before {{ position: absolute; left: 0; top: 0; height: 18px; border-radius: 4px; background: var(--text-muted); opacity: .45; }}
  .sim-after {{ position: absolute; left: 0; top: 4px; height: 10px; border-radius: 3px; background: var(--bar-hue); }}
  .sim-row.chosen .name {{ font-weight: 700; }}
  .sim-delta {{ font-size: 12px; color: var(--text-secondary); font-variant-numeric: tabular-nums; text-align: right; }}
  .sim-note {{ font-size: 12px; color: var(--text-secondary); margin-top: 10px; line-height: 1.7; }}
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
      <thead><tr><th>순위</th><th>행정동</th><th>격차 점수</th><th>상태</th><th>임계거리별 순위(1/3/5km)</th><th>격차 유형</th></tr></thead>
      <tbody id="table-body"></tbody>
    </table>
    <div class="empty-state" id="empty-bars">검색 결과가 없습니다.</div>
  </section>

  <section>
    <h2>행정동별 정책 카드</h2>
    <div class="cards" id="card-grid"></div>
    <div class="empty-state" id="empty-cards">검색 결과가 없습니다.</div>
  </section>

  <section>
    <h2>시설 입지 시뮬레이션 — 의료시설을 한 곳 두면?</h2>
    <p style="font-size:13px;color:var(--text-secondary);margin:0 0 12px">가상의 의료시설 1곳을 선택한 행정동에 두고 격차 점수를 다시 계산한 결과다(3km 기준). 회색 막대가 현재, 파란 막대가 시설을 둔 뒤다.</p>
    <div class="sim-controls">
      <label>설치 후보 <select id="sim-site"></select></label>
      <span>규모(의사 수)</span>
      <div class="view-toggle" id="sim-cap" style="margin:0"></div>
    </div>
    <div class="sim-summary" id="sim-summary"></div>
    <div id="sim-bars"></div>
    <div class="sim-note" id="sim-consensus"></div>
    <div class="sim-note">
      · 격차 점수의 절반은 외국인 비율(수요)이라 시설로 줄일 수 없다 — 외국인 비율이 가장 높은 곳은 시설을 많이 둬도 점수가 0.5 아래로 내려가지 않는다.<br>
      · 후보지는 동 단위(그 동네 어딘가)이고 직선거리 기준이다. 부지·인력·예산·언어 지원은 반영하지 않았다.
    </div>
  </section>

  <section>
    <h2>해석 시 유의사항</h2>
    <ul style="font-size:13px;color:var(--text-secondary);line-height:1.7;padding-left:18px;margin:0">
      <li>접근성은 3km 기준 2SFCA 하나이고, 순위는 임계거리(1/3/5km)에 따라 바뀐다 — 위 "임계거리별 순위"를 함께 볼 것.</li>
      <li>정책 카드의 "[근거]"는 전국(여가부)·경북 1권역(포항·경주·영천·경산·청도) 조사 수치이며 포항 단독 수치가 아니다. 행정동별로 달라지는 값이 아니다.</li>
      <li>카드는 LLM이 생성했고 형식만 자동 검증했다 — 내용의 타당성은 사람 검수가 필요하다.</li>
    </ul>
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
      <td class="num">${{d.rank_range}}${{d.stable_top ? ' (안정)' : ''}}</td>
      <td>${{escapeHtml(d.gap_type)}}</td>
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
      <div class="score">${{escapeHtml(d.gap_type)}} · 임계거리 1/3/5km 순위 ${{d.rank_range}}${{d.stable_top ? ' · 모든 임계거리에서 상위 5위 이내' : ''}}</div>
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

const SITING = {siting_json};
let simCap = SITING.capacities[0];

function renderSim() {{
  const site = document.getElementById('sim-site').value;
  const after = SITING.after[String(simCap)][site];
  const before = SITING.gap_before;
  const totalF = SITING.foreign.reduce((a, b) => a + b, 0);
  const wMean = arr => arr.reduce((acc, v, i) => acc + v * SITING.foreign[i], 0) / totalF;
  const idx = SITING.names.indexOf(site);
  const rankOf = (arr, i) => 1 + arr.filter(v => v > arr[i]).length;
  document.getElementById('sim-summary').innerHTML = `
    <div class="stat-tile"><div class="label">${{escapeHtml(site)}} 격차 점수</div><div class="value">${{before[idx].toFixed(3)}} → ${{after[idx].toFixed(3)}}</div></div>
    <div class="stat-tile"><div class="label">${{escapeHtml(site)}} 순위</div><div class="value">${{rankOf(before, idx)}}위 → ${{rankOf(after, idx)}}위</div></div>
    <div class="stat-tile"><div class="label">외국인 가중 평균 격차</div><div class="value">${{wMean(before).toFixed(4)}} → ${{wMean(after).toFixed(4)}}</div></div>`;
  const order = SITING.names.map((n, i) => i).sort((a, b) => before[b] - before[a]);
  document.getElementById('sim-bars').innerHTML = order.map(i => {{
    const delta = after[i] - before[i];
    return `<div class="sim-row ${{i === idx ? 'chosen' : ''}}">
      <div class="name">${{escapeHtml(SITING.names[i])}}</div>
      <div class="sim-track"><div class="sim-before" style="width:${{(before[i] * 100).toFixed(1)}}%"></div><div class="sim-after" style="width:${{(after[i] * 100).toFixed(1)}}%"></div></div>
      <div class="sim-delta">${{before[i].toFixed(3)}} → ${{after[i].toFixed(3)}}${{Math.abs(delta) < 0.0005 ? '' : ' (' + delta.toFixed(3) + ')'}}</div>
    </div>`;
  }}).join('');
}}

function initSim() {{
  const sel = document.getElementById('sim-site');
  sel.innerHTML = SITING.consensus.map((c, k) =>
    `<option value="${{escapeHtml(c.adm_nm)}}">${{escapeHtml(c.adm_nm)}}${{k < 3 ? ' (추천 ' + (k + 1) + '위)' : ''}}</option>`).join('');
  sel.addEventListener('change', renderSim);
  const capBox = document.getElementById('sim-cap');
  capBox.innerHTML = SITING.capacities.map(c => `<button data-cap="${{c}}" class="${{c === simCap ? 'active' : ''}}">${{c}}명</button>`).join('');
  capBox.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {{
    simCap = Number(b.dataset.cap);
    capBox.querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
    renderSim();
  }}));
  const top = SITING.consensus.slice(0, 3).map(c => `${{escapeHtml(c.adm_nm)}}(임계거리 1/3/5km 순위 평균 ${{c.rank_mean}}, 최악 ${{c.rank_worst}}위)`).join(', ');
  document.getElementById('sim-consensus').innerHTML = '임계거리 1/3/5km를 종합한 상위 후보: ' + top + '.';
  renderSim();
}}

renderBars('');
renderCards('');
initSim();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    path = save_dashboard()
    print(f"저장됨: {path}")
