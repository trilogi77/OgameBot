import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import research as R
from ogbot import gamedata as gd

# Simula seguir RESEARCH_UNLOCK_ORDER como haría el brain: en cada paso sube el lab a
# necesidad y comprueba que los prerrequisitos ya están cubiertos por pasos anteriores.
levels = {}
lab = 1
steps = 0
seen_lab_reqs = []
while True:
    step = R.next_unlock_research(levels)
    if step is None:
        break
    steps += 1
    assert steps < 500, "el plan no termina (bucle)"
    tech, lvl = step
    assert lvl == levels.get(tech, 0) + 1, f"paso no incremental: {tech} {lvl}"

    # Lab a necesidad.
    lab_req = gd.RESEARCH_LAB_REQ.get(tech, 0)
    if lab < lab_req:
        lab = lab_req
    seen_lab_reqs.append(lab_req)

    # Prerrequisitos de investigación deben estar YA cubiertos (validez del orden).
    for rname, rlvl in gd.RESEARCH_PREREQS.get(tech, {}).items():
        assert levels.get(rname, 0) >= rlvl, \
            f"orden inválido: {tech} {lvl} necesita {rname} {rlvl} y hay {levels.get(rname,0)}"

    levels[tech] = lvl

# Al terminar: TODAS las tecnologías modeladas están a nivel >=1 (desbloqueadas).
for tech in gd.RESEARCH_COST:
    assert levels.get(tech, 0) >= 1, f"{tech} no quedó desbloqueada"

# Niveles intermedios exigidos por los prereqs para desbloquear plasma/hiperespacio/astro.
assert levels["energy_tech"] >= 8, levels["energy_tech"]
assert levels["laser_tech"] >= 10, levels["laser_tech"]
assert levels["ion_tech"] >= 5, levels["ion_tech"]
assert levels["shielding_tech"] >= 5, levels["shielding_tech"]
assert levels["hyperspace_tech"] >= 3, levels["hyperspace_tech"]
assert levels["espionage_tech"] >= 4, levels["espionage_tech"]
assert levels["impulse_drive"] >= 3, levels["impulse_drive"]
assert levels["computer_tech"] >= 5, levels["computer_tech"]   # slots de flota pronto

# Computación 5 debe alcanzarse PRONTO (antes de la mitad del plan), no al final.
order_techs2 = [t for t, _ in R.RESEARCH_UNLOCK_ORDER]
assert order_techs2.index("computer_tech") <= 2, order_techs2

# El laboratorio máximo que exige el plan es 7 (hiperespacio); nunca pide más.
assert lab == 7, lab
assert max(seen_lab_reqs) == 7

# next_unlock_research devuelve None con todo hecho; max_lab_needed cae a 0.
assert R.next_unlock_research(levels) is None
assert R.max_lab_needed(levels) == 0
# Con nada hecho, el plan necesitará hasta lab 7.
assert R.max_lab_needed({}) == 7

# Astrofísica (colonias) se desbloquea PRONTO, antes que plasma (crecimiento primero).
order_techs = [t for t, _ in R.RESEARCH_UNLOCK_ORDER]
assert order_techs.index("astrophysics") < order_techs.index("plasma_tech")

print(f"OK ({steps} pasos, lab final {lab})")
