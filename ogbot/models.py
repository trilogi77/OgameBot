"""
models.py
=========
Estructuras de datos que representan el estado del juego.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass
class Resources:
    metal: float = 0.0
    crystal: float = 0.0
    deut: float = 0.0
    energy: float = 0.0

    def value(self, ratio=(2.5, 1.5, 1.0)) -> float:
        """Valor en metal-equivalente para comparar/ordenar."""
        return (self.metal
                + self.crystal * (ratio[0] / ratio[1])
                + self.deut * (ratio[0] / ratio[2]))

    def total(self) -> float:
        return self.metal + self.crystal + self.deut

    def __add__(self, o: "Resources") -> "Resources":
        return Resources(self.metal + o.metal, self.crystal + o.crystal,
                         self.deut + o.deut, self.energy + o.energy)

    def __sub__(self, o: "Resources") -> "Resources":
        return Resources(self.metal - o.metal, self.crystal - o.crystal,
                         self.deut - o.deut, self.energy - o.energy)

    def can_afford(self, cost) -> bool:
        return (self.metal >= cost.metal and self.crystal >= cost.crystal
                and self.deut >= cost.deut)


@dataclass
class Coords:
    galaxy: int
    system: int
    position: int
    type: str = "planet"  # 'planet', 'moon', o 'debris'

    def tuple(self) -> Tuple[int, int, int]:
        return (self.galaxy, self.system, self.position)

    def __str__(self) -> str:
        if self.type == "moon":
            tag = "M"
        elif self.type == "debris":
            tag = "D"
        else:
            tag = "P"
        return f"[{self.galaxy}:{self.system}:{self.position}]{tag}"


@dataclass
class Planet:
    id: str
    name: str
    coords: Coords
    resources: Resources = field(default_factory=Resources)
    max_temp: int = 30
    buildings: Dict[str, int] = field(default_factory=dict)   # nombre -> nivel
    ships: Dict[str, int] = field(default_factory=dict)
    defenses: Dict[str, int] = field(default_factory=dict)
    has_moon: bool = False
    moon: Optional["Planet"] = None
    fields_used: int = 0
    fields_max: int = 163
    building_in_progress: bool = False
    building_remaining_seconds: int = 0
    building_queue: List[str] = field(default_factory=list)
    lifeform_in_progress: bool = False
    lifeform_available: bool = True   # False si el universo/planeta no tiene Formas de vida
    # Investigación: es global de la cuenta, pero se lee del panel del overview de cada
    # planeta (todos muestran el mismo estado).
    research_in_progress: bool = False
    research_remaining_seconds: int = 0
    # Lo que el planeta está AHORRANDO para un objetivo concreto (p.ej. el paso pendiente
    # del programa especial). Los demás subsistemas solo gastan el excedente por encima
    # de esta reserva, POR RECURSO (ver economy.spendable_resources).
    savings_reserve: Optional[Resources] = None
    # Descripción legible de para qué se está ahorrando (solo informativo, para la UI).
    savings_reason: Optional[str] = None

    def lvl(self, b: str) -> int:
        val = self.buildings.get(b, 0)
        if hasattr(self, 'building_queue') and self.building_queue:
            val += self.building_queue.count(b)
        return val


@dataclass
class EspionageReport:
    coords: Coords
    player_name: str
    is_inactive: bool
    resources: Resources
    fleet: Dict[str, int] = field(default_factory=dict)
    defense: Dict[str, int] = field(default_factory=dict)
    buildings: Dict[str, int] = field(default_factory=dict)
    research: Dict[str, int] = field(default_factory=dict)
    timestamp: float = 0.0
    counterespionage_risk: float = 0.0  # probabilidad de detección
    activity_mins: Optional[int] = None  # actividad del informe (<60 = reciente); None = sin dato
    # Misiles del defensor (interceptor_missile / interplanetary_missile). Van aparte de
    # `defense` porque NO combaten (no deben marcar el planeta como "defendido"), pero cada
    # interceptor enemigo destruye un IPM nuestro al lanzarlo.
    missiles: Dict[str, int] = field(default_factory=dict)
    # Visibilidad del informe: con pocas sondas OGame OCULTA flota/defensa
    # (data-raw-hiddenships/hiddendef=1) y no expone el desglose. Sin estos flags el bot
    # veía fleet={}/defense={} y daba el objetivo por indefenso -> atacaba a ciegas.
    fleet_visible: bool = True
    defense_visible: bool = True
    fleet_value: Optional[int] = None      # valor de la flota ('-' oculto -> None)
    defense_value: Optional[int] = None    # valor de la defensa ('-' oculto -> None)
    military_score: int = 0                # puntos militares del dueño (data-raw-highscoremilitary)
    loot_percent: Optional[float] = None   # % saqueable real del servidor (data-raw-loot)

    @property
    def has_full_visibility(self) -> bool:
        return bool(self.fleet_visible and self.defense_visible)

    @property
    def is_undefended(self) -> bool:
        """FAIL-CLOSED: sin visibilidad NO se puede afirmar que esté indefenso."""
        if not self.has_full_visibility:
            return False
        if (self.fleet_value or 0) > 0 or (self.defense_value or 0) > 0:
            return False
        return sum(self.fleet.values()) == 0 and sum(self.defense.values()) == 0


@dataclass
class Target:
    coords: Coords
    player_name: str
    report: Optional[EspionageReport] = None
    expected_loot: Resources = field(default_factory=Resources)
    fuel_cost: float = 0.0
    flight_time: float = 0.0
    score: float = 0.0          # beneficio neto ponderado
    needs_clearing: bool = False  # tiene defensa/flota que destruir primero
    expected_debris: Dict[str, float] = field(default_factory=dict)  # escombros esperados del combate (simulados)

    def __str__(self) -> str:
        return (f"{self.coords} {self.player_name} "
                f"loot≈{int(self.expected_loot.value()):,} score={self.score:,.0f}")


@dataclass
class FleetMovement:
    mission: str                # attack, transport, deploy, expedition, harvest, colonize, espionage
    origin: Coords
    destination: Coords
    ships: Dict[str, int]
    arrival_ts: float
    return_ts: float
    cargo: Resources = field(default_factory=Resources)
    is_return: bool = False
