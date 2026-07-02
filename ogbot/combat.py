"""
combat.py
=========
Simulador de combate aproximado al motor de OGame. Sirve para que el bot decida
si un objetivo defendido es rentable de limpiar y con qué flota.

Modelo AGREGADO por tipo de unidad: en lugar de un objeto por nave/defensa
(contra 3000 lanzamisiles eso creaba cientos de miles de objetos por batalla),
cada tipo lleva conteo + pools agregados de escudo y casco.

Mecánicas modeladas:
 - 6 rondas de combate con FUEGO SIMULTÁNEO: ambos bandos disparan con el
   estado del INICIO de la ronda (el daño de los dos bandos se calcula primero
   y se aplica después; los destruidos en la ronda sí devuelven el fuego).
 - Reparto de disparos proporcional al conteo de cada tipo enemigo.
 - RAPIDFIRE como valor esperado: la serie de re-disparos con prob. (R-1)/R da
   R disparos esperados por tirador contra un tipo con rapidfire R.
 - ESCUDOS: si el daño de un disparo es < 1% del escudo base del objetivo,
   "rebota" (se ignora). El pool de escudo se regenera al inicio de cada ronda.
 - CASCO (hull): structure/10, agregado en pool por tipo. Si el casco medio
   queda por debajo del 70% del máximo, cada unidad explota con probabilidad
   1 - (casco / casco_max); las bajas se muestrean con redondeo aleatorio
   (se mantiene el carácter Monte Carlo).
 - Bonos de tecnología: arma/escudo/casco +10% por nivel.

NOTA: es una aproximación estadística (Monte Carlo). El motor real tiene matices,
pero esto es más que suficiente para decisiones de rentabilidad/riesgo.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import Dict, List
from .gamedata import SHIPS, DEFENSES, Unit


@dataclass
class Tech:
    weapons: int = 0
    shielding: int = 0
    armor: int = 0


@dataclass
class CombatResult:
    attacker_won: bool
    rounds: int
    attacker_losses: Dict[str, int] = field(default_factory=dict)
    defender_losses: Dict[str, int] = field(default_factory=dict)
    attacker_survivors: Dict[str, int] = field(default_factory=dict)
    defender_survivors: Dict[str, int] = field(default_factory=dict)
    debris: Dict[str, float] = field(default_factory=dict)  # metal/crystal


@dataclass
class _Pool:
    """Agregado de todas las unidades de un mismo tipo en un bando."""
    base: Unit
    weapon: float
    shield: float       # escudo base por unidad (con tech)
    hull_max: float     # casco máximo por unidad (con tech)
    count: int
    hull_frac: float = 1.0  # fracción media de casco de las unidades vivas


def _build_pools(fleet: Dict[str, int], tech: Tech, defs=False) -> List[_Pool]:
    src = DEFENSES if defs else SHIPS
    pools: List[_Pool] = []
    for name, n in fleet.items():
        u = src.get(name)
        if not u or n <= 0:
            continue
        pools.append(_Pool(
            base=u,
            weapon=u.weapon * (1 + 0.10 * tech.weapons),
            shield=u.shield * (1 + 0.10 * tech.shielding),
            hull_max=u.hull * (1 + 0.10 * tech.armor),
            count=int(n),
        ))
    return pools


def _alive(pools: List[_Pool]) -> int:
    return sum(p.count for p in pools)


def _rand_round(x: float) -> int:
    n = int(x)
    return n + (1 if random.random() < x - n else 0)


def _volley(shooters: List[_Pool], targets: List[_Pool]) -> List[float]:
    """Daño total que recibiría cada pool objetivo con el estado actual.
    No aplica nada: permite el fuego simultáneo (calcular ambos, aplicar después)."""
    dmg = [0.0] * len(targets)
    total = _alive(targets)
    if total <= 0:
        return dmg
    for s in shooters:
        if s.count <= 0 or s.weapon <= 0:
            continue
        for i, t in enumerate(targets):
            if t.count <= 0:
                continue
            if s.weapon < 0.01 * t.shield:
                continue  # rebote: absorbido sin efecto
            rf = s.base.rapidfire.get(t.base.name, 0)
            shots = s.count * (t.count / total) * (rf if rf > 0 else 1)
            dmg[i] += shots * s.weapon
    return dmg


def _apply_damage(targets: List[_Pool], dmg: List[float]):
    for t, d in zip(targets, dmg):
        if t.count <= 0:
            continue
        # escudo regenerado al inicio de la ronda: absorbe primero
        hull_dmg = max(0.0, d - t.count * t.shield)
        pool = t.count * t.hull_max * t.hull_frac - hull_dmg
        if pool <= 0:
            t.count = 0
            continue
        t.hull_frac = pool / (t.count * t.hull_max)
        # explosión de casco: se comprueba cada ronda aunque no haya daño nuevo
        if t.hull_frac < 0.7:
            deaths = _rand_round(t.count * (1 - t.hull_frac))
            t.count = max(0, t.count - deaths)


def _count(pools: List[_Pool]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for p in pools:
        if p.count > 0:
            out[p.base.name] = out.get(p.base.name, 0) + p.count
    return out


def simulate(attacker_fleet: Dict[str, int], attacker_tech: Tech,
             defender_fleet: Dict[str, int], defender_defense: Dict[str, int],
             defender_tech: Tech, debris_factor: float = 0.30,
             debris_deut: bool = False) -> CombatResult:
    atk = _build_pools(attacker_fleet, attacker_tech)
    deff = _build_pools(defender_fleet, defender_tech)
    deff += _build_pools(defender_defense, defender_tech, defs=True)

    rounds = 0
    for rounds in range(1, 7):
        if not _alive(atk) or not _alive(deff):
            break
        # fuego simultáneo: ambas descargas con el estado del inicio de la ronda
        dmg_to_def = _volley(atk, deff)
        dmg_to_atk = _volley(deff, atk)
        _apply_damage(deff, dmg_to_def)
        _apply_damage(atk, dmg_to_atk)

    atk_surv = _count(atk)
    def_surv = _count(deff)
    atk_loss = {k: attacker_fleet.get(k, 0) - atk_surv.get(k, 0) for k in attacker_fleet}
    all_def = {**defender_fleet, **defender_defense}
    def_loss = {k: all_def.get(k, 0) - def_surv.get(k, 0) for k in all_def}

    # escombros: % del coste de naves destruidas (defensa no genera escombros)
    debris = {"metal": 0.0, "crystal": 0.0}
    for name, lost in def_loss.items():
        if name in defender_defense:
            continue
        u = SHIPS.get(name)
        if not u or lost <= 0:
            continue
        debris["metal"] += u.cost.metal * lost * debris_factor
        debris["crystal"] += u.cost.crystal * lost * debris_factor
        if debris_deut:
            debris.setdefault("deut", 0.0)
            debris["deut"] += u.cost.deut * lost * debris_factor
    for name, lost in atk_loss.items():
        u = SHIPS.get(name)
        if not u or lost <= 0:
            continue
        debris["metal"] += u.cost.metal * lost * debris_factor
        debris["crystal"] += u.cost.crystal * lost * debris_factor

    attacker_won = _alive(atk) > 0 and _alive(deff) == 0
    return CombatResult(
        attacker_won=attacker_won, rounds=rounds,
        attacker_losses={k: v for k, v in atk_loss.items() if v > 0},
        defender_losses={k: v for k, v in def_loss.items() if v > 0},
        attacker_survivors=atk_surv, defender_survivors=def_surv, debris=debris,
    )


def monte_carlo(attacker_fleet, attacker_tech, defender_fleet, defender_defense,
                defender_tech, runs: int = 30, **kw) -> dict:
    """Promedia varias simulaciones para estimar probabilidad de victoria."""
    wins = 0
    losses_value = 0.0
    for _ in range(runs):
        r = simulate(attacker_fleet, attacker_tech, defender_fleet,
                     defender_defense, defender_tech, **kw)
        if r.attacker_won:
            wins += 1
        for name, n in r.attacker_losses.items():
            u = SHIPS.get(name)
            if u:
                losses_value += (u.cost.metal + u.cost.crystal + u.cost.deut) * n
    return {"win_rate": wins / runs,
            "avg_attacker_loss_value": losses_value / runs}


if __name__ == "__main__":
    import time
    t = Tech()
    # (a) superioridad clara: 200 cazas ligeros vs 20 lanzamisiles
    mc = monte_carlo({"light_fighter": 200}, t, {}, {"rocket_launcher": 20}, t, runs=30)
    assert mc["win_rate"] > 0.9, f"(a) win_rate={mc['win_rate']}"
    # (b) flotas idénticas: sin sesgo pro-atacante
    mc = monte_carlo({"light_fighter": 100}, t, {"light_fighter": 100}, {}, t, runs=50)
    assert mc["win_rate"] < 0.9, f"(b) win_rate={mc['win_rate']}"
    # (c) rendimiento con defensa masiva
    start = time.time()
    monte_carlo({"cruiser": 1000}, t, {}, {"rocket_launcher": 3000}, t, runs=20)
    elapsed = time.time() - start
    assert elapsed < 5.0, f"(c) tardo {elapsed:.2f}s"
    print(f"self-check OK: (a)(b) pasan, (c) {elapsed * 1000:.0f} ms")
