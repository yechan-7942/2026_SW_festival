"""도로망 거리 2SFCA — 직선거리 기본 모델(two_sfca.py)과 비교하기 위한 변형.

포항은 형산강·해안선·산지 때문에 직선거리와 실제 이동 거리 차이가 클 수 있다.
OSM 차량 도로망에서 최단 도로 거리를 구해 같은 2SFCA를 돌린다. 공식 산출물
(accessibility.parquet)에는 반영하지 않는다 — 비교 결과는 reports/m6_validation.md.

도로망은 data/raw/osm/(git 제외)에 저장하고, 없으면 OSM에서 받는다.
"""

from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from src.access.catchment import load_demand_points, load_supply_points

GRAPH_PATH = "data/raw/osm/pohang_drive.graphml"
PLACE = "Pohang-si, North Gyeongsang Province, South Korea"
CRS = "EPSG:5179"  # 수요·공급점과 같은 좌표계


def load_road_graph(path: str = GRAPH_PATH) -> nx.MultiDiGraph:
    """포항시 차량 도로망(EPSG:5179 투영). 파일이 없으면 OSM에서 받아 저장한다."""
    import osmnx as ox

    if not Path(path).exists():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        ox.save_graphml(ox.graph_from_place(PLACE, network_type="drive"), path)
    return ox.project_graph(ox.load_graphml(path), to_crs=CRS)


def road_distance_matrix(demand, supply, graph: nx.MultiDiGraph, cutoff_m: float = 8000) -> np.ndarray:
    """수요점 × 공급점 도로 거리(m). cutoff_m 안에 닿지 않으면 inf.

    각 점을 가장 가까운 도로 교차점에 붙이고, 붙일 때의 직선 거리를 양 끝에 더한다.
    일방통행은 무시한다(무방향 그래프) — 병원까지 가고 오는 길이 다를 수 있지만
    여기서는 "얼마나 멀리 있나"만 본다.
    """
    import osmnx as ox

    undirected = graph.to_undirected()
    d_nodes, d_snap = ox.distance.nearest_nodes(graph, demand.geometry.x, demand.geometry.y, return_dist=True)
    s_nodes, s_snap = ox.distance.nearest_nodes(graph, supply.geometry.x, supply.geometry.y, return_dist=True)
    s_nodes, s_snap = np.asarray(s_nodes), np.asarray(s_snap)

    matrix = np.full((len(demand), len(supply)), np.inf)
    for i, (node, snap) in enumerate(zip(d_nodes, d_snap)):
        lengths = nx.single_source_dijkstra_path_length(undirected, node, cutoff=cutoff_m, weight="length")
        road = np.array([lengths.get(n, np.inf) for n in s_nodes])
        matrix[i] = road + snap + s_snap
    return matrix


def straight_distance_matrix(demand, supply) -> np.ndarray:
    d = np.column_stack([demand.geometry.x, demand.geometry.y])
    s = np.column_stack([supply.geometry.x, supply.geometry.y])
    return np.linalg.norm(d[:, None, :] - s[None, :, :], axis=2)


def two_sfca_from_matrix(distance_m: np.ndarray, pop: np.ndarray, capacity: np.ndarray, threshold_m: float) -> np.ndarray:
    """거리 행렬로 계산하는 2SFCA — two_sfca.compute_step1/step2와 같은 식."""
    within = distance_m <= threshold_m
    catchment_pop = pop @ within
    ratio = np.divide(capacity, catchment_pop, out=np.zeros(len(capacity)), where=catchment_pop > 0)
    return within @ ratio


def compare_road_vs_straight(category_large: str = "보건의료", thresholds_km=(1, 3, 5), graph=None) -> dict:
    """임계거리별 직선/도로 2SFCA access_index를 나란히 담은 표와 우회율 요약을 반환한다."""
    demand = load_demand_points()
    supply = load_supply_points(category_large)
    graph = graph if graph is not None else load_road_graph()
    road = road_distance_matrix(demand, supply, graph, cutoff_m=max(thresholds_km) * 1000 + 3000)
    straight = straight_distance_matrix(demand, supply)
    pop = demand["pop_total"].to_numpy(dtype=float)
    capacity = supply["capacity"].to_numpy(dtype=float)

    frames = []
    for t in thresholds_km:
        frames.append(
            pd.DataFrame(
                {
                    "adm_cd": demand["adm_cd"].to_numpy(),
                    "threshold_km": t,
                    "straight": two_sfca_from_matrix(straight, pop, capacity, t * 1000),
                    "road": two_sfca_from_matrix(road, pop, capacity, t * 1000),
                }
            )
        )

    pairs = np.isfinite(road) & (straight > 300) & (straight < 5000)  # 아주 가까운 쌍은 붙이는 오차가 우회율을 부풀린다
    detour = road[pairs] / straight[pairs]
    nearest = pd.DataFrame(
        {"adm_cd": demand["adm_cd"].to_numpy(), "nearest_straight_m": straight.min(axis=1), "nearest_road_m": road.min(axis=1)}
    )
    return {
        "access": pd.concat(frames, ignore_index=True),
        "detour_quantiles": pd.Series(np.percentile(detour, [25, 50, 75, 90]), index=["p25", "p50", "p75", "p90"]),
        "nearest": nearest,
    }
