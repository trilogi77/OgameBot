"""Verifica que la colonización tenga prioridad frente a las expediciones cuando
hay una colonización pendiente: se reserva 1 slot de flota extra para que la
colonización (que corre después en la ronda de farmeo) tenga hueco garantizado."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace, MethodType
from ogbot.brain import Brain
from ogbot.config import Config


def make_fake(total, active, colony_ships=0, n_planets=1, max_colonies=9,
              enable_coloni=True, reserve=1, allow_last=True):
    cfg = Config()
    cfg.keep_free_fleet_slots = reserve
    cfg.allow_last_slot_for_missions = allow_last
    cfg.max_colonies = max_colonies
    cfg.enable_colonization = enable_coloni
    planets = [SimpleNamespace(ships={"colony_ship": colony_ships}) for _ in range(n_planets)]
    fake = SimpleNamespace(
        cfg=cfg,
        total_fleet_slots=total,
        active_slots=active,
        research_levels={},
    )
    for name in ("_effective_slot_reserve", "_has_free_slots_for_mission",
                 "_has_ships", "_colony_pending"):
        setattr(fake, name, MethodType(getattr(Brain, name), fake))
    return fake, planets


def test_colony_pending_detects_ship():
    f, planets = make_fake(total=5, active=0, colony_ships=1)
    assert f._colony_pending(planets) is True
    print("OK _colony_pending: nave de colonización + hueco -> True")


def test_colony_pending_false_when_no_ship():
    f, planets = make_fake(total=5, active=0, colony_ships=0)
    assert f._colony_pending(planets) is False
    print("OK _colony_pending: sin nave de colonización -> False")


def test_colony_pending_false_when_disabled_or_maxed():
    f_dis, planets = make_fake(total=5, active=0, colony_ships=1, enable_coloni=False)
    assert f_dis._colony_pending(planets) is False
    f_max, planets_max = make_fake(total=5, active=0, colony_ships=1,
                                   n_planets=9, max_colonies=9)
    assert f_max._colony_pending(planets_max) is False
    print("OK _colony_pending: desactivada o máximo de colonias alcanzado -> False")


def test_reserved_slot_blocks_last_expedition():
    # 5 slots, 3 activos -> quedan 2. Reserva 1 -> mission normal True (queda 2 >= 2).
    # Con reserva extra por colonia: exige 2 (misión) + 1 (colonia) = 3 libres -> False.
    f, _ = make_fake(total=5, active=3, colony_ships=1)
    assert f._has_free_slots_for_mission(extra_reserve=0) is True
    assert f._has_free_slots_for_mission(extra_reserve=1) is False, \
        "con colonización pendiente, la expedición debe ceder el penúltimo slot"
    print("OK reserva de colonia: la expedición deja el slot para colonizar")


def test_no_reserve_when_slots_abundant():
    # 8 slots, 0 activos: sobra hueco para ambas cosas.
    f, _ = make_fake(total=8, active=0, colony_ships=1)
    assert f._has_free_slots_for_mission(extra_reserve=1) is True
    print("OK con slots de sobra: expediciones y colonización conviven sin bloqueo")


if __name__ == "__main__":
    test_colony_pending_detects_ship()
    test_colony_pending_false_when_no_ship()
    test_colony_pending_false_when_disabled_or_maxed()
    test_reserved_slot_blocks_last_expedition()
    test_no_reserve_when_slots_abundant()
    print("\nTODAS LAS PRUEBAS OK")
