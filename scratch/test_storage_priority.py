"""Verifica que subir el almacén sea PRIORIDAD MÁXIMA cuando se acerca a llenarse,
al menos hasta que el almacén pueda guardar 1M de recursos."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ogbot import economy, startorder
from ogbot.config import Config
from ogbot.models import Planet, Coords, Resources


def make_planet(**buildings):
    p = Planet(id="1", name="P", coords=Coords(1, 1, 8))
    p.buildings = buildings
    p.resources = Resources()
    return p


def test_near_full_storage_wins_over_energy_and_mines():
    cfg = Config()
    # Planeta con minas y energía que normalmente construiría energía o mina,
    # pero el almacén de metal está casi lleno -> debe ganar el almacén.
    p = make_planet(metal_mine=15, crystal_mine=14, deut_synth=10,
                    solar_plant=5, metal_storage=4, crystal_storage=4, deut_tank=4)
    cap = startorder.storage_capacity(p.lvl("metal_storage"))
    p.resources = Resources(metal=cap * 0.95, crystal=0, deut=0)
    choice = economy.next_build(p, cfg, plasma=0)
    assert choice is not None, "esperaba una construcción"
    name, _ = choice
    assert name == "metal_storage", f"esperaba metal_storage, obtuve {name}"
    print("OK near_full -> metal_storage (max priority):", name)


def test_below_1M_target_upgrades_when_half_full():
    cfg = Config()
    # Almacén que aún no puede guardar 1M y está a medio llenar -> se sube igualmente.
    p = make_planet(metal_mine=10, crystal_mine=10, deut_synth=8,
                    solar_plant=10, metal_storage=3, crystal_storage=3, deut_tank=3)
    cap = startorder.storage_capacity(p.lvl("metal_storage"))
    assert cap < 1_000_000
    p.resources = Resources(metal=cap * 0.6, crystal=0, deut=0)
    choice = economy.next_build(p, cfg, plasma=0)
    assert choice is not None
    name, _ = choice
    assert name == "metal_storage", f"esperaba metal_storage, obtuve {name}"
    print("OK below-1M half-full -> metal_storage:", name)


def test_above_1M_target_only_triggers_near_full():
    cfg = Config()
    # Almacén ya capaz de guardar >1M: a medio llenar NO debe forzar almacén.
    lvl = 9  # cap ~ 1.5M
    cap = startorder.storage_capacity(lvl)
    assert cap >= 1_000_000, cap
    p = make_planet(metal_mine=10, crystal_mine=10, deut_synth=8,
                    solar_plant=12, metal_storage=lvl, crystal_storage=lvl, deut_tank=lvl)
    p.resources = Resources(metal=cap * 0.6, crystal=cap * 0.6, deut=cap * 0.6)
    choice = economy.next_build(p, cfg, plasma=0)
    # Debe elegir otra cosa (mina), no almacén.
    if choice is not None:
        name, _ = choice
        assert name not in ("metal_storage", "crystal_storage", "deut_tank"), \
            f"no debía forzar almacén a medio llenar por encima de 1M, obtuve {name}"
    print("OK above-1M half-full -> no fuerza almacén")


def test_target_disabled_uses_only_fill_trigger():
    cfg = Config()
    cfg.storage_min_capacity_target = 0
    p = make_planet(metal_mine=10, crystal_mine=10, deut_synth=8,
                    solar_plant=10, metal_storage=3, crystal_storage=3, deut_tank=3)
    cap = startorder.storage_capacity(p.lvl("metal_storage"))
    p.resources = Resources(metal=cap * 0.6, crystal=0, deut=0)  # 60% < trigger 90%
    choice = economy.next_build(p, cfg, plasma=0)
    if choice is not None:
        name, _ = choice
        assert name != "metal_storage", f"con objetivo desactivado no debía forzar almacén al 60%, obtuve {name}"
    print("OK target=0 -> solo dispara por llenado normal")


if __name__ == "__main__":
    test_near_full_storage_wins_over_energy_and_mines()
    test_below_1M_target_upgrades_when_half_full()
    test_above_1M_target_only_triggers_near_full()
    test_target_disabled_uses_only_fill_trigger()
    print("\nTODAS LAS PRUEBAS OK")
