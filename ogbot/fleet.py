"""
fleet.py
========
Lógica de flota (pura, sin tocar el navegador):
 - fleetsave: calcular un movimiento seguro para que la flota y/o recursos no
   estén en el planeta cuando estés offline (es LA mecánica de supervivencia).
 - dimensionar cargueros para un saqueo.
 - planificar expediciones.
 - planificar reciclaje de campos de escombros.
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple
from . import gamedata as gd
from .models import Coords, Resources, Planet
from .config import Config


def cargos_needed(loot: Resources, ship: str = "large_cargo") -> int:
    cap = gd.SHIPS[ship].cargo
    return max(1, math.ceil(loot.total() / cap))


def pick_cargo_ships(available: Dict[str, int], cargo_needed: float) -> Dict[str, int]:
    """Elige cuántos cargueros (de los disponibles) llevan 'cargo_needed' de capacidad.

    Prioriza grandes y usa pequeños como complemento. Si no hay capacidad suficiente,
    devuelve todos los cargueros disponibles (envío parcial). {} si no hay cargueros.
    """
    out: Dict[str, int] = {}
    remaining = cargo_needed
    for ship in ("large_cargo", "small_cargo"):
        have = available.get(ship, 0)
        cap = gd.SHIPS[ship].cargo if ship in gd.SHIPS else 0
        if have <= 0 or cap <= 0:
            continue
        use = min(have, math.ceil(remaining / cap)) if remaining > 0 else 0
        if use > 0:
            out[ship] = use
            remaining -= use * cap
        if remaining <= 0:
            break
    return out


def size_attack_fleet(loot: Resources, template: Dict[str, int]) -> Dict[str, int]:
    """Ajusta el nº de cargueros del template para llevar todo el loot."""
    fleet = dict(template)
    cargo_cap = sum(gd.SHIPS[n].cargo * q for n, q in fleet.items() if n in gd.SHIPS)
    if cargo_cap >= loot.total():
        return {k: v for k, v in fleet.items() if v > 0}
    
    # Decidir qué tipo de carguero escalar según el template
    cargo_ship = "large_cargo"
    if "small_cargo" in fleet and fleet["small_cargo"] > 0 and fleet.get("large_cargo", 0) == 0:
        cargo_ship = "small_cargo"
        
    deficit = loot.total() - cargo_cap
    extra = math.ceil(deficit / gd.SHIPS[cargo_ship].cargo)
    fleet[cargo_ship] = fleet.get(cargo_ship, 0) + extra
    return {k: v for k, v in fleet.items() if v > 0}


def size_attack_fleet_for_planet(planet: Planet, loot: Resources, template: Dict[str, int]) -> Dict[str, int]:
    """
    Dimensiona la flota de ataque para llevar el loot de forma dinámica.
    - Mantiene escoltas/miliares del template.
    - Calcula la cantidad de cargueros estrictamente necesaria basándose en el botín,
      escalando y combinando small_cargo y large_cargo de forma inteligente según hangar.
    """
    # 1. Copiar escoltas del template (naves no cargueras)
    fleet = {k: v for k, v in template.items() if k not in ("small_cargo", "large_cargo") and v > 0}
    
    # 2. Determinar la capacidad de carga requerida
    loot_total = loot.total()
    
    # 3. Obtener naves disponibles en el planeta
    avail_large = planet.ships.get("large_cargo", 0)
    avail_small = planet.ships.get("small_cargo", 0)
    
    # 4. Ver preferencia del usuario según el template
    pref_large = template.get("large_cargo", 0)
    pref_small = template.get("small_cargo", 0)
    
    # Si el usuario explícitamente configuró sólo un tipo de carguero en el template, respetamos esa preferencia.
    # Si configuró ambos o ninguno, preferimos usar lo disponible en el planeta.
    use_large = True
    use_small = True
    if pref_large > 0 and pref_small == 0:
        use_small = False
    elif pref_small > 0 and pref_large == 0:
        use_large = False
        
    # Calcular capacidades unitarias
    cap_large = gd.SHIPS["large_cargo"].cargo
    cap_small = gd.SHIPS["small_cargo"].cargo
    
    needed_large = 0
    needed_small = 0
    
    # Si preferimos large_cargo o si ambos están activos, intentamos cubrir con large_cargo primero
    if use_large and avail_large > 0 and loot_total > 0:
        max_large_needed = math.ceil(loot_total / cap_large)
        needed_large = min(max_large_needed, avail_large)
        loot_total -= needed_large * cap_large
        
    # Si todavía queda botín por cargar y podemos usar small_cargo
    if use_small and avail_small > 0 and loot_total > 0:
        max_small_needed = math.ceil(loot_total / cap_small)
        needed_small = min(max_small_needed, avail_small)
        loot_total -= needed_small * cap_small
        
    # Si todavía queda botín (por escasez del carguero preferido) y el otro está desactivado por preferencia
    # del template, pero tenemos en el hangar, lo usamos como último recurso de respaldo:
    if loot_total > 0:
        if not use_large and pref_small > 0 and avail_large > 0:
            max_large_needed = math.ceil(loot_total / cap_large)
            extra_large = min(max_large_needed, avail_large)
            needed_large += extra_large
            loot_total -= extra_large * cap_large
            
        if not use_small and pref_large > 0 and avail_small > 0 and loot_total > 0:
            max_small_needed = math.ceil(loot_total / cap_small)
            extra_small = min(max_small_needed, avail_small)
            needed_small += extra_small
            loot_total -= extra_small * cap_small

    if needed_large > 0:
        fleet["large_cargo"] = needed_large
    if needed_small > 0:
        fleet["small_cargo"] = needed_small
        
    return fleet


def size_attack_fleet_probes(planet: Planet, loot: Resources, template: Dict[str, int],
                             probe_cargo: int = 0) -> Dict[str, int]:
    """Dimensiona un raid con sondas de espionaje (servidores donde las sondas tienen bodega).
    Mantiene las escoltas del template y escala las sondas según el botín y las disponibles
    en el hangar. Devuelve solo escoltas si no hay sondas o bodega (cargo_capacity=0 -> se
    descarta luego en la evaluación)."""
    cap = probe_cargo if probe_cargo > 0 else gd.SHIPS["espionage_probe"].cargo
    fleet = {k: v for k, v in template.items() if k != "espionage_probe" and v > 0}  # escoltas
    avail = planet.ships.get("espionage_probe", 0)
    if cap <= 0 or avail <= 0:
        return fleet
    needed = math.ceil(loot.total() / cap) if loot.total() > 0 else 1
    fleet["espionage_probe"] = max(1, min(avail, needed))
    return fleet


def fleetsave_plan(origin: Coords, all_planets: List[Coords], cfg: Config,
                   offline_hours: float = 8.0) -> Optional[dict]:
    """
    Devuelve un plan de fleetsave que retorna pasado ~offline_hours.
    Estrategia: 'deploy' a tu planeta/luna al % de velocidad adecuado.
    """
    if not cfg.enable_fleetsave:
        return None

    candidates = [p for p in all_planets if p.tuple() != origin.tuple()]
    if not candidates:
        # fallback: expedición (16ª posición del propio sistema), vuelve sola
        exp_dest = Coords(origin.galaxy, origin.system, 16)
        return {"mission": "expedition", "destination": exp_dest,
                "speed_percent": 1.0, "hold_hours": min(offline_hours, 1.0)}

    # Si se conecta a mitad de la noche para retornar los despliegues,
    # el tiempo de ida debe ser al menos la mitad del offline_hours.
    recall_halfway = getattr(cfg, "fleetsave_recall_halfway", False)
    if recall_halfway:
        target_t = (offline_hours / 2.0) * 3600
    else:
        target_t = offline_hours * 3600

    if cfg.fleetsave_mission == "deploy":
        options = []
        for dest in candidates:
            dist = gd.distance(origin.tuple(), dest.tuple())
            for sp in (1.0, 0.5, 0.4, 0.3, 0.2, 0.1):
                t = gd.flight_time(dist, gd.SHIPS["small_cargo"].speed, sp, cfg.fleet_speed)
                options.append({
                    "destination": dest,
                    "speed_percent": sp,
                    "flight_s": t
                })

        # Buscar combinaciones que duren al menos target_t
        valid_options = [opt for opt in options if opt["flight_s"] >= target_t]

        # Preferir destinos LUNA: una luna no se puede escanear con sensor phalanx,
        # así que esconde mejor la flota (luna->luna mejor que luna->planeta).
        prefer_moon = getattr(cfg, "fleetsave_prefer_moon", True)
        moons_of = lambda lst: [o for o in lst if getattr(o["destination"], "type", "planet") == "moon"]

        if prefer_moon and moons_of(valid_options):
            # Mejor luna válida (la más corta que aún dure >= target_t)
            best = min(moons_of(valid_options), key=lambda opt: opt["flight_s"])
        elif valid_options:
            # Cualquier destino válido (el más corto que dure >= target_t)
            best = min(valid_options, key=lambda opt: opt["flight_s"])
        elif prefer_moon and moons_of(options):
            # Ninguna llega al objetivo: la luna más larga disponible
            best = max(moons_of(options), key=lambda opt: opt["flight_s"])
        else:
            # Si ninguna opción es lo suficientemente larga, elegir la más larga de todas
            best = max(options, key=lambda opt: opt["flight_s"])

        return {
            "mission": "deploy",
            "destination": best["destination"],
            "speed_percent": best["speed_percent"],
            "flight_s": best["flight_s"]
        }

    # fallback para otras misiones (expedición / transport)
    exp_dest = Coords(origin.galaxy, origin.system, 16)
    return {"mission": "expedition", "destination": exp_dest,
            "speed_percent": 1.0, "hold_hours": min(offline_hours, 1.0)}


def expedition_plan(home: Coords, cfg: Config, destination: Optional[Coords] = None,
                    ships: Optional[Dict[str, int]] = None) -> dict:
    dest = destination or Coords(home.galaxy, home.system, cfg.expedition_position)
    fleet = dict(ships) if ships else dict(cfg.expedition_ships)
    return {"mission": "expedition", "destination": dest,
            "ships": fleet,
            "hold_hours": float(getattr(cfg, "expedition_hold_hours", 1.0))}


def expedition_rotation_system(home_system: int, system_range: int, index: int) -> int:
    """
    Sistema destino rotado alrededor de home_system para que las expediciones no
    agoten siempre el mismo sistema. Recorre 0, +1, -1, +2, -2, ... dentro de
    +/- system_range, envolviendo dentro del rango válido de sistemas (1..499).
    """
    if system_range <= 0:
        return home_system
    span = 2 * system_range + 1
    i = index % span
    if i == 0:
        offset = 0
    elif i % 2 == 1:
        offset = (i + 1) // 2
    else:
        offset = -(i // 2)
    return ((home_system + offset - 1) % 499) + 1


def optimal_expedition_cargo(max_find_units: int, cargo_ship: str = "large_cargo",
                             safety: float = 1.0) -> int:
    """
    Nº de naves de carga necesarias para sostener el botín máximo sin perder
    recursos. Más cargueros que esto no es rentable (capacidad ociosa).
    """
    cap = gd.SHIPS[cargo_ship].cargo if cargo_ship in gd.SHIPS else gd.SHIPS["large_cargo"].cargo
    if cap <= 0:
        return 1
    target = max_find_units * max(safety, 0.0)
    return max(1, math.ceil(target / cap))


def recycler_count(debris: Dict[str, float]) -> int:
    total = debris.get("metal", 0) + debris.get("crystal", 0) + debris.get("deut", 0)
    cap = gd.SHIPS["recycler"].cargo
    return max(1, math.ceil(total / cap))


def harvest_plan(origin: Coords, debris_coords: Coords,
                 debris: Dict[str, float]) -> dict:
    n = recycler_count(debris)
    dest = Coords(debris_coords.galaxy, debris_coords.system, debris_coords.position, type="debris")
    return {"mission": "harvest", "origin": origin, "destination": dest,
            "ships": {"recycler": n}}
