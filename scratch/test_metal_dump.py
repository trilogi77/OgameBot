"""Self-check del sumidero de metal: Blindaje en vez de almacén de metal.

    python scratch/test_metal_dump.py

Blindaje cuesta 1000 de metal PURO x2 por nivel; el almacén de metal, exactamente lo mismo.
La regla se autolimita: mientras nivel_blindaje < nivel_almacén, el blindaje es más barato y
convierte metal muerto en casco permanente. Cuando lo alcanza, gana el almacén.
"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import economy, gamedata as gd
from ogbot.models import Resources


def _planet(lab=2, storage_lvl=10, metal=1_000_000):
    return types.SimpleNamespace(
        resources=Resources(metal, 0, 0),
        lvl=lambda n, _l=lab, _s=storage_lvl: {"research_lab": _l, "metal_storage": _s}.get(n, 0))


def _cfg(**kw):
    base = dict(enable_metal_dump_research=True, metal_dump_max_armor_tech=20,
                keep_resources_buffer=0.0, research_caps={})
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_armour_is_pure_metal_same_curve_as_storage():
    for lvl in (5, 10, 12):
        a = gd.research_cost("armor_tech", lvl)
        s = gd.building_cost("metal_storage", lvl)
        assert a.crystal == 0 and a.deut == 0, a      # metal puro
        assert a.metal == s.metal, (lvl, a.metal, s.metal)


def test_dumps_when_armour_cheaper_than_storage():
    # blindaje 4 (16k) vs almacén 10 (512k) -> blindaje mucho más barato
    r = economy.metal_dump_research(_planet(storage_lvl=10), _cfg(), {"armor_tech": 4})
    assert r is not None and r[0] == "armor_tech", r
    assert r[1].metal == gd.research_cost("armor_tech", 5).metal


def test_no_dump_when_armour_more_expensive():
    # blindaje 12 (2.048M) vs almacén 10 (512k) -> el almacén gana; se autolimita
    assert economy.metal_dump_research(_planet(storage_lvl=10, metal=10**9),
                                       _cfg(), {"armor_tech": 12}) is None


def test_no_dump_without_lab():
    assert economy.metal_dump_research(_planet(lab=1), _cfg(), {"armor_tech": 0}) is None


def test_no_dump_when_capped():
    assert economy.metal_dump_research(_planet(), _cfg(metal_dump_max_armor_tech=4),
                                       {"armor_tech": 4}) is None
    # el tope del usuario (research_caps) también manda
    assert economy.metal_dump_research(_planet(), _cfg(research_caps={"armor_tech": 3}),
                                       {"armor_tech": 3}) is None


def test_no_dump_when_not_affordable():
    assert economy.metal_dump_research(_planet(metal=1000), _cfg(), {"armor_tech": 9}) is None


def test_disabled_by_flag():
    assert economy.metal_dump_research(_planet(), _cfg(enable_metal_dump_research=False),
                                       {"armor_tech": 0}) is None


# ---------------------------------------------------------------- guardas del brain
def _brain_stub(rl, planet, started):
    import logging
    from ogbot.config import Config
    from ogbot.brain import Brain
    cfg = Config()
    stub = types.SimpleNamespace(
        cfg=cfg, research_levels=rl, last_planets=[planet], log=logging.getLogger("t"),
        _start_research=lambda p, n, c: (started.append((n, c)), True)[1])
    return Brain._try_metal_dump, stub


def _lab_planet(metal=2_730_000, crystal=5_283, deut=106_781, lab=8, storage=9):
    return types.SimpleNamespace(
        coords="2:113:10",
        resources=Resources(metal, crystal, deut),
        lvl=lambda n, _l=lab, _s=storage: {"research_lab": _l, "metal_storage": _s}.get(n, 0))


# Niveles reales del usuario: el desbloqueo pendiente es hyperspace_drive 2 (necesita 40k
# cristal) y solo tienen ~5k -> el slot está ocioso ahorrando cristal.
_REAL = {"armor_tech": 3, "energy_tech": 8, "laser_tech": 10, "ion_tech": 5, "plasma_tech": 1,
         "combustion_drive": 6, "impulse_drive": 5, "hyperspace_drive": 1, "espionage_tech": 4,
         "computer_tech": 5, "astrophysics": 1, "weapons_tech": 3, "shielding_tech": 5,
         "armor_tech_dummy": 0, "hyperspace_tech": 5}


def test_brain_dumps_when_unlock_milestone_unaffordable():
    started = []
    fn, stub = _brain_stub(dict(_REAL), _lab_planet(), started)
    assert fn(stub, _lab_planet()) is True, "debería invertir el metal sobrante en blindaje"
    assert started and started[0][0] == "armor_tech", started


def test_brain_defers_when_unlock_milestone_affordable():
    started = []
    rl = dict(_REAL)
    # con cristal/deut de sobra, el hito de desbloqueo SÍ se paga: el slot es suyo
    p = _lab_planet(crystal=5_000_000, deut=5_000_000)
    fn, stub = _brain_stub(rl, p, started)
    assert fn(stub, p) is False
    assert started == []


def test_brain_defers_from_colony_with_worse_lab():
    started = []
    main = _lab_planet(lab=8)
    colony = _lab_planet(lab=0)
    fn, stub = _brain_stub(dict(_REAL), main, started)
    stub.last_planets = [main, colony]
    assert fn(stub, colony) is False       # nunca investigar desde el lab peor
    assert started == []


if __name__ == "__main__":
    test_armour_is_pure_metal_same_curve_as_storage()
    test_dumps_when_armour_cheaper_than_storage()
    test_no_dump_when_armour_more_expensive()
    test_no_dump_without_lab()
    test_no_dump_when_capped()
    test_no_dump_when_not_affordable()
    test_disabled_by_flag()
    test_brain_dumps_when_unlock_milestone_unaffordable()
    test_brain_defers_when_unlock_milestone_affordable()
    test_brain_defers_from_colony_with_worse_lab()
    print("OK")
