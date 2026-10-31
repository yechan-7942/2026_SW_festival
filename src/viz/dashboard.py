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

import numpy as np
import pandas as pd

from src.gap.siting import dashboard_payload
from src.gap.scenarios import build_comparison_payload
from src.policy.report import build_dong_fact_block, load_dong_facts
from src.policy.siting_evidence import build_siting_evidence, link_policy_card
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
    cards = pd.read_parquet(POLICY_CARDS_PATH)
    required = {"adm_nm", "policy_text", "rank", "gap_score"}
    if not required.issubset(cards.columns):
        raise ValueError("정책 카드에 분석 기준 정보가 없습니다. policy 단계를 다시 실행하세요.")
    cards = cards[["adm_nm", "policy_text", "rank", "gap_score"]].rename(
        columns={"rank": "card_rank", "gap_score": "card_gap_score"}
    )
    merged = gdf.merge(cards, on="adm_nm", how="left", validate="one_to_one")
    stale = ~np.isclose(merged["gap_score"], merged["card_gap_score"], rtol=0, atol=1e-9) | merged["rank"].ne(merged["card_rank"])
    if stale.any():
        names = merged.loc[stale, "adm_nm"].tolist()
        raise ValueError(f"현재 분석과 정책 카드의 점수·순위가 다르거나 카드가 없습니다: {names}. policy 단계를 다시 실행하세요.")
    facts = load_dong_facts(fac_type=fac_type)
    for row in merged.itertuples():
        if not isinstance(row.policy_text, str):
            raise ValueError(f"정책 카드가 비어 있습니다: {row.adm_nm}")
        expected = (
            f"[확인된 사실] {row.adm_nm}의 격차 점수는 {row.gap_score:.3f}, 격차 순위는 {row.rank}위입니다.\n"
            + "\n".join(build_dong_fact_block(row.adm_nm, facts[row.adm_nm]).splitlines()[1:])
        )
        if row.policy_text.split("[해석]", 1)[0].strip() != expected:
            raise ValueError(f"현재 인구·접근성·유형과 정책 카드의 사실 구획이 다릅니다: {row.adm_nm}. policy 단계를 다시 실행하세요.")
    merged = merged.drop(columns=["card_rank", "card_gap_score"])
    robustness = pd.read_parquet(GAP_ROBUSTNESS_PATH)
    robustness = robustness.loc[robustness["fac_type"] == fac_type].copy()
    rank_cols = [c for c in robustness.columns if c.endswith("km_rank")]
    robustness["rank_min"] = robustness[rank_cols].min(axis=1)
    robustness["rank_max"] = robustness[rank_cols].max(axis=1)
    merged = merged.merge(robustness[["adm_cd", "gap_type", "rank_min", "rank_max", "top_in_all"]], on="adm_cd", how="left", validate="one_to_one")
    if merged["policy_text"].isna().any():
        missing = merged.loc[merged["policy_text"].isna(), "adm_nm"].tolist()
        raise ValueError(f"policy_cards.parquet에 없는 행정동: {missing}")
    return merged.sort_values("rank").reset_index(drop=True)


def _split_evidence_line(policy_text: str) -> tuple[str, str]:
    """policy_text를 (본문, '[근거]' 줄)로 나눠 카드에서 근거를 따로 강조할 수 있게 한다.

    새 카드는 render_policy_card()가 '[근거]' 줄을 코드로 붙이므로(src/policy/report.py),
    여기서는 못 찾는 경우를 방어적으로 처리하지 않고 그대로 본문 전체를 반환한다 — 있어야
    할 게 없으면 화면이 어색해지는 게 조용히 넘어가는 것보다 낫다.
    """
    lines = [line.strip() for line in policy_text.splitlines() if line.strip()]
    evidence = next((line for line in lines if line.startswith("[근거]")), "")
    body = "\n".join(line for line in lines if line != evidence)
    return body, evidence


def _policy_sections(body: str) -> list[dict]:
    """정책 카드 구획을 분리한다. 기존 조건부 카드도 계속 표시한다."""
    sections = []
    labels = ("확인된 사실", "해석", "우선 대응", "보완할 지원", "추가 확인") if "[우선 대응]" in body else ("확인된 사실", "해석", "정책 후보", "추가 확인")
    for label in labels:
        marker = f"[{label}]"
        if marker not in body:
            return []
        content = body.split(marker, 1)[1].split("[", 1)[0].strip()
        sections.append({"label": label, "text": content})
    return sections


def _embed_png(path: str) -> str:
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def build_dashboard_html(fac_type: str = "보건의료") -> str:
    df = load_dashboard_data(fac_type)
    facility_evidence = build_siting_evidence()
    records = []
    for row in df.itertuples():
        policy_text = (link_policy_card(row.policy_text, facility_evidence)
                       if row.adm_cd == facility_evidence["selected"]["adm_cd"] else row.policy_text)
        body, evidence = _split_evidence_line(policy_text)
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
                "sections": _policy_sections(body),
                "evidence": evidence,
            }
        )

    top = records[0]
    avg_score = sum(r["gap_score"] for r in records) / len(records)
    heatmap_src = _embed_png(HEATMAP_PNG_PATH)
    data_json = json.dumps(records, ensure_ascii=False)
    siting_json = json.dumps(dashboard_payload(), ensure_ascii=False)
    comparison = build_comparison_payload()
    comparison_json = json.dumps(comparison, ensure_ascii=False)
    comparison_weight_note = ("수요·접근성 결핍 각 0.5" if comparison["demand_weight"] == 0.5 else
                              f"수요 {comparison['demand_weight']:g}·접근성 결핍 {comparison['access_weight']:g}")

    return _TEMPLATE.format(
        data_json=data_json,
        siting_json=siting_json,
        comparison_json=comparison_json,
        facility_json=json.dumps(facility_evidence, ensure_ascii=False),
        comparison_weight_note=comparison_weight_note,
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

  .cards {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 350px), 1fr)); gap: 14px; }}
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
  .card-section-title {{ font-size: 12px; font-weight: 700; color: var(--bar-hue); margin: 8px 0 4px; }}
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
  .comparison-panel {{ background: var(--surface-1); border: 1px solid var(--gridline); border-radius: 14px; padding: 20px; }}
  .comparison-controls {{ display:flex; gap:14px; flex-wrap:wrap; align-items:end; margin:16px 0; }}
  .comparison-controls label {{ display:flex; flex-direction:column; gap:6px; color:var(--text-secondary); font-size:12px; }}
  .comparison-controls select {{ padding:9px 12px; border:1px solid var(--gridline); border-radius:8px; background:var(--surface-1); color:var(--text-primary); font-size:14px; max-width:100%; }}
  .comparison-top {{ font-size:17px; font-weight:650; line-height:1.7; margin:12px 0; }}
  .comparison-scroll {{ overflow:auto; max-height:460px; }}
  .comparison-table {{ border-collapse:collapse; width:100%; font-size:13px; white-space:nowrap; }}
  .comparison-table th, .comparison-table td {{ padding:10px 12px; border-bottom:1px solid var(--gridline); text-align:left; }}
  .comparison-table th {{ position:sticky; top:0; background:var(--surface-1); }}
  .comparison-table .selected-rank {{ color:var(--bar-hue); font-weight:700; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>포항시 외국인 주민 생활 인프라 격차 진단</h1>
    <p>2SFCA 접근성 분석 + LLM 현장 검토 카드 — {fac_type} 도메인, 29개 행정동</p>
    <p>전체 주민 기반 공급 접근성과 외국인 주민 분포를 결합한 상대적 검토 지표입니다. 실제 의료 이용량·체감 접근성·정책 효과를 측정한 값은 아닙니다.</p>
  </header>

  <div class="filter-row">
    <input type="text" id="search" aria-label="행정동 검색" placeholder="행정동 이름으로 검색 (예: 구룡포)" autocomplete="off">
  </div>

  <div class="stats">
    <div class="stat-tile"><div class="label">분석 행정동</div><div class="value">{n_dongs}개</div></div>
    <div class="stat-tile"><div class="label">현장 검토 우선 지역</div><div class="value">{top_name}</div></div>
    <div class="stat-tile"><div class="label">최고 격차 점수</div><div class="value">{top_score}</div></div>
    <div class="stat-tile"><div class="label">평균 격차 점수</div><div class="value">{avg_score}</div></div>
  </div>

  <section>
    <h2>공간 분포</h2>
    <div class="heatmap-card"><img src="{heatmap_src}" alt="포항시 행정동별 의료 접근성 격차 점수 히트맵"></div>
  </section>

  <section>
    <h2>행정동별 격차 점수 순위</h2>
    <p class="sim-note">최우선·주의·보통·양호 배지는 점수 순위의 네 상대 구간입니다. 절대적인 의료취약지 지정이 아니며, 거리별 상위 5위 유지 여부와는 다른 기준입니다.</p>
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

  <section id="scenario-comparison">
    <h2>정책 목적과 공급 기준을 바꾸면?</h2>
    <div class="comparison-panel">
      <p class="sim-note" style="margin:0">외국인 비율은 지역 내 집중도를, 인구수는 거주 인원을 반영합니다. 목적에 따라 우선 검토 지역이 달라집니다.</p>
      <div class="comparison-controls">
        <label>정책 관점<select id="comparison-metric"><option value="ratio">외국인 비율 중심</option><option value="count">외국인 인구수 중심</option></select></label>
        <label>공급 기준<select id="comparison-supply"></select></label>
        <label>직선거리<select id="comparison-distance"></select></label>
      </div>
      <p id="comparison-meaning" class="sim-note"></p>
      <div id="comparison-top" class="comparison-top" aria-live="polite"></div>
      <p id="comparison-supply-note" class="sim-note"></p>
      <div class="comparison-scroll">
        <table class="comparison-table">
          <thead><tr><th>행정동</th><th>외국인 인구</th><th>외국인 비율</th><th>비율 중심 순위</th><th>인구수 중심 순위</th><th>선택 조건 점수</th></tr></thead>
          <tbody id="comparison-body"></tbody>
        </table>
      </div>
      <p class="sim-note">비교 가중치는 {comparison_weight_note}입니다. 각 조건에서 따로 정규화하므로 점수 차이를 실제 의료 개선량으로 해석하지 않습니다. 원본 총의사수에는 치과·한방 등도 포함되며 진료 기능이 동일하다는 뜻은 아닙니다.</p>
      <p class="sim-note"><strong>위 지도·정책 카드와 아래 입지 시뮬레이션은 기존 혼합 공급·외국인 비율 기준입니다.</strong> 이 비교 화면의 선택은 해당 결과를 변경하지 않습니다. 실제 정책 목적·가중치는 담당자 검토 전입니다.</p>
    </div>
  </section>


  <section id="facility-evidence">
    <h2>분석에서 정책으로 — 우선 검토 지역의 시설 추가 시나리오</h2>
    <p id="facility-selection"></p>
    <div class="sim-controls">
      <label>별도 추가 시나리오 <select id="facility-kind"><option value="hospital">병의원 1곳 · 의사 3명 가정</option><option value="pharmacy">약국 1개소 가정</option></select></label>
      <label>직선거리 <select id="facility-distance"></select></label>
    </div>
    <div class="sim-summary" id="facility-summary"></div>
    <p id="facility-effect"></p>
    <div class="comparison-scroll"><table class="comparison-table"><thead><tr><th>지역 · 후보점 거리</th><th>혼합 접근성 전 → 후</th><th>증감률</th><th>해당 유형 접근성 전 → 후</th><th>격차 점수 전 → 후</th></tr></thead><tbody id="facility-rows"></tbody></table></div>
    <p class="sim-note" id="facility-scope"></p>
    <p class="sim-note"><strong>정책 연결:</strong> 공간적 공급 보완 가능성을 먼저 현장 검토합니다. 통역·예약·동행은 면담에서 필요가 확인될 때 검토할 보완책입니다. 선정 지역 정책 카드에 기본 거리의 계산 근거를 연결했으며 원본 카드 파일은 보존했습니다. 이 화면의 유형·거리 선택은 다른 지도·카드의 기준을 변경하지 않습니다.</p>
  </section>

  <section>
    <h2>행정동별 현장 검토용 정책 카드</h2>
    <p style="font-size:13px;color:var(--text-secondary)">지역 분석을 바탕으로 우선 대응과 보완할 지원을 제안합니다. 구체적인 조치·제안 이유·시행 전 확인 조건을 함께 제공합니다.</p>
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
      · 외국인 비율은 지역 내 집중도를 반영하며 시설을 추가해도 바뀌지 않는 성분입니다. 합산 점수의 감소만으로 실제 개선을 평가하지 않고, 공급 접근성과 실제 이용 결과를 별도로 확인해야 합니다.<br>
      · 후보지는 동 단위(그 동네 어딘가)이고 직선거리 기준이다. 부지·인력·예산·언어 지원은 반영하지 않았다.
    </div>
  </section>

  <section>
    <h2>해석 시 유의사항</h2>
    <ul style="font-size:13px;color:var(--text-secondary);line-height:1.7;padding-left:18px;margin:0">
      <li>접근성은 3km 기준 2SFCA 하나이고, 순위는 임계거리(1/3/5km)에 따라 바뀐다 — 위 "임계거리별 순위"를 함께 볼 것.</li>
      <li>기본 공급량은 병의원 총의사수(0이면 1)와 약국 개소 수를 합산한 혼합 단위다. 비교 화면에서는 원본 총의사수와 약국 개소 수를 분리해 볼 수 있다.</li>
      <li>경북 전체·전국 조사 수치는 배경 참고이며 포항 단독 수치나 행정동별 장벽의 원인 자료가 아니다.</li>
      <li>새 카드의 인구·분석값은 코드가 표시하고 LLM은 해석과 조건부 후보를 작성한다. 실제 이동·언어·비용 장벽과 정책 실행 가능성은 현장 검토가 필요하다.</li>
    </ul>
  </section>
</div>

<script>
const DATA = {data_json};
const COMPARISON = {comparison_json};
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
      ${{d.sections.length ? d.sections.map(s => `<div class="card-section"><div class="card-section-title">${{escapeHtml(s.label)}}</div><div class="body">${{escapeHtml(s.text)}}</div></div>`).join('') : `<div class="body">${{escapeHtml(d.body)}}</div>`}}
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

function renderComparison() {{
  const supply = document.getElementById('comparison-supply').value;
  const metric = document.getElementById('comparison-metric').value;
  const km = Number(document.getElementById('comparison-distance').value);
  const cases = COMPARISON.cases.filter(c => c.supply === supply && c.threshold_km === km);
  const selected = cases.find(c => c.metric === metric).rows;
  const ratios = new Map(cases.find(c => c.metric === 'ratio').rows.map(r => [r.adm_cd,r.rank]));
  const counts = new Map(cases.find(c => c.metric === 'count').rows.map(r => [r.adm_cd,r.rank]));
  document.getElementById('comparison-meaning').textContent = COMPARISON.metrics[metric].meaning;
  document.getElementById('comparison-top').textContent = '선택 조건 상위 5곳: ' + selected.slice(0,5).map(r => r.adm_nm).join(' → ');
  const info = COMPARISON.supplies[supply];
  document.getElementById('comparison-supply-note').textContent = info.label + ' · 공급량이 양수인 시설 ' + info.facilities + '곳 · 단위: ' + info.unit + ' · 직선 ' + km + 'km';
  document.getElementById('comparison-body').innerHTML = selected.map(r => `<tr>
    <td>${{escapeHtml(r.adm_nm)}}</td><td>${{r.pop_foreign.toLocaleString('ko-KR')}}명</td><td>${{(r.foreign_ratio*100).toFixed(1)}}%</td>
    <td class="${{metric === 'ratio' ? 'selected-rank' : ''}}">${{ratios.get(r.adm_cd)}}위</td>
    <td class="${{metric === 'count' ? 'selected-rank' : ''}}">${{counts.get(r.adm_cd)}}위</td>
    <td>${{r.gap_score.toFixed(3)}}</td></tr>`).join('');
}}

function initComparison() {{
  const supply = document.getElementById('comparison-supply');
  supply.innerHTML = Object.entries(COMPARISON.supplies).map(([key,info]) => `<option value="${{key}}">${{escapeHtml(info.label)}}</option>`).join('');
  const distance = document.getElementById('comparison-distance');
  distance.innerHTML = COMPARISON.thresholds_km.map(km => `<option value="${{km}}">${{km}}km</option>`).join('');
  distance.value = String(COMPARISON.default_threshold_km);
  ['comparison-supply','comparison-metric','comparison-distance'].forEach(id => document.getElementById(id).addEventListener('change',renderComparison));
  renderComparison();
}}

const FACILITY = {facility_json};
function renderFacility() {{
  const kind = document.getElementById('facility-kind').value;
  const km = Number(document.getElementById('facility-distance').value);
  const c = FACILITY.cases.find(c => c.kind === kind && c.threshold_km === km);
  const own = c.rows.find(r => r.adm_cd === FACILITY.selected.adm_cd);
  const affected = c.rows.filter(r => r.affected && r.adm_cd !== own.adm_cd);
  document.getElementById('facility-selection').textContent = FACILITY.selected.adm_nm + ' · 기본 분석 ' + FACILITY.selected.rank + '위 · 외국인 주민 ' + FACILITY.selected.pop_foreign.toLocaleString() + '명. ' + FACILITY.selection_reason;
  const value = v => v.toFixed(8);
  document.getElementById('facility-summary').innerHTML = `<div class="stat-tile"><div class="label">${{escapeHtml(own.adm_nm)}} 혼합 공급 접근성</div><div class="value">${{value(own.access_before)}} → ${{value(own.access_after)}}</div></div><div class="stat-tile"><div class="label">격차 점수 · 기준선 정규화 고정</div><div class="value">${{own.gap_before.toFixed(3)}} → ${{own.gap_after.toFixed(3)}}</div></div>`;
  document.getElementById('facility-effect').textContent = '주변 지역 중 모형상 접근성이 증가한 곳: ' + (affected.length ? affected.map(r => r.adm_nm).join(', ') : '없음') + '. 전체 29개 지역을 후보점 거리순으로 표시합니다. 변화 0은 실제 파급효과 부재를 뜻하지 않습니다.';
  document.getElementById('facility-rows').innerHTML = c.rows.map(r => `<tr><td>${{escapeHtml(r.adm_nm)}} · ${{r.distance_km.toFixed(2)}}km${{r.affected ? ' · 증가' : ' · 변화 0'}}</td><td>${{value(r.access_before)}} → ${{value(r.access_after)}}</td><td>${{r.change_pct === null ? '기준값 0' : '+' + r.change_pct.toFixed(2) + '%'}}</td><td>${{value(r.component_before)}} → ${{value(r.component_after)}}</td><td>${{r.gap_before.toFixed(3)}} → ${{r.gap_after.toFixed(3)}}</td></tr>`).join('');
  document.getElementById('facility-scope').textContent = FACILITY.supply_definition + ' 혼합 접근성 단위: 혼합 공급/주민. 해당 유형 단위: ' + c.component_unit + '. 서로 다른 유형의 증가량·증가율로 우열을 판단하지 않습니다. ' + FACILITY.scope;
}}
document.getElementById('facility-distance').innerHTML = [...new Set(FACILITY.cases.map(c => c.threshold_km))].map(km => `<option value="${{km}}">${{km}}km</option>`).join('');
document.getElementById('facility-distance').value = String(FACILITY.default_threshold_km);
['facility-kind','facility-distance'].forEach(id => document.getElementById(id).addEventListener('change', renderFacility));
renderFacility();

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
initComparison();
initSim();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    path = save_dashboard()
    print(f"저장됨: {path}")
