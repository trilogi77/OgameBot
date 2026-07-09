"""Self-check de la Ola 1 de mejoras (bugs concretos de la auditoría).

    python scratch/test_wave1_fixes.py

1. next_defense SOLO propone defensas más allá del lanzamisiles si recibe research_levels
   (antes _defense_step no lo pasaba -> solo lanzamisiles, todo el árbol muerto).
2. targets.evaluate propaga expected_debris al Target (antes se quedaba {} y la cosecha
   post-combate era código muerto).
3. recycler_count usa la bodega efectiva con Hiperespacio (menos recicladores de más).
4. auto_fleet_targets usa floor(sqrt(astro)) para los pathfinders (no astro//2+1).
"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import economy, targets, fleet, combat, gamedata as gd
from ogbot.models import Coords, Resources


def _planet():
    return types.SimpleNamespace(
        coords=types.SimpleNamespace(galaxy=1, system=2, position=3),
        defenses={"rocket_launcher": 50},
        lvl=lambda n: {"shipyard": 6}.get(n, 0),
    )


def _cfg():
    return types.SimpleNamespace(
        enable_defense=True,
        planets_config={"1:2:3": {"defense_targets": {"rocket_launcher": 100, "heavy_laser": 50}}},
        defense_batch_size=25,
    )


def test_defense_needs_research_levels():
    p, cfg = _planet(), _cfg()
    # Sin research_levels: heavy_laser bloqueado -> solo el lanzamisiles (el bug real).
    only = economy.next_defense(p, cfg, research_levels={})
    assert only and only[0] == "rocket_launcher", only
    # Con research_levels: heavy_laser (0% completado) gana al lanzamisiles (50%).
    real = economy.next_defense(p, cfg, research_levels={"laser_tech": 6, "energy_tech": 3})
    assert real and real[0] == "heavy_laser", real


def test_expected_debris_propagated():
    origin = Coords(1, 1, 1)
    report = types.SimpleNamespace(
        coords=Coords(1, 2, 3),
        player_name="inactivo",
        resources=Resources(500_000, 300_000, 100_000),
        fleet={"light_fighter": 50},       # naves del defensor -> generan escombros al morir
        defense={},
        research={"weapons_tech": 0, "shielding_tech": 0, "armor_tech": 0},
        is_undefended=False,               # defendido -> corre el simulador
    )
    cfg = types.SimpleNamespace(
        loot_percent=0.5, fleet_speed=1.0, trade_ratio=(2.5, 1.5, 1.0),
        debris_factor=0.3, debris_includes_deut=False, min_loot_value=1000,
        farm_recycle_debris=True,
    )
    t = targets.evaluate(report, origin, {"cruiser": 300}, combat.Tech(0, 0, 0), cfg)
    assert t is not None, "objetivo descartado (¿win_rate?)"
    assert isinstance(t.expected_debris, dict) and sum(t.expected_debris.values()) > 0, t.expected_debris


def test_recycler_cargo_with_hyperspace():
    debris = {"metal": 100_000, "crystal": 0, "deut": 0}
    base = fleet.recycler_count(debris, 0)
    hyper = fleet.recycler_count(debris, 15)   # +75% bodega -> menos recicladores
    assert hyper < base, (hyper, base)


def test_pathfinder_slots_formula():
    cfg = types.SimpleNamespace(enable_expeditions=True, expedition_use_pathfinder=True,
                                expedition_cargo_ship="large_cargo", enable_farming=False,
                                enable_spy_watch=False, enable_colonization=False, max_colonies=1)
    home = types.SimpleNamespace(lvl=lambda n: 20)   # edificios desbloqueados en 'home'
    rl = {"astrophysics": 16, "hyperspace_tech": 8, "hyperspace_drive": 2,
          "espionage_tech": 2, "combustion_drive": 6}   # prereqs de large_cargo + pathfinder
    tgts = fleet.auto_fleet_targets(home, [home], rl, cfg, expe_cargo_total=100, startup_phase=True)
    # astro 16 -> floor(sqrt)=4 slots, no astro//2+1=9
    assert tgts.get("pathfinder") == gd.expedition_slots(16) == 4, tgts


if __name__ == "__main__":
    test_defense_needs_research_levels()
    test_expected_debris_propagated()
    test_recycler_cargo_with_hyperspace()
    test_pathfinder_slots_formula()
    print("OK")
