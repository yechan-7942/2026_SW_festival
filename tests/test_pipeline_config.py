import yaml


def _load_config():
    with open("config/pipeline.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_target_admin_units_count_matches_current_dong_count():
    config = _load_config()
    # reports/m1_adm_code_map.md: 포항시 현재 유효 읍면동은 29개.
    assert len(config["target_admin_units"]) == 29


def test_target_admin_units_have_unique_codes():
    config = _load_config()
    codes = [unit["adm_cd"] for unit in config["target_admin_units"]]
    assert len(codes) == len(set(codes))


def test_target_admin_units_gu_values_are_valid():
    config = _load_config()
    for unit in config["target_admin_units"]:
        assert unit["gu"] in ("남구", "북구")


def test_kosis_section_has_required_keys():
    config = _load_config()
    required = {"org_id", "tbl_id", "itm_id", "obj_l2", "start_prd", "end_prd"}
    assert required.issubset(config["kosis"].keys())


def test_distance_thresholds_present():
    config = _load_config()
    assert config["distance_thresholds_km"] == [1, 3, 5]
