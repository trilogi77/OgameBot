import os, sys, types
from types import MethodType
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain


def make(reserve, keep=1, soft=True, total=5, active=0, computer=4):
    fs = types.SimpleNamespace(
        cfg=types.SimpleNamespace(reserve_fleetsave_slot=reserve,
                                  keep_free_fleet_slots=keep,
                                  allow_last_slot_for_missions=soft),
        total_fleet_slots=total, active_slots=active,
        research_levels={"computer_tech": computer},
    )
    for n in ("_effective_slot_reserve", "_has_free_slots_for_mission"):
        setattr(fs, n, MethodType(getattr(brain.Brain, n), fs))
    return fs

# 1) Reservar ON (por defecto): reserva = keep_free_fleet_slots.
assert make(True, keep=1)._effective_slot_reserve(5) == 1
assert make(True, keep=2)._effective_slot_reserve(5) == 2

# 2) Reservar OFF (early game): reserva 0 -> todos los slots para misiones.
assert make(False, keep=1)._effective_slot_reserve(5) == 0
assert make(False, keep=3)._effective_slot_reserve(5) == 0

# 3) Con reserva OFF, _has_free_slots_for_mission aprovecha el slot que antes se reservaba.
on = make(True, keep=1, total=2, active=1)    # 1 libre; reserva 1 -> necesita 1+1=2 > 1 -> NO
off = make(False, keep=1, total=2, active=1)  # 1 libre; reserva 0 -> necesita 1 -> SÍ
assert on._has_free_slots_for_mission() is False
assert off._has_free_slots_for_mission() is True

# 4) Reserva blanda seguía respetándose con ON (no rompemos el comportamiento previo).
assert make(True, keep=1, soft=True)._effective_slot_reserve(1) == 0   # total-1
assert make(True, keep=1, soft=False)._effective_slot_reserve(1) == 1  # dura

print("OK")
