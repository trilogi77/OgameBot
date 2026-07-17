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
from . import combat
from .models import Coords, Resources, Planet
from .config import Config


def cargos_needed(loot: Resources, ship: str = "large_cargo") -> int:
    cap = gd.SHIPS[ship].cargo
    return max(1, math.ceil(loot.total() / cap))


# Naves de guerra usables como cargueros de emergencia, por bodega descendente. Sustituto
# cuando no hay large_cargo/small_cargo suficientes. Se excluyen reciclador, colonizador,
# pathfinder, sondas y satélites porque el bot los reserva para otras tareas.
WARSHIP_CARGO_FALLBACK = ["reaper", "destroyer", "battleship", "cruiser",
                          "battlecruiser", "bomber", "heavy_fighter", "light_fighter"]


def pick_cargo_ships(available: Dict[str, int], cargo_needed: float,
                     allow_warships: bool = True) -> Dict[str, int]:
    """Elige cuántos cargueros (de los disponibles) llevan 'cargo_needed' de capacidad.

    Prioriza grandes y usa pequeños como complemento. Si no hay cargueros dedicados
    suficientes y allow_warships, rellena con naves de guerra (bodega descendente) como
    sustituto. Si aún así no cubre, devuelve lo que haya (envío parcial). {} si nada carga.
    """
    out: Dict[str, int] = {}
    remaining = cargo_needed
    order = ["large_cargo", "small_cargo"]
    if allow_warships:
        order += WARSHIP_CARGO_FALLBACK
    for ship in order:
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


# De más a menos potente: el greedy de auto_military_escort los prueba en este orden.
MILITARY_PRIORITY = ["battlecruiser", "destroyer", "battleship", "bomber",
                     "cruiser", "heavy_fighter", "light_fighter"]


def auto_military_escort(available: Dict[str, int], defender_fleet: Dict[str, int],
                         defender_defense: Dict[str, int], my_tech: combat.Tech,
                         def_tech: combat.Tech, win_threshold: float = 0.95,
                         runs: int = 15) -> Optional[Dict[str, int]]:
    """Elige por simulación la escolta militar para limpiar un objetivo defendido.

    Greedy por tipo (de más a menos potente): busca por bisección el mínimo de
    cada tipo que alcanza win_threshold junto a lo ya elegido; si un tipo entero
    no basta, lo añade completo y pasa al siguiente. Devuelve {} si el objetivo
    está indefenso y None si ni todo el hangar junto gana el combate.
    """
    if sum(defender_fleet.values()) + sum(defender_defense.values()) == 0:
        return {}

    def win_rate(fleet: Dict[str, int]) -> float:
        return combat.monte_carlo(fleet, my_tech, defender_fleet,
                                  defender_defense, def_tech, runs=runs)["win_rate"]

    escort: Dict[str, int] = {}
    for ship in MILITARY_PRIORITY:
        have = available.get(ship, 0)
        if have <= 0:
            continue
        if win_rate(dict(escort, **{ship: have})) < win_threshold:
            escort[ship] = have   # todo este tipo y aún no gana: seguir sumando tipos
            continue
        lo, hi = 1, have          # mínimo de este tipo que gana (con lo ya elegido)
        while lo < hi:
            mid = (lo + hi) // 2
            if win_rate(dict(escort, **{ship: mid})) >= win_threshold:
                hi = mid
            else:
                lo = mid + 1
        # ponytail: margen fijo x1.5 — el exceso reduce pérdidas propias; el óptimo
        # exacto dependería de distancia/combustible por objetivo.
        escort[ship] = min(have, math.ceil(lo * 1.5))
        return escort
    return None  # ni con todo el hangar se alcanza win_threshold


def size_attack_fleet_probes(planet: Planet, loot: Resources, template: Dict[str, int],
                             probe_cargo: int = 0) -> Dict[str, int]:
    """Dimensiona un raid con sondas de espionaje (servidores donde las sondas tienen bodega).
    Las sondas van SOLAS: se ignoran escoltas/otras naves del template y se escalan las sondas
    según el botín y las disponibles en el hangar. Devuelve {} si no hay sondas o bodega
    (cargo_capacity=0 -> se descarta luego en la evaluación)."""
    cap = probe_cargo if probe_cargo > 0 else gd.SHIPS["espionage_probe"].cargo
    avail = planet.ships.get("espionage_probe", 0)
    if cap <= 0 or avail <= 0:
        return {}
    needed = math.ceil(loot.total() / cap) if loot.total() > 0 else 1
    return {"espionage_probe": max(1, min(avail, needed))}


def fleetsave_plan(origin: Coords, all_planets: List[Coords], cfg: Config,
                   offline_hours: float = 8.0,
                   fleet_ships: Optional[Dict[str, int]] = None,
                   research_levels: Optional[dict] = None) -> Optional[dict]:
    """
    Devuelve un plan de fleetsave que retorna pasado ~offline_hours.
    Estrategia: 'deploy' a tu planeta/luna al % de velocidad adecuado.
    """
    if not cfg.enable_fleetsave:
        return None

    # Excluir solo la ubicación EXACTA (misma tupla Y mismo tipo): la luna del
    # propio planeta comparte tupla g,s,p y es el mejor destino (distancia 5).
    origin_type = getattr(origin, "type", "planet")
    candidates = [p for p in all_planets
                  if not (p.tuple() == origin.tuple()
                          and getattr(p, "type", "planet") == origin_type)]
    if not candidates:
        # fallback: expedición (16ª posición del propio sistema), vuelve sola
        exp_dest = Coords(origin.galaxy, origin.system, 16)
        return {"mission": "expedition", "destination": exp_dest,
                "speed_percent": 1.0, "hold_hours": min(offline_hours, 1.0),
                "fallback": "expedition", "phalanx_exposed": True}

    # Si se conecta a mitad de la noche para retornar los despliegues,
    # el tiempo de ida debe ser al menos la mitad del offline_hours.
    recall_halfway = getattr(cfg, "fleetsave_recall_halfway", False)
    if recall_halfway:
        target_t = (offline_hours / 2.0) * 3600
    else:
        target_t = offline_hours * 3600

    # Velocidad de la nave más lenta de la flota real; sin flota, small_cargo.
    if fleet_ships:
        slowest = min((gd.effective_speed(n, research_levels)
                       for n, q in fleet_ships.items()
                       if q > 0 and n in gd.SHIPS and gd.effective_speed(n, research_levels) > 0),
                      default=gd.effective_speed("small_cargo", research_levels))
    else:
        slowest = gd.effective_speed("small_cargo", research_levels)

    if cfg.fleetsave_mission == "deploy":
        options = []
        for dest in candidates:
            dist = gd.distance(origin.tuple(), dest.tuple())
            for sp in (1.0, 0.5, 0.4, 0.3, 0.2, 0.1):
                t = gd.flight_time(dist, slowest, sp, cfg.fleet_speed)
                options.append({
                    "destination": dest,
                    "speed_percent": sp,
                    "flight_s": t
                })

        # Buscar combinaciones que duren al menos target_t
        valid_options = [opt for opt in options if opt["flight_s"] >= target_t]

        # Preferir destinos LUNA: una luna no se puede escanear con sensor phalanx,
        # así que esconde mejor la flota (luna->luna mejor que luna->planeta).
        # Si el origen ya es una luna, luna->luna es totalmente invisible: forzar preferencia.
        prefer_moon = getattr(cfg, "fleetsave_prefer_moon", True) or origin_type == "moon"
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
            "flight_s": best["flight_s"],
            "phalanx_exposed": getattr(best["destination"], "type", "planet") != "moon"
        }

    # fallback para otras misiones (expedición / transport)
    exp_dest = Coords(origin.galaxy, origin.system, 16)
    return {"mission": "expedition", "destination": exp_dest,
            "speed_percent": 1.0, "hold_hours": min(offline_hours, 1.0),
            "fallback": "expedition", "phalanx_exposed": True}


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
                             safety: float = 1.0, hyperspace_level: int = 0) -> int:
    """
    Nº de naves de carga necesarias para sostener el botín máximo sin perder
    recursos. Más cargueros que esto no es rentable (capacidad ociosa).
    La bodega se ajusta por el nivel de Hiperespacio (+5%/nivel).
    """
    cap = gd.effective_cargo(cargo_ship, hyperspace_level)
    if cap <= 0:
        cap = gd.effective_cargo("large_cargo", hyperspace_level)
    if cap <= 0:
        return 1
    target = max_find_units * max(safety, 0.0)
    return max(1, math.ceil(target / cap))


def recycler_count(debris: Dict[str, float], hyperspace_level: int = 0) -> int:
    total = debris.get("metal", 0) + debris.get("crystal", 0) + debris.get("deut", 0)
    # Bodega REAL con Hiperespacio (+5%/nivel). Con la base se enviaban hasta ~43% más
    # recicladores de los necesarios (Hiperespacio 15), bloqueando naves para el siguiente campo.
    cap = gd.effective_cargo("recycler", hyperspace_level)
    return max(1, math.ceil(total / cap))


def harvest_plan(origin: Coords, debris_coords: Coords,
                 debris: Dict[str, float], hyperspace_level: int = 0) -> dict:
    n = recycler_count(debris, hyperspace_level)
    dest = Coords(debris_coords.galaxy, debris_coords.system, debris_coords.position, type="debris")
    return {"mission": "harvest", "origin": origin, "destination": dest,
            "ships": {"recycler": n}}


def auto_fleet_targets(home: Planet, planets: List[Planet],
                       research_levels: Dict[str, int], cfg: Config,
                       expe_cargo_total: int = 0,
                       startup_phase: bool = False) -> Dict[str, int]:
    """Auto-gestión de flota: objetivos calculados según el tamaño de la economía.

    La escala es la suma de niveles de minas (metal+cristal) del imperio: más
    economía -> más cargueros para farmear/transportar y más escolta militar.
    `cfg.fleet_priority` inclina los ratios: "economy" (cargueros), "military"
    (escolta x2) o "expeditions" (cargueros para llenar TODOS los slots de
    expedición al óptimo —`expe_cargo_total`, lo calcula el brain— más margen
    para mover recursos). Solo incluye naves cuyos prerrequisitos ya se cumplen
    en `home`. El orden del dict es la prioridad de fabricación: el bot fabrica
    un lote por ciclo del primer déficit, igual que con los objetivos manuales.

    `startup_phase` (fase de arranque: el árbol de investigación/flotas aún no
    está desbloqueado): la flota MILITAR (cazadores/cruceros/acorazados) es gasto
    puro que roba el metal que la investigación cara del final del desbloqueo
    necesita ahorrar (`_fleet_step` solo reserva la próxima construcción de
    economía, no los ahorros de investigación), y alarga el arranque. Durante esta
    fase solo fabricamos lo justo para seguir haciendo EXPEDICIONES (cargueros +
    sondas), que sí rentan; el farmeo militar y la disuasión esperan a completar
    el desbloqueo.
    """
    def buildable(name: str) -> bool:
        return all(
            (home.lvl(req) if req in gd.BUILDING_COST else research_levels.get(req, 0)) >= lvl
            for req, lvl in gd.SHIP_PREREQS.get(name, {}).items())

    if startup_phase:
        targets: Dict[str, int] = {}
        if getattr(cfg, "enable_expeditions", True):
            cargo_ship = getattr(cfg, "expedition_cargo_ship", "large_cargo") or "large_cargo"
            if buildable(cargo_ship):
                targets[cargo_ship] = max(20, expe_cargo_total or 20)
            else:
                targets["small_cargo"] = 15  # puente hasta desbloquear el carguero grande
            if getattr(cfg, "expedition_use_pathfinder", False):
                astro = research_levels.get("astrophysics", 0)
                targets["pathfinder"] = max(1, gd.expedition_slots(astro))
        if getattr(cfg, "enable_farming", True) or getattr(cfg, "enable_spy_watch", False):
            targets["espionage_probe"] = 12
        if getattr(cfg, "enable_colonization", False) and len(planets) < getattr(cfg, "max_colonies", 1):
            targets["colony_ship"] = 1
        return {s: q for s, q in targets.items() if q > 0 and buildable(s)}

    # ponytail: heurística lineal con las minas; afinar ratios si el crecimiento cojea
    eco = sum(p.lvl("metal_mine") + p.lvl("crystal_mine") for p in planets)
    priority = (getattr(cfg, "fleet_priority", "economy") or "economy").lower()
    if priority == "military":
        cargo, lf, cr, bs = eco, eco * 4, eco, eco // 2
    elif priority == "expeditions":
        # Flota óptima para todas las expediciones + cargueros extra para mover recursos
        cargo, lf, cr, bs = expe_cargo_total + eco, eco, eco // 4, eco // 8
    else:  # economy
        cargo, lf, cr, bs = eco * 2, eco * 2, eco // 2, eco // 4

    targets: Dict[str, int] = {}
    if getattr(cfg, "enable_farming", True):
        targets["espionage_probe"] = 12
    cargo_ship = "large_cargo"
    if priority == "expeditions":
        cargo_ship = getattr(cfg, "expedition_cargo_ship", "large_cargo") or "large_cargo"
    if buildable(cargo_ship):
        targets[cargo_ship] = max(20, cargo)
    else:
        targets["small_cargo"] = 15  # puente hasta desbloquear el carguero grande
    if getattr(cfg, "enable_recycling", False) or getattr(cfg, "farm_recycle_debris", False):
        targets["recycler"] = max(4, eco // 3)
    # Colonizar necesita una nave de colonización en el hangar
    if getattr(cfg, "enable_colonization", False) and len(planets) < getattr(cfg, "max_colonies", 1):
        targets["colony_ship"] = 1
    # Pathfinder: 1 por slot de expedición cuando se usa para explorar (duplica hallazgos)
    if getattr(cfg, "expedition_use_pathfinder", False):
        astro = research_levels.get("astrophysics", 0)
        expe_slots = max(1, gd.expedition_slots(astro))
        targets["pathfinder"] = expe_slots
    # Escolta militar (la usa farm_auto_fleet) y disuasión; crece con la economía
    targets["light_fighter"] = lf
    targets["cruiser"] = cr
    targets["battleship"] = bs
    return {s: q for s, q in targets.items() if q > 0 and buildable(s)}
