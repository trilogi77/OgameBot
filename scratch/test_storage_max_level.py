"""Verifica el tope de nivel por almacén (max_metal_storage / max_crystal_storage /
max_deut_tank): al alcanzar el nivel máximo NO se debe seguir subiendo ese almacén,
aunque esté casi lleno."""
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


def test_cap_reached_does_not_upgrade_even_when_full():
    cfg = Config()
    cfg.max_metal_storage = 4          # tope en el nivel actual
    p = make_planet(metal_mine=15, crystal_mine=14, deut_synth=10,
                    solar_plant=5, metal_storage=4, crystal_storage=4, deut_tank=4)
    cap = startorder.storage_capacity(p.lvl("metal_storage"))
    p.resources = Resources(metal=cap * 0.99, crystal=0, deut=0)  # lleno al 99%
    choice = economy.next_build(p, cfg, plasma=0)
    if choice is not None:
        name, _ = choice
        assert name != "metal_storage", f"con tope alcanzado no debía subir metal_storage, obtuve {name}"
    print("OK tope metal alcanzado -> no sube metal_storage")


def test_below_cap_still_upgrades():
    cfg = Config()
    cfg.max_metal_storage = 9          # aún por debajo del tope
    p = make_planet(metal_mine=15, crystal_mine=14, deut_synth=10,
                    solar_plant=5, metal_storage=4, crystal_storage=4, deut_tank=4)
    cap = startorder.storage_capacity(p.lvl("metal_storage"))
    p.resources = Resources(metal=cap * 0.99, crystal=0, deut=0)
    choice = economy.next_build(p, cfg, plasma=0)
    assert choice is not None and choice[0] == "metal_storage", \
        f"por debajo del tope y lleno debía subir metal_storage, obtuve {choice}"
    print("OK por debajo del tope -> sí sube metal_storage")


def test_cap_zero_is_no_limit():
    cfg = Config()                     # defaults: todos los topes en 0 = sin límite
    p = make_planet(metal_mine=15, crystal_mine=14, deut_synth=10,
                    solar_plant=5, metal_storage=4, crystal_storage=4, deut_tank=4)
    cap = startorder.storage_capacity(p.lvl("metal_storage"))
    p.resources = Resources(metal=cap * 0.99, crystal=0, deut=0)
    choice = economy.next_build(p, cfg, plasma=0)
    assert choice is not None and choice[0] == "metal_storage", \
        f"con tope 0 (sin límite) debía subir metal_storage, obtuve {choice}"
    print("OK tope 0 -> sin límite, sube metal_storage")


def test_storage_blocker_respects_cap():
    from ogbot.gamedata import Cost
    cfg = Config()
    cfg.max_crystal_storage = 3
    p = make_planet(metal_storage=3, crystal_storage=3, deut_tank=3)
    # Coste que NO cabe en la capacidad actual de cristal.
    cap = startorder.storage_capacity(p.lvl("crystal_storage"))
    cost = Cost(0, int(cap * 5), 0)
    # Con tope alcanzado -> no fuerza almacén (se alimenta desde la luna).
    assert startorder.storage_blocker(cost, p, cfg) is None, "con tope no debía forzar almacén"
    # Sin cfg (o tope 0) -> sí lo fuerza, como antes.
    assert startorder.storage_blocker(cost, p) == "crystal_storage"
    print("OK storage_blocker respeta el tope de config")


if __name__ == "__main__":
    test_cap_reached_does_not_upgrade_even_when_full()
    test_below_cap_still_upgrades()
    test_cap_zero_is_no_limit()
    test_storage_blocker_respects_cap()
    print("\nTODAS LAS PRUEBAS OK")
