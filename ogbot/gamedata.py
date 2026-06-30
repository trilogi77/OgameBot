"""
gamedata.py
===========
Fórmulas y datos base de OGame. Todo está parametrizado para que puedas ajustar
los valores a la economía/velocidad de TU universo (los universos varían).

IMPORTANTE: las fórmulas de producción, coste, velocidad y combustible siguen las
del motor estándar de OGame. Si tu universo usa multiplicadores especiales
(p.ej. economía x6, debris 40%, deuterio en escombros, etc.) ajústalos en config.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Dict


# ---------------------------------------------------------------------------
# COSTES DE EDIFICIOS / INVESTIGACIONES
#   coste(level) = base * factor ** (level - 1)   (para subir AL nivel `level`)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Cost:
    metal: float = 0.0
    crystal: float = 0.0
    deut: float = 0.0
    energy: float = 0.0  # para algunas defensas/edificios

    def scaled(self, factor: float, level: int) -> "Cost":
        m = factor ** (level - 1)
        return Cost(self.metal * m, self.crystal * m, self.deut * m, self.energy)

    def total_value(self, ratio=(2.5, 1.5, 1.0)) -> float:
        """Valor unificado en 'metal equivalente' usando ratios de mercado."""
        return self.metal + self.crystal * (ratio[0] / ratio[1]) + self.deut * (ratio[0] / ratio[2])


# base, factor de crecimiento
BUILDING_COST = {
    "metal_mine":        (Cost(60, 15), 1.5),
    "crystal_mine":      (Cost(48, 24), 1.6),
    "deut_synth":        (Cost(225, 75), 1.5),
    "solar_plant":       (Cost(75, 30), 1.5),
    "fusion_reactor":    (Cost(900, 360, 180), 1.8),
    "robotics_factory":  (Cost(400, 120, 200), 2.0),
    "nanite_factory":    (Cost(1_000_000, 500_000, 100_000), 2.0),
    "shipyard":          (Cost(400, 200, 100), 2.0),
    "research_lab":      (Cost(200, 400, 200), 2.0),
    "metal_storage":     (Cost(1000, 0), 2.0),
    "crystal_storage":   (Cost(1000, 500), 2.0),
    "deut_tank":         (Cost(1000, 1000), 2.0),
}

RESEARCH_COST = {
    "energy_tech":       (Cost(0, 800, 400), 2.0),
    "laser_tech":        (Cost(200, 100), 2.0),
    "ion_tech":          (Cost(1000, 300, 100), 2.0),
    "hyperspace_tech":   (Cost(0, 4000, 2000), 2.0),
    "plasma_tech":       (Cost(2000, 4000, 1000), 2.0),
    "combustion_drive":  (Cost(400, 0, 600), 2.0),
    "impulse_drive":     (Cost(2000, 4000, 600), 2.0),
    "hyperspace_drive":  (Cost(10000, 20000, 6000), 2.0),
    "espionage_tech":    (Cost(200, 1000, 200), 2.0),
    "computer_tech":     (Cost(0, 400, 600), 2.0),
    "astrophysics":      (Cost(4000, 8000, 4000), 1.75),
    "weapons_tech":      (Cost(800, 200), 2.0),
    "shielding_tech":    (Cost(200, 600), 2.0),
    "armor_tech":        (Cost(1000, 0), 2.0),
}


# --- PRERREQUISITOS DE EDIFICIOS E INVESTIGACIONES ---
RESEARCH_LAB_REQ = {
    "energy_tech": 1,
    "laser_tech": 1,
    "ion_tech": 4,
    "hyperspace_tech": 7,
    "plasma_tech": 4,
    "combustion_drive": 1,
    "impulse_drive": 2,
    "hyperspace_drive": 7,
    "espionage_tech": 3,
    "computer_tech": 1,
    "astrophysics": 3,
    "weapons_tech": 4,
    "shielding_tech": 6,
    "armor_tech": 2,
}

BUILDING_PREREQS = {
    "crystal_mine": {"metal_mine": 1},
    "deut_synth": {"metal_mine": 1, "crystal_mine": 1},
    "fusion_reactor": {"deut_synth": 5, "energy_tech": 3},
    "robotics_factory": {},
    "shipyard": {"robotics_factory": 2},
    "research_lab": {},
    "nanite_factory": {"robotics_factory": 10, "computer_tech": 10},
}

RESEARCH_PREREQS = {
    "energy_tech": {},
    "laser_tech": {"energy_tech": 2},
    "ion_tech": {"laser_tech": 5, "energy_tech": 4},
    "hyperspace_tech": {"energy_tech": 5, "shielding_tech": 5},
    "plasma_tech": {"energy_tech": 8, "laser_tech": 10, "ion_tech": 5},
    "combustion_drive": {"energy_tech": 1},
    "impulse_drive": {"energy_tech": 1},
    "hyperspace_drive": {"hyperspace_tech": 3},
    "espionage_tech": {},
    "computer_tech": {},
    "astrophysics": {"espionage_tech": 4, "impulse_drive": 3},
    "weapons_tech": {},
    "shielding_tech": {"energy_tech": 3},
    "armor_tech": {},
}

SHIP_PREREQS = {
    "small_cargo": {"shipyard": 2, "combustion_drive": 2},
    "large_cargo": {"shipyard": 4, "combustion_drive": 6},
    "light_fighter": {"shipyard": 1, "combustion_drive": 1},
    "espionage_probe": {"shipyard": 3, "espionage_tech": 3},
    "recycler": {"shipyard": 4, "combustion_drive": 6, "armor_tech": 2},
    "colony_ship": {"shipyard": 4, "impulse_drive": 3},
    "heavy_fighter": {"shipyard": 3, "impulse_drive": 2},
    "cruiser": {"shipyard": 5, "impulse_drive": 4, "ion_tech": 2},
    "battleship": {"shipyard": 7, "hyperspace_drive": 4},
    "battlecruiser": {"shipyard": 8, "hyperspace_drive": 5, "hyperspace_tech": 5, "laser_tech": 12},
    "bomber": {"shipyard": 8, "impulse_drive": 6, "plasma_tech": 5},
    "destroyer": {"shipyard": 9, "hyperspace_drive": 6, "hyperspace_tech": 5},
    "deathstar": {"shipyard": 12, "hyperspace_drive": 7, "hyperspace_tech": 6},
}


def building_cost(name: str, level: int) -> Cost:
    base, factor = BUILDING_COST[name]
    return base.scaled(factor, level)


def research_cost(name: str, level: int) -> Cost:
    base, factor = RESEARCH_COST[name]
    return base.scaled(factor, level)


def defense_cost(name: str, amount: int = 1) -> Cost:
    """Coste de `amount` unidades de una estructura defensiva."""
    u = DEFENSES[name]
    return Cost(u.cost.metal * amount, u.cost.crystal * amount, u.cost.deut * amount)


# ---------------------------------------------------------------------------
# PRODUCCIÓN POR HORA (antes del factor de velocidad del universo)
# ---------------------------------------------------------------------------
def metal_production(level: int, plasma: int = 0, speed: float = 1.0,
                     efficiency: float = 1.0) -> float:
    base = 30  # producción base del planeta
    mine = 30 * level * (1.1 ** level) * efficiency
    bonus = mine * (0.01 * plasma)          # +1% por nivel de plasma
    return (base + mine + bonus) * speed


def crystal_production(level: int, plasma: int = 0, speed: float = 1.0,
                       efficiency: float = 1.0) -> float:
    base = 15
    mine = 20 * level * (1.1 ** level) * efficiency
    bonus = mine * (0.0066 * plasma)        # +0.66% por nivel de plasma
    return (base + mine + bonus) * speed


def deut_production(level: int, max_temp: int, plasma: int = 0, speed: float = 1.0,
                    efficiency: float = 1.0) -> float:
    mine = 10 * level * (1.1 ** level) * (1.44 - 0.004 * max_temp) * efficiency
    bonus = mine * (0.0033 * plasma)        # +0.33% por nivel de plasma
    return max(0.0, (mine + bonus) * speed)


def solar_energy(level: int) -> float:
    return 20 * level * (1.1 ** level)


def energy_consumption(name: str, level: int) -> float:
    table = {"metal_mine": 10, "crystal_mine": 10, "deut_synth": 20}
    if name not in table:
        return 0.0
    return math.ceil(table[name] * level * (1.1 ** level))


# ---------------------------------------------------------------------------
# NAVES Y DEFENSAS (estructura, escudo, arma base, velocidad, carga, consumo)
# ---------------------------------------------------------------------------
@dataclass
class Unit:
    name: str
    structure: int      # hull = structure/10
    shield: int
    weapon: int
    speed: int
    cargo: int
    fuel: int           # consumo base de deuterio
    cost: Cost
    rapidfire: Dict[str, int] = field(default_factory=dict)

    @property
    def hull(self) -> float:
        return self.structure / 10.0


SHIPS: Dict[str, Unit] = {
    "small_cargo":   Unit("small_cargo", 4000, 10, 5, 5000, 5000, 10, Cost(2000, 2000),
                          {"espionage_probe": 5, "solar_satellite": 5}),
    "large_cargo":   Unit("large_cargo", 12000, 25, 5, 7500, 25000, 50, Cost(6000, 6000),
                          {"espionage_probe": 5, "solar_satellite": 5}),
    "light_fighter": Unit("light_fighter", 4000, 10, 50, 12500, 50, 20, Cost(3000, 1000),
                          {"espionage_probe": 5, "solar_satellite": 5}),
    "heavy_fighter": Unit("heavy_fighter", 10000, 25, 150, 10000, 100, 75, Cost(6000, 4000),
                          {"espionage_probe": 5, "solar_satellite": 5, "small_cargo": 3}),
    "cruiser":       Unit("cruiser", 27000, 50, 400, 15000, 800, 300, Cost(20000, 7000, 2000),
                          {"espionage_probe": 5, "solar_satellite": 5, "light_fighter": 6, "rocket_launcher": 10}),
    "battleship":    Unit("battleship", 60000, 200, 1000, 10000, 1500, 500, Cost(45000, 15000),
                          {"espionage_probe": 5, "solar_satellite": 5}),
    "battlecruiser": Unit("battlecruiser", 70000, 400, 700, 10000, 750, 250, Cost(30000, 40000, 15000),
                          {"espionage_probe": 5, "solar_satellite": 5, "small_cargo": 3, "large_cargo": 3,
                           "heavy_fighter": 4, "cruiser": 4, "battleship": 7}),
    "bomber":        Unit("bomber", 75000, 500, 1000, 4000, 500, 1000, Cost(50000, 25000, 15000),
                          {"espionage_probe": 5, "solar_satellite": 5}),
    "destroyer":     Unit("destroyer", 110000, 500, 2000, 5000, 2000, 1000, Cost(60000, 50000, 15000),
                          {"espionage_probe": 5, "solar_satellite": 5, "battlecruiser": 2}),
    "deathstar":     Unit("deathstar", 9_000_000, 50000, 200000, 100, 1_000_000, 1, Cost(5_000_000, 4_000_000, 1_000_000),
                          {"espionage_probe": 1250, "solar_satellite": 1250, "light_fighter": 200,
                           "heavy_fighter": 100, "cruiser": 33, "battleship": 30, "bomber": 25,
                           "destroyer": 5, "battlecruiser": 15, "recycler": 250, "small_cargo": 250,
                           "large_cargo": 250}),
    "recycler":      Unit("recycler", 16000, 10, 1, 2000, 20000, 300, Cost(10000, 6000, 2000),
                          {"espionage_probe": 5, "solar_satellite": 5}),
    "espionage_probe": Unit("espionage_probe", 1000, 0, 0, 100_000_000, 5, 1, Cost(0, 1000)),
    "solar_satellite": Unit("solar_satellite", 2000, 1, 1, 0, 0, 0, Cost(0, 2000, 500)),
    "colony_ship":   Unit("colony_ship", 30000, 100, 50, 2500, 7500, 1000, Cost(10000, 20000, 10000),
                          {"espionage_probe": 5, "solar_satellite": 5}),
    "reaper":        Unit("reaper", 140000, 700, 2800, 10000, 10000, 1100, Cost(85000, 55000, 20000),
                          {"espionage_probe": 5, "solar_satellite": 5}),
    "pathfinder":    Unit("pathfinder", 23000, 100, 200, 12000, 10000, 300, Cost(8000, 15000, 8000),
                          {"espionage_probe": 5, "solar_satellite": 5}),
}

DEFENSES: Dict[str, Unit] = {
    "rocket_launcher": Unit("rocket_launcher", 2000, 20, 80, 0, 0, 0, Cost(2000, 0)),
    "light_laser":     Unit("light_laser", 2000, 25, 100, 0, 0, 0, Cost(1500, 500)),
    "heavy_laser":     Unit("heavy_laser", 8000, 100, 250, 0, 0, 0, Cost(6000, 2000)),
    "gauss_cannon":    Unit("gauss_cannon", 35000, 200, 1100, 0, 0, 0, Cost(20000, 15000, 2000)),
    "ion_cannon":      Unit("ion_cannon", 8000, 500, 150, 0, 0, 0, Cost(5000, 3000)),
    "plasma_turret":   Unit("plasma_turret", 100000, 300, 3000, 0, 0, 0, Cost(50000, 50000, 30000)),
    "small_shield_dome": Unit("small_shield_dome", 2000, 2000, 0, 0, 0, 0, Cost(10000, 10000)),
    "large_shield_dome": Unit("large_shield_dome", 10000, 10000, 0, 0, 0, 0, Cost(50000, 50000)),
}


# ---------------------------------------------------------------------------
# DISTANCIA / TIEMPO DE VUELO / COMBUSTIBLE
# ---------------------------------------------------------------------------
def distance(a: tuple, b: tuple) -> int:
    """a, b = (galaxia, sistema, posicion). Distancia estándar de OGame."""
    g1, s1, p1 = a
    g2, s2, p2 = b
    if g1 != g2:
        return 20000 * abs(g1 - g2)
    if s1 != s2:
        return 2700 + 95 * abs(s1 - s2)
    if p1 != p2:
        return 1000 + 5 * abs(p1 - p2)
    return 5  # mismo planeta (luna<->planeta)


def flight_time(dist: int, ship_speed: int, speed_percent: float = 1.0,
                universe_fleet_speed: float = 1.0) -> float:
    """Devuelve segundos. ship_speed = velocidad de la nave MÁS LENTA de la flota."""
    eff_speed = ship_speed * universe_fleet_speed
    return ((3500 / speed_percent) * math.sqrt((10 * dist) / eff_speed) + 10)


# Motor principal de cada nave y bonus de velocidad por nivel de tecnología.
SHIP_DRIVES = {
    "small_cargo": ("combustion_drive", 0.1),
    "large_cargo": ("combustion_drive", 0.1),
    "light_fighter": ("combustion_drive", 0.1),
    "heavy_fighter": ("impulse_drive", 0.2),
    "cruiser": ("impulse_drive", 0.2),
    "battleship": ("hyperspace_drive", 0.3),
    "colony_ship": ("impulse_drive", 0.2),
    "recycler": ("combustion_drive", 0.1),
    "espionage_probe": ("combustion_drive", 0.1),
    "bomber": ("impulse_drive", 0.2),
    "destroyer": ("hyperspace_drive", 0.3),
    "battlecruiser": ("hyperspace_drive", 0.3),
    "deathstar": ("hyperspace_drive", 0.3),
    "reaper": ("hyperspace_drive", 0.3),
    "pathfinder": ("hyperspace_drive", 0.3),
}


def effective_cargo(ship: str, hyperspace_level: int = 0) -> int:
    """
    Bodega real de una nave con el bonus de Tecnología de Hiperespacio (+5% por
    nivel sobre la base). Ej.: NGC base 25.000 con hiperespacio 10 -> 37.500.
    ponytail: ignora el bonus de clase Recolector (+25% carga) y formas de vida;
    el factor de seguridad de config cubre el residuo.
    """
    base = SHIPS[ship].cargo if ship in SHIPS else 0
    return int(base * (1 + 0.05 * max(hyperspace_level, 0)))


def effective_speed(ship: str, research_levels: dict = None) -> int:
    """
    Velocidad real de una nave incluyendo el bonus de su motor (la velocidad base
    no lo incluye). Acerca la estimación de vuelo a la real; el resto del desfase
    lo corrige la calibración del cerebro. ponytail: ignora los upgrades de motor
    por umbral (p.ej. large_cargo a impulso); la calibración cubre el residuo.
    """
    base = SHIPS[ship].speed if ship in SHIPS else 0
    if not research_levels:
        return base
    drive, factor = SHIP_DRIVES.get(ship, ("combustion_drive", 0.1))
    lvl = research_levels.get(drive, 0)
    return int(base * (1 + factor * max(lvl, 0)))


def fuel_cost(units: Dict[str, int], dist: int, speed_percent: float = 1.0,
              universe_fleet_speed: float = 1.0) -> int:
    """Consumo total de deuterio del viaje (ida)."""
    total = 0.0
    for name, n in units.items():
        if n <= 0:
            continue
        u = SHIPS.get(name)
        if not u:
            continue
        speed_value = speed_percent * 10
        consumption = u.fuel * n * dist / 35000.0
        total += consumption * ((speed_value / 10.0) + 1) ** 2
    return int(round(total)) + 1


# ---------------------------------------------------------------------------
# ASTROFÍSICA / SLOTS
# ---------------------------------------------------------------------------
def colony_slots(astrophysics_level: int) -> int:
    return math.floor((astrophysics_level + 1) / 2)


def expedition_slots(astrophysics_level: int) -> int:
    return math.floor(math.sqrt(astrophysics_level))


def fleet_slots(computer_tech_level: int) -> int:
    return computer_tech_level + 1


# ---------------------------------------------------------------------------
# EXPEDICIONES: botín máximo encontrable
#   Tabla oficial de OGame: el tope depende de los puntos del Top-1 del universo.
#   metal es el tope; cristal = metal/2, deuterio = metal/3. Es la misma lógica
#   que usa la calculadora de proxyforgame.com/ogame/calc/expeditions.php
# ---------------------------------------------------------------------------
EXPEDITION_METAL_CAP_TABLE = [
    (10_000, 40_000),
    (100_000, 500_000),
    (1_000_000, 1_200_000),
    (5_000_000, 1_800_000),
    (25_000_000, 2_400_000),
    (50_000_000, 3_000_000),
    (75_000_000, 3_600_000),
    (100_000_000, 4_200_000),
]
EXPEDITION_METAL_CAP_TOP = 5_000_000  # Top-1 >= 100M puntos


def expedition_base_metal_cap(top1_points: float) -> int:
    """Tope base de metal por expedición según los puntos del Top-1 del universo."""
    for threshold, cap in EXPEDITION_METAL_CAP_TABLE:
        if top1_points < threshold:
            return cap
    return EXPEDITION_METAL_CAP_TOP


def expedition_max_find_units(top1_points: float, eco_speed: float = 1.0,
                              discoverer: bool = False, pathfinder: bool = False) -> int:
    """
    Máximo de RECURSOS EN BRUTO (unidades) que una sola expedición puede encontrar
    en el mejor hallazgo posible. Sirve para dimensionar la carga (NGC) y no perder
    recursos: el hallazgo real es aleatorio (10%-100% de este tope).

    Factores: velocidad de economía del universo, clase Descubridor (x1.5) e
    incluir un Pathfinder en la flota (x2). El recurso con tope mayor es el metal,
    así que dimensionamos la bodega a ese tope.
    """
    base = expedition_base_metal_cap(top1_points)
    k_class = 1.5 if discoverer else 1.0
    k_pf = 2.0 if pathfinder else 1.0
    return int(base * max(eco_speed, 1.0) * k_class * k_pf)


def research_time(cost: "Cost", lab_level: int, universe_speed: float = 1.0,
                  nanite: int = 0) -> float:
    """
    Segundos estimados de una investigación (fórmula estándar de OGame).
    No lee la página: permite saber cuánto queda sin navegar.
    ponytail: ignora la Red de Investigación Intergaláctica (IRN); si la usas el
    tiempo real será algo menor.
    """
    denom = 1000.0 * (1 + max(lab_level, 0)) * max(universe_speed, 1.0) * (2 ** max(nanite, 0))
    if denom <= 0:
        return 0.0
    return ((cost.metal + cost.crystal) / denom) * 3600.0


def building_time(cost: "Cost", robotics_level: int = 0, nanite_level: int = 0,
                  universe_speed: float = 1.0) -> float:
    """Segundos estimados de una construcción (fórmula estándar de OGame).
    Permite saber cuándo terminará sin navegar, para encolar la siguiente."""
    denom = 2500.0 * (1 + max(robotics_level, 0)) * (2 ** max(nanite_level, 0)) * max(universe_speed, 0.01)
    if denom <= 0:
        return 0.0
    return ((cost.metal + cost.crystal) / denom) * 3600.0
