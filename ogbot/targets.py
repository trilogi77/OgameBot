"""
targets.py
==========
Buscador y analizador de objetivos de farmeo.

Flujo:
 1) UniverseAPI.inactive_targets() -> candidatos (jugadores inactivos).
 2) Filtrado por distancia desde nuestros planetas.
 3) Espionaje de los mejores candidatos (lo hace el client; aquí evaluamos).
 4) Cálculo de beneficio neto:
        loot = min(recursos * loot_percent, capacidad_carga)
        coste = combustible (ida+vuelta) + valor esperado de pérdidas en combate
        score = valor(loot) - valor(coste)
 5) Ordenar por score y devolver los mejores.

Para objetivos defendidos usa el simulador de combate (Monte Carlo) para estimar
probabilidad de victoria y pérdidas; descarta los no rentables o arriesgados.
"""
from __future__ import annotations
from typing import Dict, List, Optional
from . import gamedata as gd
from . import combat
from .models import Coords, Resources, EspionageReport, Target
from .config import Config


def parse_coords(s: str) -> Coords:
    g, sy, p = s.split(":")
    return Coords(int(g), int(sy), int(p))


def cargo_capacity(fleet: Dict[str, int]) -> int:
    return sum(gd.SHIPS[n].cargo * q for n, q in fleet.items() if n in gd.SHIPS)


def estimate_loot(resources: Resources, capacity: int, loot_percent: float) -> Resources:
    """Saqueo limitado por % saqueable y por capacidad de carga."""
    lootable = Resources(resources.metal * loot_percent,
                         resources.crystal * loot_percent,
                         resources.deut * loot_percent)
    total = lootable.total()
    if total <= capacity or total == 0:
        return lootable
    f = capacity / total
    return Resources(lootable.metal * f, lootable.crystal * f, lootable.deut * f)


def evaluate(report: EspionageReport, origin: Coords, fleet: Dict[str, int],
             my_tech: combat.Tech, cfg: Config, return_reason: bool = False):
    dist = gd.distance(origin.tuple(), report.coords.tuple())
    cap = cargo_capacity(fleet)
    loot = estimate_loot(report.resources, cap, cfg.loot_percent)

    # combustible ida + vuelta
    fuel = 2 * gd.fuel_cost(fleet, dist, 1.0, cfg.fleet_speed)
    # Filtrar naves desconocidas (KeyError) y velocidad 0 (solar_satellite -> división por cero)
    slowest = min((gd.SHIPS[n].speed for n in fleet
                   if fleet[n] > 0 and n in gd.SHIPS and gd.SHIPS[n].speed > 0), default=1)
    ftime = gd.flight_time(dist, slowest, 1.0, cfg.fleet_speed)

    needs_clearing = not report.is_undefended
    loss_value = 0.0
    debris_value = 0.0
    expected_debris: Dict[str, float] = {}
    r = cfg.trade_ratio
    if needs_clearing:
        def_tech = combat.Tech(
            weapons=report.research.get("weapons_tech", 0),
            shielding=report.research.get("shielding_tech", 0),
            armor=report.research.get("armor_tech", 0),
        )
        mc = combat.monte_carlo(fleet, my_tech, report.fleet, report.defense,
                                def_tech, runs=20,
                                debris_factor=cfg.debris_factor,
                                debris_deut=cfg.debris_includes_deut)
        if mc["win_rate"] < 0.95:
            reason = f"Combate arriesgado (victoria {mc['win_rate']*100:.1f}%)"
            return (None, reason) if return_reason else None
        loss_value = mc["avg_attacker_loss_value"]
        # Los escombros del combate cuentan como beneficio solo si el bot los va
        # a reciclar (sonda suicida + recicladores tras el ataque).
        if getattr(cfg, "farm_recycle_debris", True):
            expected_debris = mc.get("avg_debris") or {}
            debris_value = Resources(expected_debris.get("metal", 0.0),
                                     expected_debris.get("crystal", 0.0),
                                     expected_debris.get("deut", 0.0)).value(r)

    loot_val = loot.value(r)
    fuel_val = fuel * (r[0] / r[2])
    score = loot_val - fuel_val - loss_value + debris_value

    if loot_val < cfg.min_loot_value:
        reason = f"Botín insuficiente ({loot_val:.0f} < mín {cfg.min_loot_value:.0f})"
        return (None, reason) if return_reason else None
    
    if score <= 0:
        reason = f"No rentable (Score {score:.0f} <= 0; loot={loot_val:.0f}, fuel={fuel_val:.0f}, pérdidas={loss_value:.0f})"
        return (None, reason) if return_reason else None

    target = Target(coords=report.coords, player_name=report.player_name,
                    report=report, expected_loot=loot, fuel_cost=fuel,
                    flight_time=ftime, score=score, needs_clearing=needs_clearing,
                    expected_debris=expected_debris)
    return (target, "Apto para ataque") if return_reason else target

def select_targets(candidates: List[dict], origins: List[Coords],
                   max_distance_systems: int) -> List[dict]:
    """Pre-filtra por distancia antes de gastar sondas de espionaje."""
    out = []
    for c in candidates:
        coords = parse_coords(c["coords"])
        min_sys_dist = 9999
        best_dist = 10 ** 9
        for o in origins:
            if o.galaxy == coords.galaxy:
                sys_dist = abs(o.system - coords.system)
                if sys_dist < min_sys_dist:
                    min_sys_dist = sys_dist
                d = gd.distance(o.tuple(), coords.tuple())
                if d < best_dist:
                    best_dist = d
        if min_sys_dist <= max_distance_systems:
            c["_distance"] = best_dist
            out.append(c)
    # Ordenar por distancia (menor distancia primero) y usar el ranking económico como desempate
    out.sort(key=lambda c: (c["_distance"], c.get("econ_rank", 999999)))
    return out


def rank(targets: List[Target], limit: int) -> List[Target]:
    return sorted(targets, key=lambda t: t.score, reverse=True)[:limit]


# --- Aprendizaje por objetivo (historial de botín REAL de los combates) -------
# Entrada del historial: {"raids": int, "loot": float (valor metal-equiv.), "last": epoch}

def avg_real_loot(entry: Optional[dict]) -> float:
    """Botín real medio por raid del historial de un objetivo (0 = sin historial)."""
    raids = int((entry or {}).get("raids", 0) or 0)
    return float((entry or {}).get("loot", 0.0) or 0.0) / raids if raids > 0 else 0.0


def blacklist_state(entry: Optional[dict], min_loot_value: float, now: float,
                    days: float, min_raids: int = 3) -> str:
    """Decisión de blacklist para una granja según su botín real:
       'skip'  = pobre demostrada y dentro de la ventana de castigo -> no espiar/atacar
       'reset' = ventana cumplida -> borrar historial y darle otra oportunidad
       'ok'    = atacable (sin historial suficiente o rinde bien)."""
    if days <= 0 or not entry:
        return "ok"
    if int(entry.get("raids", 0) or 0) < min_raids:
        return "ok"
    if avg_real_loot(entry) >= min_loot_value:
        return "ok"
    last = float(entry.get("last", 0.0) or 0.0)
    return "skip" if now - last < days * 86400 else "reset"
