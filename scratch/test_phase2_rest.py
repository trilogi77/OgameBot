"""Self-check de S2 (visibilidad del espionaje) y C1 (economía adaptativa/calibrada).

    python scratch/test_phase2_rest.py

S2: en el servidor real los informes traen data-raw-hiddenships/hiddendef=1 y NO traen
    data-raw-fleet/defense. Antes eso daba fleet={}/defense={} -> is_undefended=True -> el
    bot atacaba a ciegas. Ahora is_undefended es fail-closed y evaluate() lo descarta.
C1: el umbral de amortización crece con el nivel medio de minas (antes las minas se
    congelaban a ~nivel 19-20) y el payback usa la producción REAL si el planeta la trae.
"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import economy, targets, combat, gamedata as gd
from ogbot.models import EspionageReport, Coords, Resources


# ---------------------------------------------------------------- S2
def _rep(**kw):
    base = dict(coords=Coords(2, 105, 7), player_name="Corona", is_inactive=True,
                resources=Resources(808824, 140000, 62152))
    base.update(kw)
    return EspionageReport(**base)


def test_hidden_report_is_not_undefended():
    r = _rep(fleet_visible=False, defense_visible=False)   # caso REAL del servidor
    assert r.has_full_visibility is False
    assert r.is_undefended is False        # antes: True -> ataque a ciegas


def test_visible_and_empty_is_undefended():
    r = _rep(fleet_visible=True, defense_visible=True, fleet_value=0, defense_value=0)
    assert r.is_undefended is True


def test_visible_with_defense_value_is_defended():
    r = _rep(fleet_visible=True, defense_visible=True, defense_value=50_000)
    assert r.is_undefended is False


def test_evaluate_refuses_without_visibility():
    cfg = types.SimpleNamespace(loot_percent=0.5, fleet_speed=1.0, trade_ratio=(2.5, 1.5, 1.0),
                                debris_factor=0.8, debris_includes_deut=True,
                                min_loot_value=1000, farm_recycle_debris=True)
    r = _rep(fleet_visible=False, defense_visible=False)
    res, reason = targets.evaluate(r, Coords(2, 113, 10), {"espionage_probe": 5},
                                   combat.Tech(0, 0, 0), cfg, return_reason=True)
    assert res is None and "visibilidad" in reason, (res, reason)


# ---------------------------------------------------------------- C1
def _planet(levels, eff=None):
    p = types.SimpleNamespace(max_temp=30, lvl=lambda n: levels.get(n, 0))
    if eff:
        p.production_efficiency = eff
    return p


def test_adaptive_threshold_grows_with_mines():
    cfg = types.SimpleNamespace(target_mine_ratio_payback_hours=24.0,
                                adaptive_mine_payback=True, max_mine_payback_hours=168.0)
    low = economy.effective_payback_threshold(_planet({}), cfg)
    mid = economy.effective_payback_threshold(
        _planet({"metal_mine": 20, "crystal_mine": 20, "deut_synth": 20}), cfg)
    assert low == 24.0, low
    assert mid == 48.0, mid                       # 24 * (1 + 20/20)
    huge = economy.effective_payback_threshold(
        _planet({"metal_mine": 300, "crystal_mine": 300, "deut_synth": 300}), cfg)
    assert huge == 168.0, huge                    # tope duro


def test_adaptive_can_be_disabled():
    cfg = types.SimpleNamespace(target_mine_ratio_payback_hours=24.0,
                                adaptive_mine_payback=False, max_mine_payback_hours=168.0)
    p = _planet({"metal_mine": 30, "crystal_mine": 30, "deut_synth": 30})
    assert economy.effective_payback_threshold(p, cfg) == 24.0


def test_efficiency_shortens_payback():
    cfg = types.SimpleNamespace(trade_ratio=(2.5, 1.5, 1.0), universe_speed=8.0,
                                enable_fusion_reactor=False)
    lv = {"metal_mine": 16, "crystal_mine": 14, "deut_synth": 12, "fusion_reactor": 0}
    base, _ = economy._mine_payback(_planet(lv), "metal_mine", cfg, 0)
    fast, _ = economy._mine_payback(_planet(lv, {"metal": 1.5}), "metal_mine", cfg, 0)
    assert fast < base, (fast, base)              # +50% real -> amortiza antes
    same, _ = economy._mine_payback(_planet(lv, {"metal": 99.0}), "metal_mine", cfg, 0)
    assert abs(same - base) < 1e-9, (same, base)  # valor absurdo -> se ignora


def test_efficiency_hook_exists_in_gamedata():
    # efficiency escala SOLO la parte de mina, no la producción base del planeta (30 metal).
    speed, base = 8.0, 30
    a = gd.metal_production(20, 0, speed)
    b = gd.metal_production(20, 0, speed, efficiency=1.5)
    assert b > a
    assert abs((b / speed - base) - 1.5 * (a / speed - base)) < 1e-6


if __name__ == "__main__":
    test_hidden_report_is_not_undefended()
    test_visible_and_empty_is_undefended()
    test_visible_with_defense_value_is_defended()
    test_evaluate_refuses_without_visibility()
    test_adaptive_threshold_grows_with_mines()
    test_adaptive_can_be_disabled()
    test_efficiency_shortens_payback()
    test_efficiency_hook_exists_in_gamedata()
    print("OK")
