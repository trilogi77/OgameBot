"""Verifica que las cuentas con poca Computación (pocos slots) puedan farmear:
la reserva de emergencia "blanda" nunca bloquea el último slot para misiones."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace, MethodType
from ogbot.brain import Brain
from ogbot.config import Config


def make_fake(total, active, reserve=1, allow_last=True, computer_tech=None):
    cfg = Config()
    cfg.keep_free_fleet_slots = reserve
    cfg.allow_last_slot_for_missions = allow_last
    rl = {} if computer_tech is None else {"computer_tech": computer_tech}
    fake = SimpleNamespace(
        cfg=cfg,
        total_fleet_slots=total,
        active_slots=active,
        research_levels=rl,
    )
    # Enlazar los métodos reales de Brain al objeto falso (sin construir un Brain completo)
    for name in ("_effective_slot_reserve", "_has_free_slots_for_mission",
                 "_has_free_slots_for_espionage"):
        setattr(fake, name, MethodType(getattr(Brain, name), fake))
    return fake


def mission(fake):
    return fake._has_free_slots_for_mission()


def espionage(fake):
    return fake._has_free_slots_for_espionage()


def test_one_slot_account_can_spy_and_attack():
    # 1 slot total, reserva 1. Con reserva blanda -> reserva efectiva 0 -> puede espiar.
    f = make_fake(total=1, active=0, reserve=1, allow_last=True)
    assert espionage(f) is True, "cuenta de 1 slot debería poder espiar"
    assert mission(f) is True, "cuenta de 1 slot debería poder atacar cuando el slot está libre"
    # Con la sonda en vuelo (active=1) no quedan slots: no manda otra misión.
    f2 = make_fake(total=1, active=1, reserve=1, allow_last=True)
    assert mission(f2) is False
    print("OK cuenta 1 slot: espía y ataca (tras volver la sonda)")


def test_two_slot_account_keeps_one_reserved():
    # 2 slots, reserva 1. Reserva efectiva = min(1, 1) = 1 -> deja 1 para misión, 1 reservado.
    f = make_fake(total=2, active=0, reserve=1, allow_last=True)
    assert mission(f) is True
    f_one_active = make_fake(total=2, active=1, reserve=1, allow_last=True)
    assert mission(f_one_active) is False, "con 1 activo debe respetar la reserva (queda 1, reservado)"
    print("OK cuenta 2 slots: usa 1, reserva 1")


def test_big_account_unchanged():
    # 10 slots, reserva 1. Reserva efectiva = min(1, 9) = 1: comportamiento idéntico al anterior.
    f = make_fake(total=10, active=0, reserve=1, allow_last=True)
    f_hard = make_fake(total=10, active=0, reserve=1, allow_last=False)
    assert mission(f) == mission(f_hard) is True
    # Al borde de la reserva: 9 activos, reserva 1 -> queda 1, reservado -> False en ambos.
    f9 = make_fake(total=10, active=9, reserve=1, allow_last=True)
    f9h = make_fake(total=10, active=9, reserve=1, allow_last=False)
    assert mission(f9) is False and mission(f9h) is False
    # 8 activos -> queda 2 -> True en ambos.
    f8 = make_fake(total=10, active=8, reserve=1, allow_last=True)
    assert mission(f8) is True
    print("OK cuenta grande: reserva blanda idéntica a la dura")


def test_hard_reserve_disables_tiny_account():
    # Reserva dura (allow_last=False): 1 slot + reserva 1 -> farmeo desactivado.
    f = make_fake(total=1, active=0, reserve=1, allow_last=False)
    assert mission(f) is False
    assert espionage(f) is False
    print("OK reserva dura: cuenta de 1 slot no farmea (opción de máxima seguridad)")


def test_fallback_uses_computer_tech():
    # Sin lectura de slots (total=0), se usa computer_tech+1.
    f = make_fake(total=0, active=0, reserve=1, allow_last=True, computer_tech=0)
    # total efectivo = 1 -> reserva blanda 0 -> puede farmear
    assert mission(f) is True
    print("OK fallback computer_tech: computación 0 -> 1 slot -> farmea con reserva blanda")


if __name__ == "__main__":
    test_one_slot_account_can_spy_and_attack()
    test_two_slot_account_keeps_one_reserved()
    test_big_account_unchanged()
    test_hard_reserve_disables_tiny_account()
    test_fallback_uses_computer_tech()
    print("\nTODAS LAS PRUEBAS OK")
