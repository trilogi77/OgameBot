"""
economy.py
==========
Gestor económico. Decide la SIGUIENTE construcción óptima en cada planeta usando
análisis de payback marginal en vez de un ratio fijo de minas:

Para cada mina candidata calculamos:
   coste_subida   (en metal-equivalente)
   produccion_extra/hora (en metal-equivalente) al subir 1 nivel
   payback = coste / produccion_extra
Elegimos la mina con MENOR payback (la que se amortiza antes). Si todas superan
el umbral configurado, invertimos en investigación/flota.

También gestiona energía (placas solares / reactor de fusión) y robótica/nanitas
para acelerar colas, y almacenes para no desbordar producción.
"""
from __future__ import annotations
from typing import Optional, Tuple
from . import gamedata as gd
from .models import Planet, Resources
from .config import Config


def _eq(res: gd.Cost, ratio) -> float:
    return res.total_value(ratio)


def _eff(planet: Planet, res: str) -> float:
    """Factor producción_real / producción_por_fórmula del planeta para ese recurso.

    Lo calcula el brain a partir de la producción REAL cacheada (page=resourcesettings) y lo
    cuelga del planeta como `production_efficiency`. Captura oficiales, clase, formas de vida
    y boosters, que las fórmulas base ignoran (error típico del 20-40%). Sin dato (o absurdo)
    cae a 1.0, es decir, el comportamiento anterior."""
    e = getattr(planet, "production_efficiency", None) or {}
    try:
        v = float(e.get(res, 1.0))
    except (TypeError, ValueError):
        return 1.0
    return v if 0.1 <= v <= 5.0 else 1.0


def _deut_net_production(deut_lvl: int, planet: Planet, cfg: Config, plasma: int) -> float:
    """Deuterio/h neto: producción del sintetizador menos el consumo del reactor de fusión."""
    prod = gd.deut_production(deut_lvl, planet.max_temp, plasma, cfg.universe_speed,
                              efficiency=_eff(planet, "deut"))
    return prod - gd.fusion_deut_consumption(planet.lvl("fusion_reactor"), cfg.universe_speed)


def production_eq_per_hour(planet: Planet, cfg: Config, plasma: int) -> float:
    """Producción actual del planeta en metal-equivalente/hora."""
    m = gd.metal_production(planet.lvl("metal_mine"), plasma, cfg.universe_speed,
                            efficiency=_eff(planet, "metal"))
    c = gd.crystal_production(planet.lvl("crystal_mine"), plasma, cfg.universe_speed,
                              efficiency=_eff(planet, "crystal"))
    d = _deut_net_production(planet.lvl("deut_synth"), planet, cfg, plasma)
    r = cfg.trade_ratio
    return m + c * (r[0] / r[1]) + d * (r[0] / r[2])


def effective_payback_threshold(planet: Planet, cfg: Config) -> float:
    """Umbral de amortización EFECTIVO, en horas.

    El valor fijo (24 h) está tuneado para el arranque: en cuanto TODAS las minas lo superan
    (~nivel 19-20) `next_build` devolvía None para siempre, las minas dejaban de subir y el
    bot solo engordaba almacenes. Aquí el umbral crece con el nivel medio de minas (que una
    mina cara tarde más en amortizar es lo normal al crecer), con un tope duro.
    Con `adaptive_mine_payback: false` se respeta el valor fijo de siempre.
    """
    base = float(getattr(cfg, "target_mine_ratio_payback_hours", 24.0))
    if not getattr(cfg, "adaptive_mine_payback", True):
        return base
    avg = (planet.lvl("metal_mine") + planet.lvl("crystal_mine") + planet.lvl("deut_synth")) / 3.0
    cap = float(getattr(cfg, "max_mine_payback_hours", 168.0) or 168.0)
    return min(cap, base * (1.0 + avg / 20.0))


def _mine_payback(planet: Planet, mine: str, cfg: Config, plasma: int) -> Tuple[float, gd.Cost]:
    lvl = planet.lvl(mine)
    cost = gd.building_cost(mine, lvl + 1)
    r = cfg.trade_ratio
    if mine == "metal_mine":
        eff = _eff(planet, "metal")
        cur = gd.metal_production(lvl, plasma, cfg.universe_speed, efficiency=eff)
        nxt = gd.metal_production(lvl + 1, plasma, cfg.universe_speed, efficiency=eff)
        extra = nxt - cur
    elif mine == "crystal_mine":
        eff = _eff(planet, "crystal")
        cur = gd.crystal_production(lvl, plasma, cfg.universe_speed, efficiency=eff)
        nxt = gd.crystal_production(lvl + 1, plasma, cfg.universe_speed, efficiency=eff)
        extra = (nxt - cur) * (r[0] / r[1])
    else:  # deut_synth
        cur = _deut_net_production(lvl, planet, cfg, plasma)
        nxt = _deut_net_production(lvl + 1, planet, cfg, plasma)
        extra = (nxt - cur) * (r[0] / r[2])
    if extra <= 0:
        return float("inf"), cost
    payback = _eq(cost, r) / extra        # horas para amortizar
    return payback, cost


def energy_balance(planet: Planet, energy_tech: int = 0) -> float:
    """
    Calcula el balance de energía ajustado para el planeta.
    Utiliza el valor real leído de la página como base para incorporar
    bonificaciones de oficiales, formas de vida y boosters, y ajusta
    el consumo/producción si los niveles simulados de edificios difieren
    de los reales.
    """
    # 1. Calcular producción y consumo usando los niveles actuales en planet.buildings (que pueden ser simulados)
    prod_simulated = gd.solar_energy(planet.lvl("solar_plant"))
    # Satélites solares (comprobar tanto en ships como en buildings)
    sats = planet.ships.get("solar_satellite", 0) or planet.buildings.get("solar_satellite", 0)
    prod_simulated += sats * 25
    
    fusion_lvl_sim = planet.lvl("fusion_reactor")
    if fusion_lvl_sim > 0:
        prod_simulated += 30 * fusion_lvl_sim * ((1.05 + energy_tech * 0.01) ** fusion_lvl_sim)
        
    cons_simulated = sum(gd.energy_consumption(m, planet.lvl(m))
                         for m in ("metal_mine", "crystal_mine", "deut_synth"))
    
    # 2. Obtener niveles reales
    actual_buildings = planet.buildings.copy()
                
    prod_actual = gd.solar_energy(actual_buildings.get("solar_plant", 0))
    prod_actual += sats * 25
    
    fusion_lvl_act = actual_buildings.get("fusion_reactor", 0)
    if fusion_lvl_act > 0:
        prod_actual += 30 * fusion_lvl_act * ((1.05 + energy_tech * 0.01) ** fusion_lvl_act)
        
    cons_actual = sum(gd.energy_consumption(m, actual_buildings.get(m, 0))
                      for m in ("metal_mine", "crystal_mine", "deut_synth"))
                      
    base_energy = planet.resources.energy
    
    # Si base_energy es 0 y el cálculo de la fórmula para el estado real da algo muy distinto a 0,
    # probablemente estemos en un test unitario donde no se raspó la página y resources.energy es 0.
    # En ese caso, usamos el balance por fórmula puro.
    formula_actual = prod_actual - cons_actual
    if base_energy == 0.0 and formula_actual != 0.0:
        return prod_simulated - cons_simulated
        
    # Si tenemos base_energy real, ajustamos por la diferencia entre simulado y real
    diff_prod = prod_simulated - prod_actual
    diff_cons = cons_simulated - cons_actual
    
    return base_energy + diff_prod - diff_cons


def time_to_accumulate(cost: gd.Cost, planet: Planet, cfg: Config, plasma: int) -> float:
    """Calcula el tiempo estimado en horas para acumular los recursos necesarios para un coste, aplicando el buffer."""
    buf = 1 - cfg.keep_resources_buffer
    avail_m = planet.resources.metal * buf
    avail_c = planet.resources.crystal * buf
    avail_d = planet.resources.deut * buf

    needed_m = max(0.0, cost.metal - avail_m)
    needed_c = max(0.0, cost.crystal - avail_c)
    needed_d = max(0.0, cost.deut - avail_d)

    if needed_m == 0.0 and needed_c == 0.0 and needed_d == 0.0:
        return 0.0

    prod_m = max(0.1, gd.metal_production(planet.lvl("metal_mine"), plasma, cfg.universe_speed))
    prod_c = max(0.1, gd.crystal_production(planet.lvl("crystal_mine"), plasma, cfg.universe_speed))
    prod_d = max(0.1, _deut_net_production(planet.lvl("deut_synth"), planet, cfg, plasma))

    t_m = needed_m / prod_m
    t_c = needed_c / prod_c
    t_d = needed_d / prod_d

    return max(t_m, t_c, t_d)


def spendable_resources(planet: Planet, cfg: Config, buf: Optional[float] = None,
                        research_levels: Optional[dict] = None) -> Resources:
    """Recursos que este planeta puede GASTAR ahora mismo.

    Aplica el buffer configurado y descuenta la reserva de ahorro del planeta
    (``planet.savings_reserve``, la fija el brain cuando el planeta está ahorrando para un
    objetivo concreto, p.ej. un paso del programa especial). La reserva se descuenta POR
    RECURSO: ahorrar deuterio para un motor no debe bloquear el metal/cristal sobrante,
    que sigue disponible para minas u otras construcciones.

    Además, la reserva de cada recurso se reduce por lo que ese recurso va a PRODUCIR
    mientras se espera al recurso que de verdad falta (mismo tiempo que ve
    ``time_to_accumulate``): si al cuello de botella (p.ej. deuterio) le quedan 2.3h,
    el metal/cristal reservado que la producción de esas 2.3h vaya a reponer de sobra
    no hace falta tenerlo ya guardado, y se libera para subir minas u otra cosa ahora
    en vez de esperar sin motivo.
    """
    if buf is None:
        buf = 1 - cfg.keep_resources_buffer
    m = planet.resources.metal * buf
    c = planet.resources.crystal * buf
    d = planet.resources.deut * buf
    block = current_savings_block(planet, cfg, research_levels)
    if block is not None:
        m = max(0.0, m - block.metal)
        c = max(0.0, c - block.crystal)
        d = max(0.0, d - block.deut)
    return Resources(m, c, d)


def current_savings_block(planet: Planet, cfg: Config,
                          research_levels: Optional[dict] = None) -> Optional[Resources]:
    """Cuánto de cada recurso está reteniendo AHORA MISMO ``planet.savings_reserve``
    (la reserva descontada por lo que ya va a producir mientras se espera al recurso
    cuello de botella; ver ``spendable_resources``). None si el planeta no está ahorrando."""
    reserve = getattr(planet, "savings_reserve", None)
    if reserve is None:
        return None
    res_m = max(0.0, getattr(reserve, "metal", 0.0))
    res_c = max(0.0, getattr(reserve, "crystal", 0.0))
    res_d = max(0.0, getattr(reserve, "deut", 0.0))
    plasma = (research_levels or {}).get("plasma_tech", 0)
    wait_h = time_to_accumulate(reserve, planet, cfg, plasma)
    if wait_h > 0:
        prod_m = gd.metal_production(planet.lvl("metal_mine"), plasma, cfg.universe_speed)
        prod_c = gd.crystal_production(planet.lvl("crystal_mine"), plasma, cfg.universe_speed)
        prod_d = max(0.0, _deut_net_production(planet.lvl("deut_synth"), planet, cfg, plasma))
        res_m = max(0.0, res_m - prod_m * wait_h)
        res_c = max(0.0, res_c - prod_c * wait_h)
        res_d = max(0.0, res_d - prod_d * wait_h)
    return Resources(res_m, res_c, res_d)


def surplus_after_reserve(avail: Resources, cost) -> Resources:
    """Excedente por recurso tras apartar lo que ``cost`` (el objetivo de ahorro) necesita."""
    return Resources(max(0.0, avail.metal - cost.metal),
                     max(0.0, avail.crystal - cost.crystal),
                     max(0.0, avail.deut - cost.deut))


def _effective_storage_min_cap(cfg: Config, research_levels: Optional[dict]) -> int:
    """Objetivo de capacidad mínima de almacén EFECTIVO para este momento.

    Fuera del arranque es ``storage_min_capacity_target`` (1M por defecto). Pero durante
    la FASE DE DESBLOQUEO (árbol de investigación sin completar) perseguir 1M de capacidad
    es inversión prematura: cada almacén a 1M cuesta ~255k de metal y en un arranque de
    pocos días nunca se retiene tanto, así que roba recursos a minas/investigación. Ahí se
    usa un objetivo reducido (``storage_min_capacity_target_startup``, 250k por defecto),
    suficiente para guardar el ahorro más grande del desbloqueo. Al terminarlo se recupera
    el objetivo pleno automáticamente.
    """
    base = int(getattr(cfg, "storage_min_capacity_target", 1_000_000) or 0)
    if base <= 0:
        return base
    if getattr(cfg, "research_unlock_all", True) and research_levels:
        from .research import next_unlock_research
        if next_unlock_research(research_levels) is not None:
            startup = int(getattr(cfg, "storage_min_capacity_target_startup", 250_000) or 0)
            if startup > 0:
                return min(base, startup)
    return base


def _next_storage_build(planet: Planet, cfg: Config, get_resolved_building,
                        min_cap_target: Optional[int] = None):
    """Devuelve (nombre_almacén, coste) del almacén a subir con PRIORIDAD MÁXIMA, o None.

    Regla: cuando un recurso se acerca a llenar su almacén, subir ese almacén es la
    máxima prioridad de construcción. Además, mientras el almacén no pueda guardar el
    mínimo objetivo (``min_cap_target``, 1M por defecto fuera del arranque) el disparador
    es más agresivo (se subirá aunque solo esté a medio llenar) para llegar cuanto antes
    a un nivel capaz de almacenar ese mínimo.
    """
    from . import startorder
    trigger = getattr(cfg, "storage_fill_trigger_percent", 0.90)
    if min_cap_target is None:
        min_cap_target = getattr(cfg, "storage_min_capacity_target", 1_000_000)
    for res_name, b_name in (("metal", "metal_storage"),
                             ("crystal", "crystal_storage"),
                             ("deut", "deut_tank")):
        lvl = planet.lvl(b_name)
        cap = startorder.storage_capacity(lvl)
        current_amount = getattr(planet.resources, res_name)
        # Mientras el almacén no alcance el nivel capaz de guardar el mínimo objetivo,
        # bajamos el umbral de disparo para forzar su subida cuanto antes.
        effective_trigger = trigger
        if min_cap_target > 0 and cap < min_cap_target:
            effective_trigger = min(trigger, 0.5)
        if current_amount >= cap * effective_trigger:
            res = get_resolved_building(b_name, lvl + 1)
            if res:
                return res
    return None


def metal_dump_research(planet: Planet, cfg: Config,
                        research_levels: Optional[Dict[str, int]] = None):
    """Blindaje como sumidero del EXCEDENTE de metal, en vez de un almacén cada vez más caro.

    Blindaje cuesta 1000 de metal PURO y dobla por nivel: exactamente la misma curva que el
    almacén de metal. Así que cuando el metal va a desbordar, subir blindaje convierte metal
    muerto en casco permanente para toda la flota, mientras que el almacén solo compra sitio
    donde guardarlo.

    Se AUTOLIMITA: solo se propone mientras el blindaje no salga más caro que el almacén que
    evitamos (es decir, mientras nivel_blindaje < nivel_almacén). Cuando lo alcanza, gana el
    almacén y el metal vuelve a acumularse.

    Devuelve ("armor_tech", coste) o None. El llamante decide (necesita saber que el slot de
    investigación está libre y que no hay algo mejor que investigar).
    """
    if not getattr(cfg, "enable_metal_dump_research", True):
        return None
    if planet.lvl("research_lab") < gd.RESEARCH_LAB_REQ.get("armor_tech", 2):
        return None

    lvl = int((research_levels or {}).get("armor_tech", 0))
    cap = int(getattr(cfg, "metal_dump_max_armor_tech", 20) or 20)
    user_cap = (getattr(cfg, "research_caps", None) or {}).get("armor_tech")
    if user_cap:                       # respetar el tope que el usuario haya puesto
        cap = min(cap, int(user_cap))
    if lvl >= cap:
        return None

    cost = gd.research_cost("armor_tech", lvl + 1)
    storage_cost = gd.building_cost("metal_storage", planet.lvl("metal_storage") + 1)
    if cost.metal > storage_cost.metal:
        return None                    # el blindaje ya es más caro: mejor el almacén

    avail = spendable_resources(planet, cfg)
    if not avail.can_afford(cost):
        return None
    return "armor_tech", cost


def get_mine_target_level(mine: str, cfg: Config) -> int:
    target = cfg.max_mine_level
    if mine == "metal_mine":
        target = getattr(cfg, "target_metal_mine", 99)
    elif mine == "crystal_mine":
        target = getattr(cfg, "target_crystal_mine", 99)
    elif mine == "deut_synth":
        target = getattr(cfg, "target_deut_synth", 99)
    return min(target, cfg.max_mine_level)


def next_build(
    planet: Planet,
    cfg: Config,
    plasma: int = 0,
    research_levels: Optional[Dict[str, int]] = None,
    requested_research_lab_lvl: int = 0,
    best_lab_planet_coords = None,
    requested_shipyard_lvl: int = 0
) -> Optional[Tuple[str, gd.Cost]]:
    """
    Devuelve (nombre_edificio, coste) de la próxima construcción recomendada,
    o None si conviene gastar en investigación/flota.
    """
    if research_levels is None:
        research_levels = {}
    from .prereqs import resolve_prerequisites

    def get_resolved_building(b_name: str, target_lvl: int) -> Optional[Tuple[str, gd.Cost]]:
        res = resolve_prerequisites("building", b_name, target_lvl, planet, research_levels)
        if res and res[0] == "building":
            return res[1], gd.building_cost(res[1], res[2])
        return None

    # 0) PRIORIDAD MÁXIMA: almacenes. Si algún recurso se acerca a llenar su almacén
    #    (o el almacén aún no puede guardar el mínimo objetivo), subir el almacén manda
    #    por encima de laboratorio, astillero, energía y minas. Durante el desbloqueo el
    #    objetivo se reduce para no invertir en almacenes de 1M de forma prematura.
    storage_choice = _next_storage_build(
        planet, cfg, get_resolved_building,
        _effective_storage_min_cap(cfg, research_levels))
    if storage_choice:
        return storage_choice

    # 1) Laboratorio de investigación si es solicitado y este es el planeta designado
    if best_lab_planet_coords and planet.coords.tuple() == best_lab_planet_coords.tuple():
        if planet.lvl("research_lab") < requested_research_lab_lvl:
            res = get_resolved_building("research_lab", planet.lvl("research_lab") + 1)
            if res:
                return res

    # 2) Astillero si es solicitado o temprano si metal >= 4 y no hay astillero
    target_shipyard = max(1 if planet.lvl("metal_mine") >= 4 else 0, requested_shipyard_lvl)
    if target_shipyard > 0 and planet.lvl("shipyard") < target_shipyard:
        res = get_resolved_building("shipyard", planet.lvl("shipyard") + 1)
        if res:
            return res

    # 3) Energía primero: si está en déficit, placa solar (o fusión si es rentable)
    energy_tech = research_levels.get("energy_tech", 0) if research_levels else 0
    if energy_balance(planet, energy_tech) < 0:
        if cfg.enable_fusion_reactor and planet.lvl("deut_synth") >= 12 \
                and planet.lvl("fusion_reactor") < planet.lvl("solar_plant") - cfg.fusion_reactor_solar_offset:
            res = get_resolved_building("fusion_reactor", planet.lvl("fusion_reactor") + 1)
            if res:
                return res
        return get_resolved_building("solar_plant", planet.lvl("solar_plant") + 1)

    # 4) Robótica temprana solo después de tener deut básico
    if planet.lvl("robotics_factory") < 2 and planet.lvl("deut_synth") >= 3:
        res = get_resolved_building("robotics_factory", planet.lvl("robotics_factory") + 1)
        if res:
            return res

    # 5) Mejor mina por payback
    best_mine, best_pb, best_cost = None, float("inf"), None
    for mine in ("metal_mine", "crystal_mine", "deut_synth"):
        if planet.lvl(mine) >= get_mine_target_level(mine, cfg):
            continue
        res = resolve_prerequisites("building", mine, planet.lvl(mine) + 1, planet, research_levels)
        if not res or res[0] != "building":
            continue
        actual_name = res[1]
        actual_lvl = res[2]
        cost = gd.building_cost(actual_name, actual_lvl)

        pb, _ = _mine_payback(planet, mine, cfg, plasma)
        if pb < best_pb:
            best_mine, best_pb, best_cost = actual_name, pb, cost

    if best_mine and best_pb <= effective_payback_threshold(planet, cfg):
        return best_mine, best_cost

def next_resources_build(
    planet: Planet,
    cfg: Config,
    plasma: int = 0,
    research_levels: Optional[Dict[str, int]] = None
) -> Optional[Tuple[str, gd.Cost]]:
    if research_levels is None:
        research_levels = {}
    from .prereqs import resolve_prerequisites

    def get_resolved_building(b_name: str, target_lvl: int) -> Optional[Tuple[str, gd.Cost]]:
        res = resolve_prerequisites("building", b_name, target_lvl, planet, research_levels)
        if res and res[0] == "building":
            return res[1], gd.building_cost(res[1], res[2])
        return None

    # 0) PRIORIDAD MÁXIMA: almacenes (antes que energía y minas). Objetivo reducido
    #    durante el desbloqueo (ver _effective_storage_min_cap).
    storage_choice = _next_storage_build(
        planet, cfg, get_resolved_building,
        _effective_storage_min_cap(cfg, research_levels))
    if storage_choice:
        return storage_choice

    # 1) Energía
    energy_tech = research_levels.get("energy_tech", 0) if research_levels else 0
    if energy_balance(planet, energy_tech) < 0:
        if cfg.enable_fusion_reactor and planet.lvl("deut_synth") >= 12 \
                and planet.lvl("fusion_reactor") < planet.lvl("solar_plant") - cfg.fusion_reactor_solar_offset:
            res = get_resolved_building("fusion_reactor", planet.lvl("fusion_reactor") + 1)
            if res:
                return res
        return get_resolved_building("solar_plant", planet.lvl("solar_plant") + 1)

    # 3) Minas por payback
    best_mine, best_pb, best_cost = None, float("inf"), None
    for mine in ("metal_mine", "crystal_mine", "deut_synth"):
        if planet.lvl(mine) >= get_mine_target_level(mine, cfg):
            continue
        res = resolve_prerequisites("building", mine, planet.lvl(mine) + 1, planet, research_levels)
        if not res or res[0] != "building":
            continue
        actual_name = res[1]
        actual_lvl = res[2]
        cost = gd.building_cost(actual_name, actual_lvl)

        pb, _ = _mine_payback(planet, mine, cfg, plasma)
        if pb < best_pb:
            best_mine, best_pb, best_cost = actual_name, pb, cost

    if best_mine and best_pb <= effective_payback_threshold(planet, cfg):
        return best_mine, best_cost

    return None


def next_facilities_build(
    planet: Planet,
    cfg: Config,
    research_levels: Optional[Dict[str, int]] = None,
    requested_research_lab_lvl: int = 0,
    best_lab_planet_coords = None,
    requested_shipyard_lvl: int = 0
) -> Optional[Tuple[str, gd.Cost]]:
    if research_levels is None:
        research_levels = {}
    from .prereqs import resolve_prerequisites

    def get_resolved_building(b_name: str, target_lvl: int) -> Optional[Tuple[str, gd.Cost]]:
        res = resolve_prerequisites("building", b_name, target_lvl, planet, research_levels)
        if res and res[0] == "building":
            return res[1], gd.building_cost(res[1], res[2])
        return None

    # 1) Laboratorio
    if best_lab_planet_coords and planet.coords.tuple() == best_lab_planet_coords.tuple():
        if planet.lvl("research_lab") < requested_research_lab_lvl:
            res = get_resolved_building("research_lab", planet.lvl("research_lab") + 1)
            if res:
                return res

    # 2) Astillero
    target_shipyard = max(1 if planet.lvl("metal_mine") >= 4 else 0, requested_shipyard_lvl)
    if target_shipyard > 0 and planet.lvl("shipyard") < target_shipyard:
        res = get_resolved_building("shipyard", planet.lvl("shipyard") + 1)
        if res:
            return res

    # 3) Robótica
    if planet.lvl("robotics_factory") < 2 and planet.lvl("deut_synth") >= 3:
        res = get_resolved_building("robotics_factory", planet.lvl("robotics_factory") + 1)
        if res:
            return res

    return None


def affordable_resources_build(
    planet: Planet,
    cfg: Config,
    plasma: int = 0,
    research_levels: Optional[Dict[str, int]] = None
):
    if research_levels is None:
        research_levels = {}
    choice = next_resources_build(planet, cfg, plasma, research_levels)
    if not choice:
        return None
    name, cost = choice
    avail = spendable_resources(planet, cfg, research_levels=research_levels)

    if avail.can_afford(cost):
        return name, cost

    t = time_to_accumulate(cost, planet, cfg, plasma)
    max_wait = getattr(cfg, "max_saving_hours_economy", 4.0)

    # Al ahorrar, el fallback solo puede gastar el EXCEDENTE que el objetivo no necesita.
    fallback_avail = surplus_after_reserve(avail, cost) if t <= max_wait else avail

    # Fallback
    from .prereqs import resolve_prerequisites
    affordable_mines = []
    for mine in ("metal_mine", "crystal_mine", "deut_synth"):
        if planet.lvl(mine) >= get_mine_target_level(mine, cfg):
            continue
        res = resolve_prerequisites("building", mine, planet.lvl(mine) + 1, planet, research_levels)
        if not res or res[0] != "building" or res[1] != mine:
            continue
        c = gd.building_cost(mine, planet.lvl(mine) + 1)
        if fallback_avail.can_afford(c):
            pb, _ = _mine_payback(planet, mine, cfg, plasma)
            affordable_mines.append((mine, pb, c))

    if affordable_mines:
        affordable_mines.sort(key=lambda x: x[1])
        best_mine, _, best_cost = affordable_mines[0]
        return best_mine, best_cost

    return None


def affordable_facilities_build(
    planet: Planet,
    cfg: Config,
    plasma: int = 0,
    research_levels: Optional[Dict[str, int]] = None,
    requested_research_lab_lvl: int = 0,
    best_lab_planet_coords = None,
    requested_shipyard_lvl: int = 0
):
    if research_levels is None:
        research_levels = {}
    choice = next_facilities_build(
        planet, cfg, research_levels,
        requested_research_lab_lvl, best_lab_planet_coords,
        requested_shipyard_lvl
    )
    if not choice:
        return None
    name, cost = choice
    avail = spendable_resources(planet, cfg, research_levels=research_levels)

    if avail.can_afford(cost):
        return name, cost

    return None


def affordable_build(
    planet: Planet,
    cfg: Config,
    plasma: int = 0,
    research_levels: Optional[Dict[str, int]] = None,
    requested_research_lab_lvl: int = 0,
    best_lab_planet_coords = None,
    requested_shipyard_lvl: int = 0
):
    """Como next_build, pero comprueba que haya recursos (con buffer) y aplica lógica de ahorro."""
    if research_levels is None:
        research_levels = {}
    choice = next_build(
        planet, cfg, plasma, research_levels,
        requested_research_lab_lvl, best_lab_planet_coords,
        requested_shipyard_lvl
    )
    if not choice:
        return None
    name, cost = choice
    avail = spendable_resources(planet, cfg, research_levels=research_levels)

    if avail.can_afford(cost):
        return name, cost

    # Lógica de ahorro: si no se lo puede permitir, ver cuánto tardará
    t = time_to_accumulate(cost, planet, cfg, plasma)
    max_wait = getattr(cfg, "max_saving_hours_economy", 4.0)

    # Mientras se ahorra (t <= max_wait) solo se aparta lo que el objetivo NECESITA de
    # cada recurso: el excedente de los demás puede pagar una mina alternativa YA (que
    # falte deuterio para el objetivo no bloquea minas que solo cuestan metal/cristal).
    # Si el objetivo tardará demasiado, se libera todo para no parar el planeta.
    fallback_avail = surplus_after_reserve(avail, cost) if t <= max_wait else avail

    from .prereqs import resolve_prerequisites
    affordable_mines = []
    for mine in ("metal_mine", "crystal_mine", "deut_synth"):
        if planet.lvl(mine) >= get_mine_target_level(mine, cfg):
            continue
        res = resolve_prerequisites("building", mine, planet.lvl(mine) + 1, planet, research_levels)
        if not res or res[0] != "building" or res[1] != mine:
            continue
        c = gd.building_cost(mine, planet.lvl(mine) + 1)
        if fallback_avail.can_afford(c):
            pb, _ = _mine_payback(planet, mine, cfg, plasma)
            affordable_mines.append((mine, pb, c))

    if affordable_mines:
        affordable_mines.sort(key=lambda x: x[1])
        best_mine, _, best_cost = affordable_mines[0]
        return best_mine, best_cost

    return None



# ---------------------------------------------------------------------------
# Defensa
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Defensa
# ---------------------------------------------------------------------------
_DEFENSE_LIST = [
    "rocket_launcher",
    "light_laser",
    "heavy_laser",
    "gauss_cannon",
    "ion_cannon",
    "plasma_turret",
    "small_shield_dome",
    "large_shield_dome",
]


def check_defense_prereqs(name: str, planet: Planet, research_levels: Dict[str, int]) -> bool:
    """Verifica si se cumplen los prerrequisitos de astillero y tecnología para construir una defensa."""
    if name == "rocket_launcher":
        return planet.lvl("shipyard") >= 1
    elif name == "light_laser":
        return (planet.lvl("shipyard") >= 2 and 
                research_levels.get("laser_tech", 0) >= 3 and 
                research_levels.get("energy_tech", 0) >= 1)
    elif name == "heavy_laser":
        return (planet.lvl("shipyard") >= 4 and 
                research_levels.get("laser_tech", 0) >= 6 and 
                research_levels.get("energy_tech", 0) >= 3)
    elif name == "ion_cannon":
        return (planet.lvl("shipyard") >= 4 and 
                research_levels.get("ion_tech", 0) >= 4)
    elif name == "gauss_cannon":
        return (planet.lvl("shipyard") >= 6 and 
                research_levels.get("energy_tech", 0) >= 6 and 
                research_levels.get("weapons_tech", 0) >= 3 and 
                research_levels.get("shielding_tech", 0) >= 1)
    elif name == "plasma_turret":
        return (planet.lvl("shipyard") >= 8 and 
                research_levels.get("plasma_tech", 0) >= 7)
    elif name == "small_shield_dome":
        return (planet.lvl("shipyard") >= 1 and
                research_levels.get("shielding_tech", 0) >= 2)
    elif name == "large_shield_dome":
        return (planet.lvl("shipyard") >= 6 and
                research_levels.get("shielding_tech", 0) >= 6)
    return False


def get_planet_defense_ratio(planet: Planet, cfg: Config, attr: str, default_val: float) -> float:
    coords_str = f"{planet.coords.galaxy}:{planet.coords.system}:{planet.coords.position}"
    planets_config = getattr(cfg, "planets_config", {}) or {}
    p_cfg = planets_config.get(coords_str, {})
    return p_cfg.get(attr, getattr(cfg, attr, default_val))


def next_defense(
    planet: Planet,
    cfg: Config,
    is_night_mode: bool = False,
    research_levels: Optional[Dict[str, int]] = None
) -> Optional[Tuple[str, int, gd.Cost]]:
    """
    Devuelve (nombre_defensa, cantidad, coste_total) o None.
    Calcula los objetivos para cada defensa y elige la que tenga menor porcentaje
    de completado (paralelo), ignorando las que no cumplen prerrequisitos.
    """
    if not getattr(cfg, "enable_defense", True):
        return None

    coords_str = f"{planet.coords.galaxy}:{planet.coords.system}:{planet.coords.position}"
    planets_config = getattr(cfg, "planets_config", {}) or {}
    p_cfg = planets_config.get(coords_str, {})
    defense_targets = p_cfg.get("defense_targets", {})
    if not defense_targets:
        return None

    if research_levels is None:
        research_levels = {}

    candidates = []
    for idx, name in enumerate(_DEFENSE_LIST):
        # Omitir si no se cumplen los prerrequisitos tecnológicos reales
        if not check_defense_prereqs(name, planet, research_levels):
            continue

        target = int(defense_targets.get(name, 0))
        if target <= 0:
            continue

        current = planet.defenses.get(name, 0)
        # Las cúpulas tienen un límite estricto de 1
        if name in ("small_shield_dome", "large_shield_dome"):
            target = min(target, 1)

        if current < target:
            pct = current / target
            candidates.append((pct, idx, name, target, current))

    if not candidates:
        return None

    # Ordenar por porcentaje de completado (menor primero), usando el índice original para desempatar
    candidates.sort(key=lambda x: (x[0], x[1]))
    _, _, best_name, target, current = candidates[0]

    # Para cúpulas el lote es 1, para otras defensas respetamos el batch size
    if best_name in ("small_shield_dome", "large_shield_dome"):
        count = 1
    else:
        batch = get_planet_defense_ratio(planet, cfg, "defense_batch_size", getattr(cfg, "defense_batch_size", 25))
        count = min(batch, int(target - current))

    cost = gd.defense_cost(best_name, count)
    return best_name, count, cost


def affordable_defense(
    planet: Planet,
    cfg: Config,
    is_night_mode: bool = False,
    research_levels: Optional[Dict[str, int]] = None
) -> Optional[Tuple[str, int, gd.Cost]]:
    """Como next_defense, pero reduce el lote si no hay recursos suficientes."""
    choice = next_defense(planet, cfg, is_night_mode, research_levels)
    if not choice:
        return None
    name, count, _ = choice
    # De noche, no dejamos buffer de seguridad para gastar lo máximo posible
    # (la reserva de ahorro del planeta sí se respeta siempre).
    avail = spendable_resources(planet, cfg, buf=1.0 if is_night_mode else None,
                                research_levels=research_levels)
    for n in range(count, 0, -1):
        cost = gd.defense_cost(name, n)
        if avail.can_afford(cost):
            return name, n, cost
    return None
