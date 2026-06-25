"""
combat.py
=========
Simulador de combate aproximado al motor de OGame. Sirve para que el bot decida
si un objetivo defendido es rentable de limpiar y con qué flota.

Mecánicas modeladas:
 - 6 rondas de combate.
 - Cada unidad dispara a un objetivo aleatorio del bando contrario.
 - RAPIDFIRE: si una unidad tiene rapidfire R contra el objetivo, con prob.
   (R-1)/R vuelve a disparar.
 - ESCUDOS: el escudo absorbe daño; si el daño de un disparo es < 1% del escudo
   base, "rebota" (no hace daño). El escudo se regenera al inicio de cada ronda.
 - CASCO (hull): structure/10. Si tras una ronda el casco < 70% del máximo, la
   unidad tiene prob. de explotar = 1 - (casco_actual / casco_max).
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
class CombatUnit:
    base: Unit
    weapon: float
    shield: float
    hull_max: float
    hull: float
    shield_pool: float = 0.0

    def reset_shield(self):
        self.shield_pool = self.shield


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


def _build_units(fleet: Dict[str, int], tech: Tech, defs=False) -> List[CombatUnit]:
    src = DEFENSES if defs else SHIPS
    units: List[CombatUnit] = []
    for name, n in fleet.items():
        u = src.get(name)
        if not u or n <= 0:
            continue
        for _ in range(int(n)):
            units.append(CombatUnit(
                base=u,
                weapon=u.weapon * (1 + 0.10 * tech.weapons),
                shield=u.shield * (1 + 0.10 * tech.shielding),
                hull_max=u.hull * (1 + 0.10 * tech.armor),
                hull=u.hull * (1 + 0.10 * tech.armor),
            ))
    return units


def _fire(shooters: List[CombatUnit], targets: List[CombatUnit]):
    if not targets:
        return
    for s in shooters:
        if s.hull <= 0:
            continue
        again = True
        while again:
            tgt = random.choice(targets)
            dmg = s.weapon
            # rebote por escudo
            if dmg < 0.01 * tgt.shield:
                pass  # disparo absorbido sin efecto relevante
            elif dmg <= tgt.shield_pool:
                tgt.shield_pool -= dmg
            else:
                rem = dmg - tgt.shield_pool
                tgt.shield_pool = 0
                tgt.hull -= rem
            # rapidfire
            rf = s.base.rapidfire.get(tgt.base.name, 0)
            again = rf > 0 and random.random() < (rf - 1) / rf


def _cleanup(units: List[CombatUnit]) -> List[CombatUnit]:
    survivors = []
    for u in units:
        if u.hull <= 0:
            continue
        # explosión si casco dañado por debajo del 70%
        if u.hull < 0.7 * u.hull_max:
            if random.random() < (1 - u.hull / u.hull_max):
                continue
        survivors.append(u)
    return survivors


def _count(units: List[CombatUnit]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for u in units:
        out[u.base.name] = out.get(u.base.name, 0) + 1
    return out


def simulate(attacker_fleet: Dict[str, int], attacker_tech: Tech,
             defender_fleet: Dict[str, int], defender_defense: Dict[str, int],
             defender_tech: Tech, debris_factor: float = 0.30,
             debris_deut: bool = False) -> CombatResult:
    atk = _build_units(attacker_fleet, attacker_tech)
    deff = _build_units(defender_fleet, defender_tech)
    deff += _build_units(defender_defense, defender_tech, defs=True)

    rounds = 0
    for rounds in range(1, 7):
        if not atk or not deff:
            break
        for u in atk:
            u.reset_shield()
        for u in deff:
            u.reset_shield()
        a_shot = list(atk)
        d_shot = list(deff)
        _fire(a_shot, deff)
        _fire(d_shot, atk)
        atk = _cleanup(atk)
        deff = _cleanup(deff)

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

    attacker_won = bool(atk) and not deff
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
