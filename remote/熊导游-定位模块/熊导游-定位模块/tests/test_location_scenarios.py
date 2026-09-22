"""端到端场景测试：读**真实的** scenic_areas.yaml / beacons.yaml，
走**真实的**围栏判定和切换逻辑。

和 test_switch.py 的区别：那边用构造出来的假配置测逻辑分支；
这边用仓库里真正会部署的那份配置测"整条链路合起来对不对"。

场景里的坐标是从配置现取的，所以你改了 scenic_areas.yaml 的坐标或半径，
这里会跟着走，不会变成一堆假失败。
"""
from __future__ import annotations

import pytest

from location.ble_scan import BeaconRegistry
from location.geofence import GeofenceIndex
from location.tools.simulate_route import (AREAS_YAML, BEACONS_YAML, build_scenarios,
                                  evaluate_scenario)

SCENARIO_NAMES = list(build_scenarios(GeofenceIndex.load(AREAS_YAML)))


@pytest.fixture(scope="module")
def config():
    index = GeofenceIndex.load(AREAS_YAML)
    return index, BeaconRegistry.load(BEACONS_YAML), build_scenarios(index)


@pytest.mark.parametrize("scenario", SCENARIO_NAMES)
def test_scenario_matches_expectations(scenario, config):
    index, registry, scenarios = config
    failures = []

    for step, loc in evaluate_scenario(scenario, index, registry, scenarios):
        expect = step.get("expect")
        if expect is None:
            continue
        actual = (loc.mode, loc.poi_name)
        if actual != expect:
            failures.append(f"{step['desc']}：期望 {expect}，实际 {actual}")

    assert not failures, "\n".join(failures)


def test_every_scenario_step_is_described(config):
    """每一步都要有人话说明 —— 这个工具是给人看判定的，说不清就没价值。"""
    _index, _registry, scenarios = config
    for name, steps in scenarios.items():
        assert steps, f"场景「{name}」是空的"
        for step in steps:
            assert step.get("desc"), f"场景「{name}」有步骤缺少 desc"


def test_scenarios_cover_all_three_modes(config):
    """三个 mode 都必须被场景覆盖到，否则翻车路径没被测过。"""
    _index, _registry, scenarios = config
    modes = {expect[0] for steps in scenarios.values()
             for expect in (s.get("expect") for s in steps) if expect}
    assert modes == {"gps", "ble", "none"}
