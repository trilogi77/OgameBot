"""Self-check del módulo de misiles interplanetarios (C5, opcional).

    python scratch/test_missiles.py

1. missiles_needed usa structure (no hull=structure/10): con hull subestimaría x10.
2. Los interceptores enemigos suman misiles 1:1.
3. El alcance del IPM: misma galaxia y (impulso*5)-1 sistemas.
4. La capacidad del silo (nivel*10 slots; el IPM ocupa 2).
5. _missile_step NO lanza si falta stock ni si el objetivo está fuera de rango.
"""
import os, sys, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import gamedata as gd
from ogbot.brain import Brain
from ogbot.models import Coords, Resources


def test_uses_structure_not_hull():
    # 1 lanzamisiles: structure 2000 + shield 20 = 2020; con margen 1.25 -> 2525/12000 -> 1 IPM
    assert gd.missiles_needed({"rocket_launcher": 1}) == 1
    # 10 torretas de plasma (structure 100000 c/u): con hull (=10000) saldrían ~11.
    n = gd.missiles_needed({"plasma_turret": 10})
    assert n > 100, n
    # Con armas 12 el daño sube x2.2 -> bajan los misiles necesarios
    assert gd.missiles_needed({"plasma_turret": 10}, weapons_tech=12) < n


def test_interceptors_add_one_to_one():
    base = gd.missiles_needed({"rocket_launcher": 20})
    assert gd.missiles_needed({"rocket_launcher": 20}, enemy_interceptors=5) == base + 5


def test_range():
    assert gd.ipm_range(0) == 0 and gd.ipm_range(1) == 4 and gd.ipm_range(6) == 29
    assert gd.ipm_in_range((2, 100, 8), (2, 104, 5), 1) is True     # 4 sistemas, impulso 1
    assert gd.ipm_in_range((2, 100, 8), (2, 105, 5), 1) is False    # 5 > 4
    assert gd.ipm_in_range((2, 100, 8), (3, 100, 5), 9) is False    # otra galaxia


def test_silo_capacity():
    assert gd.missile_silo_capacity(4) == 40          # 40 slots -> 20 IPM (2 slots c/u)
    assert gd.MISSILE_SLOTS["interplanetary_missile"] == 2
    assert gd.MISSILE_SILO_REQ["interplanetary_missile"] == 4


def _planet(g, s, p, silo=4, ipm=0):
    return types.SimpleNamespace(
        coords=Coords(g, s, p), id="planet-1",
        defenses={"interplanetary_missile": ipm},
        resources=Resources(0, 0, 0), building_in_progress=False,
        lvl=lambda n, _silo=silo: {"missile_silo": _silo}.get(n, 0))


def _report(coords, defense, metal=1_000_000):
    return types.SimpleNamespace(
        coords=coords, is_inactive=True, defense=defense, missiles={}, research={},
        resources=Resources(metal, 0, 0))


def _stub(report, fired):
    c = report.coords
    rep_key = f"{c.galaxy}:{c.system}:{c.position}"   # formato real de read_all_spy_reports
    stub = types.SimpleNamespace(
        cfg=types.SimpleNamespace(enable_missile_attacks=True, missile_min_loot_value=1000,
                                  missile_max_per_target=500, missile_silo_target=4,
                                  trade_ratio=(2.5, 1.5, 1.0), loot_percent=0.5,
                                  keep_resources_buffer=0.0),
        research_levels={"impulse_drive": 6}, my_tech=types.SimpleNamespace(weapons=0),
        log=logging.getLogger("t"), missile_opened=[],
        client=types.SimpleNamespace(
            read_all_spy_reports=lambda: {rep_key: report},
            launch_missiles=lambda *a: (fired.append(a), True)[1]),
        _guard=lambda: True, _save_state=lambda: None, _tg_alert=lambda m: None,
        record_session_action=lambda *a: None,
        _missile_build=lambda p, n: fired.append(("build", n)))
    stub._missile_origin = lambda pl, t, imp: Brain._missile_origin(stub, pl, t, imp)
    return stub


def test_no_launch_without_stock():
    fired = []
    planets = [_planet(2, 100, 8, silo=4, ipm=0)]           # silo OK pero 0 misiles
    stub = _stub(_report(Coords(2, 102, 5), {"rocket_launcher": 10}), fired)
    Brain._missile_step(stub, planets)
    assert fired and fired[0][0] == "build", fired          # construye, no lanza


def test_no_launch_out_of_range():
    fired = []
    planets = [_planet(2, 100, 8, silo=4, ipm=99)]
    stub = _stub(_report(Coords(3, 100, 5), {"rocket_launcher": 10}), fired)  # otra galaxia
    Brain._missile_step(stub, planets)
    assert fired == [], fired                                # ni construye ni lanza


def test_launches_when_ready():
    fired = []
    planets = [_planet(2, 100, 8, silo=4, ipm=99)]
    stub = _stub(_report(Coords(2, 102, 5), {"rocket_launcher": 10}), fired)
    Brain._missile_step(stub, planets)
    assert fired and fired[0][0] is planets[0], fired        # lanzó desde el planeta
    assert stub.missile_opened == ["2:102:5"], stub.missile_opened


if __name__ == "__main__":
    test_uses_structure_not_hull()
    test_interceptors_add_one_to_one()
    test_range()
    test_silo_capacity()
    test_no_launch_without_stock()
    test_no_launch_out_of_range()
    test_launches_when_ready()
    print("OK")
