"""
research.py
===========
Gestor de investigación. Recorre la lista de prioridad de config y devuelve la
próxima tecnología a investigar que sea asequible y cuyos prerequisitos estén
cubiertos. La investigación es global a la cuenta.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple
from . import gamedata as gd
from .models import Resources, Planet
from .config import Config


# Orden para DESBLOQUEAR todo el árbol de investigación lo antes posible (sin graviton ni
# la red intergaláctica, que exige hyperspace 8 / computer 8 y frenaría el crecimiento).
# Objetivos por hito (acumulativos): al alcanzar el último, todas las tecnologías están a
# nivel >=1 y todas las naves/defensas quedan disponibles. Prioriza crecimiento (energía,
# computación, ASTROFÍSICA=colonias) y deja el grind caro (láser 10 / ion 5 / energía 8 ->
# plasma) al final. Prerrequisitos verificados contra el árbol de OGame (gamedata).
RESEARCH_UNLOCK_ORDER: List[Tuple[str, int]] = [
    ("energy_tech", 1),
    ("computer_tech", 1),      # +1 slot de flota; barato
    ("combustion_drive", 1),
    ("energy_tech", 2),
    ("laser_tech", 1),
    ("armor_tech", 1),
    ("impulse_drive", 1),
    ("espionage_tech", 4),     # para astrofísica
    ("impulse_drive", 3),      # para astrofísica
    ("astrophysics", 1),       # COLONIAS: crecimiento, desbloqueado pronto
    ("energy_tech", 3),        # para escudos
    ("weapons_tech", 1),       # lab 4
    ("laser_tech", 5),         # para ion
    ("energy_tech", 4),        # para ion
    ("ion_tech", 1),           # lab 4
    ("shielding_tech", 5),     # lab 6; para hiperespacio
    ("energy_tech", 5),        # para hiperespacio
    ("hyperspace_tech", 3),    # lab 7; para motor hiperespacial
    ("hyperspace_drive", 1),   # lab 7
    ("laser_tech", 10),        # para plasma
    ("ion_tech", 5),           # para plasma
    ("energy_tech", 8),        # para plasma
    ("plasma_tech", 1),        # último desbloqueo
]


def next_unlock_research(research: Dict[str, int]) -> Optional[Tuple[str, int]]:
    """Siguiente (tech, nivel_a_investigar) del plan de desbloqueo, o None si ya está todo.
    Devuelve el nivel inmediato (actual+1) del primer hito no alcanzado."""
    for tech, target in RESEARCH_UNLOCK_ORDER:
        cur = research.get(tech, 0)
        if cur < target:
            return tech, cur + 1
    return None


def max_lab_needed(research: Dict[str, int]) -> int:
    """Nivel de laboratorio máximo que exigen los hitos de desbloqueo AÚN pendientes.
    Sirve para ir subiendo el laboratorio en los ratos sin recursos, sin pasarse."""
    need = 0
    for tech, target in RESEARCH_UNLOCK_ORDER:
        if research.get(tech, 0) < target:
            need = max(need, gd.RESEARCH_LAB_REQ.get(tech, 0))
    return need


def next_research(
    research: Dict[str, int],
    planet: Planet,
    cfg: Config
) -> Optional[Tuple[str, gd.Cost, Optional[int]]]:
    """
    Devuelve (nombre_tech, coste, req_lab_lvl) de la próxima investigación recomendada,
    o None si estamos esperando/ahorrando.
    Si req_lab_lvl no es None, significa que estamos bloqueados esperando ese nivel de laboratorio.
    """
    from .economy import time_to_accumulate

    # 1. Obtener pesos y límites de la configuración
    weights = getattr(cfg, "research_weights", {
        "astrophysics": 2.0,
        "plasma_tech": 1.8,
        "computer_tech": 1.5,
        "combustion_drive": 1.2,
        "impulse_drive": 1.1,
        "hyperspace_drive": 1.0,
        "espionage_tech": 1.0,
        "weapons_tech": 1.0,
        "shielding_tech": 1.0,
        "armor_tech": 1.0,
        "hyperspace_tech": 0.9,
        "energy_tech": 0.5,
        "laser_tech": 0.5,
        "ion_tech": 0.5,
    })
    
    caps = dict(getattr(cfg, "research_caps", {
        "laser_tech": 12,
        "ion_tech": 5,
        "energy_tech": 8,
        "hyperspace_tech": 15,
    }))
    
    # Si habilitamos reactor de fusión, el límite de energía aumenta
    if cfg.enable_fusion_reactor:
        caps["energy_tech"] = max(caps.get("energy_tech", 8), 20)

    plasma = research.get("plasma_tech", 0)
    buf = 1 - cfg.keep_resources_buffer
    avail = Resources(planet.resources.metal * buf,
                      planet.resources.crystal * buf,
                      planet.resources.deut * buf)

    max_wait = getattr(cfg, "max_saving_hours_research", 6.0)
    ratio = getattr(cfg, "trade_ratio", (2.5, 1.5, 1.0))

    # Helper recursivo para obtener el siguiente paso inmediato
    def get_next_steps(tech_name, target_lvl, visited=None):
        if visited is None:
            visited = set()
        key = (tech_name, target_lvl)
        if key in visited:
            return []
        visited.add(key)

        current_lvl = research.get(tech_name, 0)
        if current_lvl >= target_lvl:
            return []

        # Comprobar laboratorio en este planeta
        lab_req = gd.RESEARCH_LAB_REQ.get(tech_name, 0)
        if planet.lvl("research_lab") < lab_req:
            # Requerimos subir laboratorio primero
            return [("building", "research_lab", planet.lvl("research_lab") + 1)]

        # Comprobar otros requisitos
        prereqs = gd.RESEARCH_PREREQS.get(tech_name, {})
        missing_prereqs = []
        for req_name, req_lvl in prereqs.items():
            req_current = research.get(req_name, 0)
            if req_current < req_lvl:
                missing_prereqs.append((req_name, req_current + 1))

        if missing_prereqs:
            # Si hay prerrequisitos sin cumplir, devolvemos sus pasos inmediatos
            steps = []
            for m_name, m_lvl in missing_prereqs:
                steps.extend(get_next_steps(m_name, m_lvl, visited))
            return steps

        # Si no hay prerrequisitos pendientes, el paso es la tecnología misma
        return [("research", tech_name, target_lvl)]

    candidates = {}
    for tech in cfg.research_priority:
        if tech not in gd.RESEARCH_COST:
            continue

        # Verificar si se alcanzó el límite voluntario de utilidad.
        # Un cap 0 o vacío (la GUI puede guardar 0) significa "sin límite".
        cap = caps.get(tech)
        current_lvl = research.get(tech, 0)
        if cap is not None and cap > 0 and current_lvl >= cap:
            continue

        target_lvl = current_lvl + 1
        steps = get_next_steps(tech, target_lvl)

        for step in steps:
            if step[0] == "research":
                r_name, r_lvl = step[1], step[2]
                if (r_name, r_lvl) not in candidates:
                    cost = gd.research_cost(r_name, r_lvl)
                    cost_val = cost.total_value(ratio)
                    w = weights.get(r_name, 1.0)
                    weighted_cost = cost_val / w
                    candidates[(r_name, r_lvl)] = (cost, weighted_cost, None)
            elif step[0] == "building":
                # Bloqueado por laboratorio de investigación
                b_name, b_lvl = step[1], step[2]
                cost = gd.research_cost(tech, target_lvl)
                cost_val = cost.total_value(ratio)
                w = weights.get(tech, 1.0)
                weighted_cost = cost_val / w
                candidates[(tech, target_lvl)] = (cost, weighted_cost, b_lvl)

    # Ordenar candidatos por coste ponderado (de menor a mayor)
    sorted_candidates = sorted(candidates.items(), key=lambda x: x[1][1])

    # 1ª pasada: preferir investigación DISPONIBLE (no bloqueada por laboratorio). Así el
    # bot hace lo que ya puede (astrofísica, computación, motores…) en vez de fijarse en
    # una tecnología barata pero bloqueada (p.ej. armas 1 pidiendo laboratorio 4) y no
    # investigar nada. La bloqueada más barata que sí nos podríamos permitir se recuerda
    # como plan B: subir el laboratorio para desbloquearla.
    blocked_fallback = None
    for (r_name, r_lvl), (cost, w_cost, lab_lvl) in sorted_candidates:
        if lab_lvl is not None:
            if blocked_fallback is None and avail.can_afford(cost):
                blocked_fallback = (r_name, cost, lab_lvl)
            continue
        if avail.can_afford(cost):
            return r_name, cost, None
        # Disponible pero sin recursos: ahorrar si llega pronto (no adelantamos el lab).
        t = time_to_accumulate(cost, planet, cfg, plasma)
        if t <= max_wait:
            return None

    # 2ª pasada: no hay investigación disponible que hacer ahora -> desbloquear el
    # laboratorio para la bloqueada más barata (el brain subirá el laboratorio).
    return blocked_fallback

