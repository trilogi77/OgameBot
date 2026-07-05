"""
brain.py
========
El "cerebro": orquesta toda la estrategia en un bucle que corre en segundo plano.

Cada CICLO (con intervalo aleatorio y dentro de tu franja horaria) hace, en orden
de prioridad estratégica:

  0. FLEETSAVE primero si toca terminar el ciclo / vas a estar offline.
  1. Leer estado (recursos, planetas, flota en movimiento).
  2. Reciclar campos de escombros propios pendientes.
  3. Cobrar/relanzar expediciones.
  4. Economía: subir la mejor mina/edificio por planeta (payback marginal).
  5. Investigación: siguiente tecnología por prioridad.
  6. Flota: fabricar cargueros/cazas según necesidad de farmeo.
  7. Farmeo: buscar objetivos (API), espiar, evaluar y atacar los rentables.
  8. Recoger loot (los ataques vuelven solos; el reciclaje se programa).
  9. Colonización si hay slot libre de astrofísica.
 10. Lunas (opcional) si está activado y hay oportunidad.
 11. FLEETSAVE final antes de "dormir".

Todo respeta dry_run, rate-limit y delays humanizados.
"""
from __future__ import annotations
import random
import time
from typing import Dict, List, Optional, Tuple
from .config import Config
from .client import GameClient
from .universe_api import UniverseAPI
from . import economy, research as research_mod, targets as tgt, fleet as fleet_mod, moons, gamedata as gd
from . import startorder
from . import combat
from .models import Coords, Resources, Planet
from . import utils
from .stats import StatsMixin

import re as _re

# Nombres EXACTOS de naves como los muestra OGame (es/en) en las "Naves
# encontradas" de un informe de expedición. El informe lista "Nombre <n>"
# (nombre y luego la cifra). \b evita que "cruiser" cace dentro de
# "battlecruiser" o "carga" en otra nave.
EXPEDITION_SHIP_NAMES = [
    ("large_cargo", ["nave grande de carga", "large cargo"]),
    ("small_cargo", ["nave pequeña de carga", "small cargo"]),
    ("light_fighter", ["cazador ligero", "light fighter"]),
    ("heavy_fighter", ["cazador pesado", "heavy fighter"]),
    ("cruiser", ["crucero", "cruiser"]),
    ("battleship", ["nave de batalla", "battleship"]),
    ("battlecruiser", ["acorazado", "battlecruiser"]),
    ("bomber", ["bombardero", "bomber"]),
    ("destroyer", ["destructor", "destroyer"]),
    ("deathstar", ["estrella de la muerte", "deathstar"]),
    ("reaper", ["segador", "reaper"]),
    ("pathfinder", ["explorador", "pathfinder"]),
    ("recycler", ["reciclador", "recycler"]),
    ("espionage_probe", ["sonda de espionaje", "espionage probe"]),
]


def parse_found_ships(text: str) -> dict:
    """{ship_key: cantidad} de las naves encontradas en un informe de expedición."""
    found = {}
    for ship_key, variants in EXPEDITION_SHIP_NAMES:
        for variant in variants:
            m = _re.search(r'\b' + _re.escape(variant) + r'\b\s*:?\s*\+?\s*([\d.,]+)',
                           text, _re.IGNORECASE)
            if m:
                qty = int(_re.sub(r'[^\d]', '', m.group(1)) or 0)
                if qty:
                    found[ship_key] = found.get(ship_key, 0) + qty
                break  # no contar es+en de la misma nave
    return found


# Cola de construcción: suelos de espera para no recargar/navegar en bucle (patrón de bot).
_QUEUE_RETRY_S = 120.0       # reintento corto (rate-limit, fallo de envío, fin impreciso)
_QUEUE_ETA_FLOOR_S = 120.0   # nunca despertar por "ahorro de recursos" antes de 2 min

# Misiones de OGame -> nombre legible (código numérico y alias de texto).
MISSION_NAMES_ES = {
    "1": "Ataque", "2": "Ataque (ACS)", "3": "Transporte", "4": "Despliegue",
    "5": "Defensa (ACS)", "6": "Espionaje", "7": "Colonización",
    "8": "Reciclaje", "9": "Destruir luna", "15": "Expedición",
    "attack": "Ataque", "transport": "Transporte", "deploy": "Despliegue",
    "espionage": "Espionaje", "colonize": "Colonización", "harvest": "Reciclaje",
    "expedition": "Expedición", "destroy": "Destruir luna",
}

# Nombres de recurso (es/en) tal como aparecen en el tooltip de la flota.
_CARGO_NAMES = {
    "metal": ["metal"],
    "crystal": ["cristal", "crystal"],
    "deut": ["deuterio", "deuterium", "deut"],
}


def _split_ships_cargo(ships_raw: dict):
    """Separa el tooltip de la flota en naves y carga (Metal/Cristal/Deuterio)."""
    ships, cargo = {}, {"metal": 0, "crystal": 0, "deut": 0}
    for name, val in (ships_raw or {}).items():
        low = str(name).strip().lower()
        matched = next((ck for ck, variants in _CARGO_NAMES.items() if low in variants), None)
        if matched:
            cargo[matched] += val
        elif name:
            ships[name] = ships.get(name, 0) + val
    return ships, cargo


def _route_key(f):
    return (str(f.get("mission_code", "")), f.get("origin", ""),
            f.get("destination", ""), bool(f.get("is_return")))


def _best_prev(f, prev_by_route, used):
    """Casa este vuelo con el MISMO vuelo físico del fichero previo: misma ruta/misión y la
    llegada más cercana (tolerancia 180 s para absorber el pequeño desfase entre la escritura
    del ciclo y la del chequeo de ataques). Cada vuelo previo se usa una sola vez para no
    confundir dos flotas distintas en la misma ruta (p.ej. dos expediciones)."""
    cands = prev_by_route.get(_route_key(f), [])
    fa = int(f.get("arrival_epoch") or 0)
    best, best_d = None, 181
    for p in cands:
        if id(p) in used:
            continue
        pa = int(p.get("arrival_epoch") or 0)
        d = abs(pa - fa) if (pa and fa) else 0
        if d < best_d:
            best, best_d = p, d
    if best is not None:
        used.add(id(best))
    return best


def _estimate_departures(flights, now):
    """Último recurso para la 'hora de vuelta si se recupera ahora' cuando no hay reversal del
    DOM ni pata de vuelta que emparejar (caso típico: el despliegue, de una sola ida): usa
    'first_seen' (la 1ª vez que vimos el vuelo) como salida estimada. Es exacto si el bot
    estaba corriendo cuando salió la flota; si ya estaba en vuelo al arrancar, subestima lo ya
    volado (la vuelta saldrá algo corta). Solo rellena los que aún no tengan salida."""
    for f in flights:
        if f.get("is_return") or int(f.get("departure_epoch") or 0) > 0:
            continue
        fs = int(f.get("first_seen") or 0)
        a = int(f.get("arrival_epoch") or 0)
        if fs and a and fs < a:
            f["departure_epoch"] = fs
            f["departure_estimated"] = True


_REV_DT = _re.compile(r'(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})\D+(\d{1,2}):(\d{2}):(\d{2})')
_REV_CLOCK = _re.compile(r'(?<!\d)(\d{1,2}):(\d{2}):(\d{2})(?!\d)')


def _clock_to_future_epoch(hh, mm, ss, now):
    """Epoch de la próxima ocurrencia (hoy o mañana) de una hora HH:MM:SS en hora local."""
    import time as _t
    lt = _t.localtime(now)
    try:
        cand = _t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, ss, 0, 0, -1))
    except (ValueError, OverflowError):
        return 0
    if cand <= now:
        cand += 86400
    return cand


def _reversal_departure(reversal_epoch, reversal_text, now):
    """Estima la SALIDA de la flota (epoch) a partir de la info de 'reversal' de OGame (la
    hora a la que volvería si se recupera ahora). Con ella la GUI calcula en vivo esa hora:
    vuelve_en(t) = t + (t - salida). Devuelve 0 si no se pudo determinar.

    Acepta varios formatos: epoch absoluto (data-*), epoch en el texto, fecha+hora absoluta
    'DD.MM.YYYY HH:MM:SS', o solo 'HH:MM:SS' (probando como hora absoluta y, si no encaja,
    como contador del regreso = lo ya volado)."""
    def from_abs(ep):
        dep = round(2 * now - ep)
        return dep if now - 31 * 86400 < dep < now else 0

    try:
        ep = int(reversal_epoch or 0)
    except (TypeError, ValueError):
        ep = 0
    if ep > 1_000_000_000:
        return from_abs(ep)

    txt = (reversal_text or "").strip()
    if not txt:
        return 0
    m = _re.search(r'\b(1\d{9})\b', txt)            # epoch incrustado en el texto
    if m:
        return from_abs(int(m.group(1)))
    m = _REV_DT.search(txt)                          # fecha + hora absoluta
    if m:
        import time as _t
        d, mo, y, hh, mm, ss = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
        try:
            return from_abs(_t.mktime((y, mo, d, hh, mm, ss, 0, 0, -1)))
        except (ValueError, OverflowError):
            pass
    m = _REV_CLOCK.search(txt)                       # solo HH:MM:SS
    if m:
        hh, mm, ss = (int(m.group(i)) for i in (1, 2, 3))
        abs_ep = _clock_to_future_epoch(hh, mm, ss, now)   # (a) hora absoluta de regreso
        if abs_ep:
            dep = from_abs(abs_ep)
            if dep:
                return dep
        secs = hh * 3600 + mm * 60 + ss                    # (b) contador del regreso
        if 0 < secs < 2592000:
            return round(now - secs)
    return 0


def _dedup_flights(flights):
    """Colapsa vuelos que son el MISMO evento físico leído dos veces (misma misión, ruta y
    llegada EXACTA). OGame a veces expone una fila de "vuelta" espuria junto a la de ida con
    idéntica hora de llegada; las patas de ida y vuelta REALES tienen llegadas distintas y NO
    se tocan. Al fusionar gana: la fila con datos (naves/carga), el tipo de cuerpo más
    específico (luna/escombros sobre el 'planeta' por defecto) y la ida sobre la vuelta."""
    seen, out = {}, []
    for f in flights:
        ep = int(f.get("arrival_epoch") or 0)
        # Si no hay llegada (ni epoch ni texto), usar una identidad única para NO fusionar dos
        # flotas distintas por tener ambas la llegada en blanco.
        key = (str(f.get("mission_code", "")), f.get("origin", ""),
               f.get("destination", ""), ep if ep else (f.get("arrival_text") or f"id{id(f)}"))
        a = seen.get(key)
        if a is None:
            seen[key] = f
            out.append(f)
            continue
        if not a.get("ships") and f.get("ships"):
            a["ships"] = f["ships"]
        if not any((a.get("cargo") or {}).values()) and any((f.get("cargo") or {}).values()):
            a["cargo"] = f["cargo"]
        for k in ("origin_type", "dest_type"):
            if a.get(k, "planet") == "planet" and f.get(k, "planet") != "planet":
                a[k] = f[k]
        if a.get("is_return") and not f.get("is_return"):
            a["is_return"] = False
        a["is_hostile"] = a.get("is_hostile") or f.get("is_hostile")
        if not a.get("departure_epoch") and f.get("departure_epoch"):
            a["departure_epoch"] = f["departure_epoch"]
            if f.get("departure_estimated"):
                a["departure_estimated"] = True
        if not a.get("return_arrival_epoch") and f.get("return_arrival_epoch"):
            a["return_arrival_epoch"] = f["return_arrival_epoch"]
            a["return_arrival_text"] = f.get("return_arrival_text", "")
        fs_a, fs_f = int(a.get("first_seen") or 0), int(f.get("first_seen") or 0)
        if fs_f and (not fs_a or fs_f < fs_a):
            a["first_seen"] = fs_f   # conserva la 1ª vez visto más temprana (salida estimada)
    return out


def _link_round_trips(flights):
    """Agrupa cada vuelo de ida con su pata de vuelta vinculada (misma misión y ruta, con
    llegada posterior) en UNA sola tarjeta, para no mostrarlas separadas:
      - la ida hereda la hora de vuelta a casa  -> return_arrival_epoch/text
      - si falta, deriva la SALIDA (para la 'hora de vuelta si se recupera ahora'): viaje
        simétrico sin estancia, salida = 2*llegada_ida − llegada_vuelta
      - la pata de vuelta se descarta de la lista
    La vuelta sin ida visible (la ida ya llegó) se conserva como tarjeta propia. El despliegue,
    de una sola ida, no tiene vuelta que emparejar: su hora de regreso necesita el reversal del
    juego (capturable con OGBOT_DUMP_MOVEMENTS=1)."""
    # Emparejado FIFO: la ida más temprana con la vuelta más temprana de la misma ruta. Así no
    # se cruza el orden cuando hay varias flotas iguales (p.ej. dos expediciones al mismo sitio).
    ep = lambda x: int(x.get("arrival_epoch") or 0)
    returns = sorted((f for f in flights if f.get("is_return") and ep(f)), key=ep)
    outbounds = sorted((f for f in flights if not f.get("is_return") and ep(f)), key=ep)
    used = set()
    for f in outbounds:
        a1 = ep(f)
        for i, r in enumerate(returns):
            if i in used:
                continue
            a2 = ep(r)
            if (r.get("mission_code") == f.get("mission_code")
                    and r.get("origin") == f.get("origin")
                    and r.get("destination") == f.get("destination")
                    and a2 > a1):
                f["return_arrival_epoch"] = a2
                f["return_arrival_text"] = r.get("arrival_text", "")
                # Salida derivada (viaje simétrico SIN estancia): solo una ESTIMACIÓN, y errónea
                # en misiones con espera (expedición). Se descarta si sale negativa o anterior a
                # la 1ª vez vista; el reversal del DOM, si lo hay, ya habrá puesto la exacta.
                if not f.get("departure_epoch"):
                    dep = 2 * a1 - a2
                    fs = int(f.get("first_seen") or 0)
                    if dep > 0 and (not fs or dep >= fs):
                        f["departure_epoch"] = dep
                        f["departure_estimated"] = True
                used.add(i)
                break
    drop = {id(returns[i]) for i in used}
    return [f for f in flights if id(f) not in drop]


def build_flights(mvs, now, prev=None):
    """Convierte movimientos crudos de read_movements() en vuelos para la GUI.

    Excluye flotas hostiles entrantes (los ataques los gestiona el escape/Telegram): así
    las dos fuentes —la página de movimientos del ciclo y el event_list del chequeo de
    ataques— producen el MISMO conjunto (solo tus flotas) y no parpadean filas.

    prev: vuelos del fichero anterior. Si la fuente actual no trae naves/carga (el
    event_list no las trae), se conservan del vuelo previo equivalente (escrito por el
    ciclo con datos completos).
    """
    prev_by_route = {}
    for pf in (prev or []):
        prev_by_route.setdefault(_route_key(pf), []).append(pf)
    used = set()
    flights = []
    for mv in mvs:
        if mv.get("is_hostile"):
            continue
        ships, cargo = _split_ships_cargo(mv.get("ships", {}))
        arrival_text = (mv.get("arrival_text", "") or "").strip()
        # Preferir el epoch absoluto del DOM (estable y sin ambigüedad); si no, el contador.
        try:
            abs_ep = int(mv.get("arrival_epoch") or 0)
        except (TypeError, ValueError):
            abs_ep = 0
        if abs_ep > 0:
            arrival_epoch = abs_ep
        else:
            secs = parse_time_to_seconds(arrival_text)
            arrival_epoch = round(now + secs) if secs is not None else 0
        # Salida estimada (para la hora de vuelta si se recupera), derivada del 'reversal'.
        departure_epoch = _reversal_departure(mv.get("reversal_epoch"), mv.get("reversal_text"), now)
        mcode = str(mv.get("mission", ""))
        f = {
            "mission": MISSION_NAMES_ES.get(mcode, mcode or "?"),
            "mission_code": mcode,
            "origin": mv.get("origin", ""),
            "origin_type": mv.get("origin_type", "planet"),
            "destination": mv.get("destination", ""),
            "dest_type": mv.get("dest_type", "planet"),
            "is_return": bool(mv.get("is_return")),
            "is_hostile": False,
            "arrival_text": arrival_text,
            "arrival_epoch": arrival_epoch,
            "departure_epoch": departure_epoch,
            "reversal_raw": (mv.get("reversal_text", "") or "")[:80],   # para depurar el formato
            "ships": ships,
            "cargo": cargo,
            "first_seen": 0,
        }
        # Casar con el MISMO vuelo del fichero previo (misma ruta, llegada cercana) para:
        #  - conservar naves/carga del ciclo cuando esta lectura no las trae (event_list),
        #  - ESTABILIZAR la llegada (evita el "sube y baja" del contador entre fuentes),
        #  - arrastrar 'first_seen' (1ª vez visto = salida estimada del despliegue).
        pm = _best_prev(f, prev_by_route, used)
        if pm is not None:
            if not ships and not any(cargo.values()):
                f["ships"] = pm.get("ships", {}) or {}
                f["cargo"] = pm.get("cargo", {}) or {"metal": 0, "crystal": 0, "deut": 0}
            if abs_ep <= 0 and int(pm.get("arrival_epoch") or 0) > 0:
                f["arrival_epoch"] = int(pm["arrival_epoch"])
            f["first_seen"] = int(pm.get("first_seen") or 0) or now
            # Conservar la SALIDA y la vuelta a casa ya calculadas. El ciclo (página de
            # movimientos) lee el reversal y la pata de vuelta; el event_list no, así que sin
            # esto el dato exacto parpadearía y se degradaría a estimado en cada lectura ligera.
            if not f.get("departure_epoch") and int(pm.get("departure_epoch") or 0) > 0:
                f["departure_epoch"] = int(pm["departure_epoch"])
                if pm.get("departure_estimated"):
                    f["departure_estimated"] = True
            if not f.get("return_arrival_epoch") and int(pm.get("return_arrival_epoch") or 0) > 0:
                f["return_arrival_epoch"] = int(pm["return_arrival_epoch"])
                f["return_arrival_text"] = pm.get("return_arrival_text", "")
        else:
            f["first_seen"] = now
        flights.append(f)
    flights = _dedup_flights(flights)
    flights = _link_round_trips(flights)
    _estimate_departures(flights, now)
    return flights


def _retain_unlanded(new_flights, prev, now):
    """Evita que el panel de Vuelos se vacíe por una lectura de movimientos fallida o
    transitoria (navegación/selector). Si la lista nueva viene VACÍA, conserva del fichero
    previo los vuelos cuya llegada (o vuelta a casa) aún está en el futuro; los ya aterrizados
    se descartan, de modo que cuando de verdad no hay flotas la lista se vacía sola en cuanto
    pasan sus horas de llegada. Si la lista nueva trae datos, se usa tal cual."""
    if new_flights:
        return new_flights
    kept = []
    for f in (prev or []):
        horizon = max(int(f.get("return_arrival_epoch") or 0), int(f.get("arrival_epoch") or 0))
        if horizon and horizon > now:
            kept.append(f)
    return kept


class Brain(StatsMixin):
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = utils.setup_logger("ogbot", cfg.log_file, cfg.log_level)
        self._apply_probe_cargo(warn=True)
        self._apply_empire_auto()
        self.client = GameClient(cfg, self.log)
        self.api = UniverseAPI(cfg.server_url, logger=self.log) if cfg.server_url else None
        self.rate = utils.RateLimiter(cfg.max_actions_per_hour)
        self.my_tech = combat.Tech()
        self.research_levels: Dict[str, int] = {}
        self.running = True
        self.last_planets: List = []
        self.active_slots = 0
        self.active_expe_slots = 0
        self.total_fleet_slots = 0
        self.total_expe_slots: Optional[int] = None   # None = aún no leído del juego
        self.escaped_fleets: List[dict] = []
        self.sent_fleetsaves: List[dict] = []   # fleetsaves nocturnos enviados (para el recall)
        self.inflight_dests: Dict[str, set] = {}  # destino "g:s:p" -> misiones propias en vuelo
        self.attack_history: Dict[str, float] = {}
        self.telegram_notified_attacks: Dict[str, float] = {}
        self._spy_seen: Dict[str, float] = {}   # cooldown de avisos de espionaje por origen->destino
        self.last_economy_run_time = 0.0
        self.last_farming_run_time = 0.0
        self.last_expeditions_run_time = 0.0
        self.last_recycling_run_time = 0.0
        # Expediciones: rotación de sistemas + reactivación por vuelta/fin
        self.expedition_rotation_index = 0
        self.next_expedition_event = 0.0
        self._expedition_returns: List[float] = []
        self._expedition_top1_cache: Tuple[float, int] = (0.0, 0)
        self.expedition_flight_cal = 1.0  # factor real/estimado de vuelo (autocalibrado)
        # Farmeo: reactivación por vuelta de flota + historial de botín real por objetivo
        self.next_farming_event = 0.0
        self._farming_returns: List[float] = []
        self.target_stats: Dict[str, dict] = {}  # "g:s:p" -> {raids, loot (valor real), last}
        self.next_build_event = 0.0       # despertar para encolar la siguiente construcción
        self.paused_until = 0.0           # pausa remota (/pausa por Telegram); persistido
        self._tg_offset = 0               # offset de getUpdates de Telegram; persistido
        self._tg_last_poll = 0.0          # throttle del sondeo de comandos Telegram
        self._phalanx_warned = set()      # avisos de phalanx: uno por origen y noche
        self._last_history_date = ""      # última fecha volcada a stats_history.jsonl
        self._next_cycle_eta = 0.0        # hora aprox. del próximo ciclo (para /status)
        self.last_hostile_epoch = 0.0     # última actividad hostil vista (ataque/sondeo); persistido
        self._last_hourly = ""            # última hora (YYYY-MM-DD HH) volcada a stats_hourly.jsonl
        self.started_at = time.time()     # arranque del proceso (para /api/botstatus); siempre nuevo
        self.player_id = ""               # id/nombre del jugador (de los meta del juego, vía client)
        self.player_name = ""
        self._load_state()
        # Memoria de estado (niveles de edificios/investigación/defensas)
        self.state_cache = {"research": {}, "planets": {}}
        self._force_resync = False        # GUI: releer TODOS los niveles del juego
        self._resync_targets = set()      # GUI: releer solo estas ubicaciones (loc_key)
        self._load_state_cache()

    def _load_state(self):
        import json
        import os
        if os.path.exists(self.cfg.state_file):
            try:
                with open(self.cfg.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.attack_history = data.get("attack_history", {})
                    self.telegram_notified_attacks = data.get("telegram_notified_attacks", {})
                    self._spy_seen = data.get("spy_seen", {})
                    self.last_economy_run_time = data.get("last_economy_run_time", 0.0)
                    self.last_farming_run_time = data.get("last_farming_run_time", 0.0)
                    self.last_expeditions_run_time = data.get("last_expeditions_run_time", 0.0)
                    self.last_recycling_run_time = data.get("last_recycling_run_time", 0.0)
                    self.expedition_rotation_index = data.get("expedition_rotation_index", 0)
                    self.next_expedition_event = data.get("next_expedition_event", 0.0)
                    self._expedition_returns = data.get("expedition_returns", [])
                    self.expedition_flight_cal = data.get("expedition_flight_cal", 1.0)
                    self.next_farming_event = data.get("next_farming_event", 0.0)
                    self._farming_returns = data.get("farming_returns", [])
                    self.target_stats = data.get("target_stats", {}) or {}
                    self.sent_fleetsaves = data.get("sent_fleetsaves", []) or []
                    self.paused_until = float(data.get("paused_until", 0.0) or 0.0)
                    self._tg_offset = int(data.get("tg_offset", 0) or 0)
                    self._last_history_date = data.get("last_history_date", "") or ""
                    self.last_hostile_epoch = float(data.get("last_hostile_epoch", 0.0) or 0.0)
                    self._last_hourly = data.get("last_hourly", "") or ""
                    self.player_id = str(data.get("player_id", "") or "")
                    self.player_name = str(data.get("player_name", "") or "")
                    # started_at NO se carga: cada proceso nuevo escribe el suyo.
                    # Coords no es JSON-serializable: se guarda como dict plano y se reconstruye.
                    self.escaped_fleets = []
                    for e in data.get("escaped_fleets", []) or []:
                        try:
                            self.escaped_fleets.append({
                                "origin": Coords(int(e["origin"]["galaxy"]), int(e["origin"]["system"]),
                                                 int(e["origin"]["position"]), e["origin"].get("type", "planet")),
                                "destination": Coords(int(e["destination"]["galaxy"]), int(e["destination"]["system"]),
                                                      int(e["destination"]["position"]), e["destination"].get("type", "planet")),
                                "escaped_at": float(e.get("escaped_at", 0.0)),
                                "is_sibling": bool(e.get("is_sibling", False)),
                            })
                        except Exception:
                            continue
                    self.log.info("Historial de cooldown de ataques cargado: %d registros.", len(self.attack_history))
            except Exception as e:
                self.log.debug("No se pudo cargar state.json: %s", e)

    def _save_state(self):
        import json
        try:
            # Limpiar notificaciones antiguas (más de 2 horas)
            now = time.time()
            self.telegram_notified_attacks = {
                k: arr_epoch for k, arr_epoch in self.telegram_notified_attacks.items()
                if arr_epoch > now - 7200
            }
            spy_cd = max(3600, int(getattr(self.cfg, "spy_watch_cooldown_mins", 30)) * 60)
            self._spy_seen = {k: t for k, t in self._spy_seen.items() if t > now - spy_cd}

            def c2d(c):
                return {"galaxy": c.galaxy, "system": c.system, "position": c.position, "type": c.type}
            # id/nombre del jugador: si el client ya los leyó de los meta, quedan persistidos.
            self.player_id = str(getattr(self.client, "player_id", "") or self.player_id or "")
            self.player_name = str(getattr(self.client, "player_name", "") or self.player_name or "")
            utils.atomic_write_json(self.cfg.state_file, {
                "attack_history": self.attack_history,
                "telegram_notified_attacks": self.telegram_notified_attacks,
                "spy_seen": self._spy_seen,
                "last_economy_run_time": self.last_economy_run_time,
                "last_farming_run_time": self.last_farming_run_time,
                "last_expeditions_run_time": self.last_expeditions_run_time,
                "last_recycling_run_time": self.last_recycling_run_time,
                "expedition_rotation_index": self.expedition_rotation_index,
                "next_expedition_event": self.next_expedition_event,
                "expedition_returns": [e for e in self._expedition_returns if e > now],
                "expedition_flight_cal": self.expedition_flight_cal,
                "next_farming_event": self.next_farming_event,
                "farming_returns": [e for e in self._farming_returns if e > now],
                # Historial de botín real por granja; se podan entradas sin raids en 90 días
                "target_stats": {k: v for k, v in self.target_stats.items()
                                 if float(v.get("last", 0.0) or 0.0) > now - 90 * 86400},
                "sent_fleetsaves": [e for e in self.sent_fleetsaves
                                    if now - e.get("sent_at", 0) <= 86400],
                "paused_until": self.paused_until,
                "tg_offset": self._tg_offset,
                "last_history_date": self._last_history_date,
                "last_hostile_epoch": self.last_hostile_epoch,
                "last_hourly": self._last_hourly,
                "started_at": self.started_at,
                "player_id": self.player_id,
                "player_name": self.player_name,
                "escaped_fleets": [{"origin": c2d(e["origin"]),
                                    "destination": c2d(e["destination"]),
                                    "escaped_at": e.get("escaped_at", 0.0),
                                    "is_sibling": bool(e.get("is_sibling", False))}
                                   for e in self.escaped_fleets],
            })
        except Exception as e:
            self.log.debug("No se pudo guardar state.json: %s", e)

    # ------------------------------------------------------------------ #
    #  Memoria de estado: caché de niveles (edificios/investigación/defensas)
    # ------------------------------------------------------------------ #
    STATE_CACHE_FILE = "game_state_cache.json"

    def _loc_key(self, coords) -> str:
        return f"{coords.galaxy}:{coords.system}:{coords.position}:{coords.type}"

    def _build_finish_pending(self, loc) -> bool:
        """True si la caché sabe de un build en esta ubicación cuyo fin estimado aún no ha
        llegado. Red de seguridad para cuando la lectura en vivo de building_in_progress da
        un falso negativo (el overview hace fail-open)."""
        entry = self.state_cache["planets"].get(self._loc_key(loc.coords))
        if not entry:
            return False
        return (entry.get("build_finish_epoch", 0.0) or 0.0) > time.time()

    def _load_state_cache(self):
        import json
        import os
        self.state_cache = {"research": {}, "planets": {}}
        if os.path.exists(self.STATE_CACHE_FILE):
            try:
                with open(self.STATE_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.state_cache["research"] = data.get("research", {}) or {}
                self.state_cache["planets"] = data.get("planets", {}) or {}
                self.log.info("Caché de estado cargada: %d ubicaciones.", len(self.state_cache["planets"]))
            except Exception as e:
                self.log.debug("No se pudo cargar game_state_cache.json: %s", e)

    def _save_state_cache(self):
        try:
            utils.atomic_write_json(self.STATE_CACHE_FILE, self.state_cache)
        except Exception as e:
            self.log.debug("No se pudo guardar game_state_cache.json: %s", e)

    def _process_recall_requests(self):
        """Ejecuta los regresos de flota pedidos desde la GUI (recall_requests.json). Se
        llama con frecuencia (tick del bucle) para que el regreso sea casi inmediato.
        Reclama el fichero de forma atómica (rename) para no perder peticiones que la GUI
        escriba justo a la vez, y re-encola las que fallen (hasta 3 intentos)."""
        import json
        import os
        src = "recall_requests.json"
        if not os.path.exists(src):
            return
        work = "recall_requests.processing.json"
        try:
            os.replace(src, work)   # lo que escriba la GUI a partir de ahora va a un fichero nuevo
        except Exception:
            return
        try:
            with open(work, "r", encoding="utf-8") as f:
                reqs = json.load(f) or []
        except Exception:
            reqs = []

        failed = []
        for r in reqs:
            origin = str((r or {}).get("origin", "")).strip()
            dest = str((r or {}).get("destination", "")).strip()
            mission = str((r or {}).get("mission_code", "")).strip()
            try:
                arrival = int((r or {}).get("arrival") or 0)
            except (TypeError, ValueError):
                arrival = 0
            attempts = int((r or {}).get("_attempts", 0) or 0)
            if not origin or not dest:
                continue
            self.log.info("Regreso de flota pedido desde la GUI: %s -> %s (misión %s).", origin, dest, mission)
            ok = False
            try:
                ok = self.client.recall_fleet(origin, dest, mission=mission, arrival=arrival)
            except Exception as e:
                self.log.warning("Error al recuperar la flota %s -> %s: %s", origin, dest, e)
            if ok:
                self.log.info("Regreso %s -> %s ejecutado.", origin, dest)
            elif attempts + 1 < 3:
                r["_attempts"] = attempts + 1
                failed.append(r)
                self.log.info("Regreso %s -> %s no encontrado todavía; reintento %d/3.",
                              origin, dest, attempts + 1)
            else:
                self.log.warning("Regreso %s -> %s descartado tras 3 intentos.", origin, dest)

        try:
            os.remove(work)
        except Exception:
            pass
        # Re-encolar los fallidos junto a las peticiones nuevas llegadas entretanto.
        if failed:
            try:
                existing = []
                if os.path.exists(src):
                    with open(src, "r", encoding="utf-8") as f:
                        existing = json.load(f) or []
                utils.atomic_write_json(src, failed + existing)
            except Exception as e:
                self.log.debug("No se pudo re-encolar regresos fallidos: %s", e)

    def _process_control_requests(self):
        """Ejecuta los comandos remotos de la GUI (bot_control.json, contrato C1):
        pause/resume/screenshot/restart_session/close_browser. Mismo patrón que
        _process_recall_requests: reclama el fichero de forma atómica (rename), ejecuta
        los comandos, y escribe el resultado en bot_control_result.json."""
        import json
        import os
        src = "bot_control.json"
        if not os.path.exists(src):
            return
        work = "bot_control.processing.json"
        try:
            os.replace(src, work)   # lo que escriba la GUI a partir de ahora va a un fichero nuevo
        except Exception:
            return
        try:
            with open(work, "r", encoding="utf-8") as f:
                commands = (json.load(f) or {}).get("commands", []) or []
        except Exception:
            commands = []
        try:
            os.remove(work)
        except Exception:
            pass
        # La GUI reescribe el fichero completo al encolar (read-modify-write): si lee
        # justo antes de nuestro os.replace puede reintroducir comandos ya consumidos.
        # Dedup por id de los últimos ejecutados para no repetirlos.
        done = getattr(self, "_control_ids_done", None)
        if done is None:
            done = self._control_ids_done = []
        commands = [c for c in commands if str((c or {}).get("id", "")) not in done]
        for c in commands:
            cmd = str((c or {}).get("cmd", "")).strip()
            cid = str((c or {}).get("id", ""))
            done.append(cid)
            del done[:-50]
            arg = (c or {}).get("arg")
            ok, detail, file_out = True, "", None
            self.log.info("Comando de la GUI: %s (id %s).", cmd, cid)
            try:
                if cmd == "pause":
                    try:
                        mins = max(1, int(arg))
                    except (TypeError, ValueError):
                        mins = 60
                    self.paused_until = time.time() + mins * 60
                    detail = f"pausado {mins} min (hasta {time.strftime('%H:%M', time.localtime(self.paused_until))})"
                elif cmd == "resume":
                    self.paused_until = 0.0
                    detail = "reanudado"
                elif cmd == "screenshot":
                    os.makedirs("gui_captures", exist_ok=True)
                    file_out = f"gui_captures/capture_{int(time.time())}.png"
                    self.client.page.screenshot(path=file_out)
                    detail = "captura guardada"
                elif cmd == "restart_session":
                    self.client.stop()
                    self.client.start()
                    ok = bool(self.client.login())
                    detail = "sesión reiniciada" if ok else "login fallido tras reiniciar"
                elif cmd == "close_browser":
                    self.client.stop()
                    self.paused_until = time.time() + 12 * 3600
                    detail = "navegador cerrado; pausado 12h (se reanuda con resume)"
                else:
                    ok, detail = False, f"comando desconocido: {cmd}"
            except Exception as e:
                ok, detail, file_out = False, str(e), None
                self.log.warning("Comando de la GUI %s falló: %s", cmd, e)
            self._save_state()
            try:
                utils.atomic_write_json("bot_control_result.json", {
                    "last": {"id": cid, "cmd": cmd, "ok": ok, "detail": detail,
                             "ts": int(time.time()), "file": file_out}})
            except Exception as e:
                self.log.debug("No se pudo escribir bot_control_result.json: %s", e)

    def _apply_pending_gui_requests(self):
        """Aplica peticiones dejadas por la GUI como ficheros: corrección manual de
        niveles (state_overrides.json) y re-lectura forzada (force_resync.json)."""
        import json
        import os
        # 1) Correcciones manuales de nivel.
        if os.path.exists("state_overrides.json"):
            try:
                with open("state_overrides.json", "r", encoding="utf-8") as f:
                    overrides = json.load(f) or []
                for ov in overrides:
                    name = (ov.get("name") or "").strip()
                    if not name:
                        continue
                    try:
                        level = int(ov.get("level"))
                    except (TypeError, ValueError):
                        continue
                    if ov.get("kind") == "research":
                        self.state_cache.setdefault("research", {}).setdefault("levels", {})[name] = level
                        self.log.info("Corrección manual (GUI): investigación %s -> nivel %d", name, level)
                    else:
                        coords = (ov.get("coords") or "").strip()
                        key = f"{coords}:{'moon' if ov.get('is_moon') else 'planet'}"
                        entry = self.state_cache["planets"].get(key)
                        if entry is not None:
                            entry.setdefault("buildings", {})[name] = level
                            self.log.info("Corrección manual (GUI): %s %s -> nivel %d", key, name, level)
                        else:
                            self.log.info("Corrección manual (GUI) ignorada: %s aún no está en caché.", key)
                self._save_state_cache()
            except Exception as e:
                self.log.debug("No se pudieron aplicar state_overrides.json: %s", e)
            try:
                os.remove("state_overrides.json")
            except Exception:
                pass
        # 2) Re-lectura forzada (toda la cuenta o solo ciertas ubicaciones).
        if os.path.exists("force_resync.json"):
            try:
                with open("force_resync.json", "r", encoding="utf-8") as f:
                    req = json.load(f)
            except Exception:
                req = {}
            if isinstance(req, dict):
                if req.get("all"):
                    self._force_resync = True
                    self.log.info("Re-lectura forzada de TODOS los niveles (GUI).")
                for k in req.get("targets", []) or []:
                    self._resync_targets.add(k)
                    self.log.info("Re-lectura forzada de %s (GUI).", k)
            try:
                os.remove("force_resync.json")
            except Exception:
                pass

    def _cache_store_location(self, loc):
        self.state_cache["planets"][self._loc_key(loc.coords)] = {
            "buildings": dict(loc.buildings),
            "defenses": dict(loc.defenses),
            "lifeform_available": loc.lifeform_available,
            "scanned_at": time.time(),
            "build_queue": list(loc.building_queue),
            "build_finish_epoch": (time.time() + loc.building_remaining_seconds) if loc.building_in_progress else 0.0,
        }
        self._save_state_cache()

    def _refresh_buildings(self, loc):
        """Re-lee SOLO los niveles de edificios de esta ubicación (sin tocar lo demás)."""
        components = ["facilities"]
        if loc.coords.type == "planet":
            components.append("supplies")
        for comp in components:
            try:
                data = self.client._read_tech_page(comp, loc)
                loc.buildings.update(data)
            except Exception as e:
                self.log.debug("No se pudieron refrescar edificios (%s) en %s: %s", comp, loc.coords, e)

    def _read_location_state(self, loc):
        """
        Lee el estado de una ubicación CON memoria: escaneo completo al inicio y en el
        resync periódico; en el resto de ciclos solo recursos+cola+naves en vivo,
        hidratando edificios/defensas desde la caché (decide 'a tiro hecho' sin ramas).
        """
        if not getattr(self.cfg, "enable_state_cache", True):
            self.client.read_planet_state(loc)
            return
        key = self._loc_key(loc.coords)
        entry = self.state_cache["planets"].get(key)
        resync_s = float(getattr(self.cfg, "state_resync_hours", 6.0)) * 3600
        needs_full = (self._force_resync or key in self._resync_targets or entry is None
                      or (time.time() - entry.get("scanned_at", 0.0) >= resync_s))

        if needs_full:
            self.client.read_planet_state(loc)
            self._cache_store_location(loc)
            return

        # Hidratar niveles desde la caché y leer solo lo que cambia en vivo
        loc.buildings.update(entry.get("buildings", {}))
        loc.defenses.update(entry.get("defenses", {}))
        loc.lifeform_available = entry.get("lifeform_available", loc.lifeform_available)
        # ponytail: en ciclos ligeros no leemos la cola de formas de vida; se evalúan
        # en los ciclos de resync. Marcar ocupada evita encolar dos veces.
        loc.lifeform_in_progress = True
        self.client.read_planet_light(loc)

        finished = entry.get("build_finish_epoch", 0.0)
        now = time.time()
        if finished and now >= finished and not loc.building_in_progress:
            self.log.info("Estado %s: construcción terminada -> refresco niveles de edificios.", loc.coords)
            self._refresh_buildings(loc)
            self._cache_store_location(loc)
        else:
            entry["build_queue"] = list(loc.building_queue)
            if loc.building_in_progress:
                entry["build_finish_epoch"] = now + loc.building_remaining_seconds
            elif finished and now < finished:
                # La lectura en vivo del overview dice "libre", pero la caché sabe de un build
                # cuyo fin estimado aún no ha llegado. El overview hace fail-open (carreras de
                # render/navegación -> False), así que esto suele ser un FALSO NEGATIVO:
                # conservamos el epoch y mantenemos el planeta ocupado para no alimentar/encolar
                # de más.
                # ponytail: techo = si el build se acelera (materia oscura) el planeta queda
                # "ocupado" hasta el epoch o el próximo resync; se autocorrige.
                loc.building_in_progress = True
                loc.building_remaining_seconds = int(finished - now)
            else:
                entry["build_finish_epoch"] = 0.0
            self._save_state_cache()

    def _read_research_smart(self):
        """Niveles de investigación desde caché; re-lee en resync o cuando una termina."""
        if not getattr(self.cfg, "enable_state_cache", True):
            return self.client.read_research()
        r = self.state_cache.setdefault("research", {})
        levels = r.get("levels") or {}
        resync_s = float(getattr(self.cfg, "state_resync_hours", 6.0)) * 3600
        needs_full = (self._force_resync or not levels
                      or (time.time() - r.get("scanned_at", 0.0) >= resync_s))
        finish = r.get("finish_epoch", 0.0)
        if finish and time.time() >= finish:
            needs_full = True
        if needs_full:
            fresh = self.client.read_research()
            if fresh:
                r["levels"] = fresh
                r["scanned_at"] = time.time()
                r["finish_epoch"] = 0.0
                r["tech"] = ""
                self._save_state_cache()
                return fresh
        return dict(levels)

    def _cache_bump_defense(self, planet, name, count):
        """Sube el contador de una defensa en la caché tras construirla."""
        if not getattr(self.cfg, "enable_state_cache", True):
            return
        entry = self.state_cache["planets"].get(self._loc_key(planet.coords))
        if entry is not None:
            d = entry.setdefault("defenses", {})
            d[name] = d.get(name, 0) + count
            self._save_state_cache()

    def _mark_build_started(self, loc, name: str, cost) -> float:
        """Marca la construcción recién iniciada en el planeta Y en la caché.
        El overview hace fail-open al leer building_in_progress: sin este apunte,
        el ciclo siguiente cree el planeta libre y re-encola el mismo nivel una y
        otra vez. Devuelve la duración estimada en segundos."""
        secs = gd.building_time(cost, loc.lvl("robotics_factory"),
                                loc.lvl("nanite_factory"), self.cfg.universe_speed)
        loc.building_in_progress = True
        loc.building_remaining_seconds = int(secs)
        if getattr(self.cfg, "enable_state_cache", True):
            entry = self.state_cache["planets"].get(self._loc_key(loc.coords))
            if entry is not None:
                entry["build_finish_epoch"] = time.time() + secs
                entry["build_queue"] = [name]
                self._save_state_cache()
        return secs

    def _mark_ships_started(self, loc, ship: str, count: int):
        """Marca el astillero como ocupado en la caché tras encargar un lote: las
        naves en cola no aparecen ni en el planeta ni en vuelo, así que sin esto
        el ciclo siguiente volvería a encargar el mismo lote."""
        if not getattr(self.cfg, "enable_state_cache", True):
            return
        unit = gd.SHIPS[ship].cost
        total = gd.Cost(unit.metal * count, unit.crystal * count, unit.deut * count)
        # ponytail: la fórmula de edificios con nivel de astillero aproxima bien el
        # tiempo de naves; solo se usa como pacing, no hace falta exactitud
        secs = gd.building_time(total, loc.lvl("shipyard"),
                                loc.lvl("nanite_factory"), self.cfg.universe_speed)
        entry = self.state_cache["planets"].get(self._loc_key(loc.coords))
        if entry is not None:
            entry["shipyard_finish_epoch"] = time.time() + secs
            self._save_state_cache()

    def _shipyard_pending(self, loc) -> bool:
        """True si el astillero de esta ubicación sigue (estimado) fabricando un lote."""
        entry = self.state_cache["planets"].get(self._loc_key(loc.coords)) or {}
        return (entry.get("shipyard_finish_epoch", 0.0) or 0.0) > time.time()

    def _write_auto_plan(self, planets):
        """Publica auto_plan.json: el plan de decisiones del modo automático — qué
        subiría el bot en cada planeta (simulando economy.next_build con niveles
        incrementados, sin gastar recursos), próximas investigaciones y déficits
        de la auto-flota. Es una previsión: cada ciclo re-decide con los recursos
        reales en mano."""
        import copy
        try:
            plasma = self.research_levels.get("plasma_tech", 0)
            plan = {"generated_at": int(time.time()), "planets": [], "research": [], "fleet": []}
            for p in planets:
                sim = copy.copy(p)
                sim.buildings = dict(p.buildings)
                sim.building_queue = list(p.building_queue)
                steps = []
                for _ in range(8):
                    choice = economy.next_build(sim, self.cfg, plasma=plasma,
                                                research_levels=self.research_levels)
                    if not choice:
                        break
                    name, _cost = choice
                    lvl = sim.lvl(name) + 1
                    steps.append({"action": name, "level": lvl})
                    # Avanzar por la COLA, no por los niveles: energy_balance se ancla
                    # en la energía real y solo descuenta lo encolado (lvl() = niveles
                    # + cola); si tocáramos buildings, el balance nunca mejoraría y el
                    # plan repetiría "planta solar" hasta agotar los pasos.
                    sim.building_queue.append(name)
                plan["planets"].append({
                    "coords": f"{p.coords.galaxy}:{p.coords.system}:{p.coords.position}",
                    "name": p.name, "steps": steps})
            # Investigación: siguientes técnicas desde el mejor laboratorio
            if planets:
                best = max(planets, key=lambda x: x.lvl("research_lab"))
                levels = dict(self.research_levels)
                for _ in range(5):
                    ch = research_mod.next_research(levels, best, self.cfg)
                    if not ch:
                        break
                    lvl = levels.get(ch[0], 0) + 1
                    entry = {"tech": ch[0], "level": lvl}
                    if ch[2] is not None:
                        entry["blocked_lab"] = ch[2]   # esperando ese nivel de laboratorio
                        plan["research"].append(entry)
                        break
                    plan["research"].append(entry)
                    levels[ch[0]] = lvl
            # Flota: déficits de la auto-gestión (objetivo vs naves actuales)
            if getattr(self.cfg, "fleet_auto_build", False):
                eligible = [p for p in planets if p.lvl("shipyard") >= 1]
                if eligible:
                    home = max(eligible, key=lambda x: x.lvl("shipyard"))
                    expe_total = 0
                    if (getattr(self.cfg, "fleet_priority", "economy") or "").lower() == "expeditions":
                        expe_total = self._expedition_optimal_cargo_total()
                    targets = fleet_mod.auto_fleet_targets(
                        home, planets, self.research_levels, self.cfg, expe_total)
                    for ship, target in targets.items():
                        have = sum(pl.ships.get(ship, 0) for pl in planets)
                        if have < target:
                            plan["fleet"].append({"ship": ship, "have": have, "target": target})
            utils.atomic_write_json("auto_plan.json", plan)
        except Exception as e:
            self.log.debug("No se pudo publicar auto_plan.json: %s", e)

    def _write_build_status(self, planets):
        """Escribe build_status.json con los tiempos restantes de construcción/investigación."""
        try:
            import json
            now = time.time()
            out = {"updated_at": now, "planets": [], "research": {}}
            all_locs = []
            for p in planets:
                all_locs.append(p)
                if p.has_moon and p.moon:
                    all_locs.append(p.moon)
            for loc in all_locs:
                entry = self.state_cache["planets"].get(self._loc_key(loc.coords), {})
                finish = entry.get("build_finish_epoch", 0.0)
                if loc.building_in_progress or (finish and finish > now):
                    out["planets"].append({
                        "coords": str(loc.coords),
                        "name": loc.name,
                        "queue": entry.get("build_queue", loc.building_queue),
                        "finish_epoch": finish or (now + loc.building_remaining_seconds),
                    })
            r = self.state_cache.get("research", {})
            if r.get("finish_epoch", 0.0) and r["finish_epoch"] > now:
                out["research"] = {"tech": r.get("tech", ""), "finish_epoch": r["finish_epoch"]}
            utils.atomic_write_json("build_status.json", out)
        except Exception as e:
            self.log.debug("No se pudo guardar build_status.json: %s", e)

    def _get_planet_setting(self, planet, setting_name: str, default_val):
        coords_str = f"{planet.coords.galaxy}:{planet.coords.system}:{planet.coords.position}"
        planets_config = getattr(self.cfg, "planets_config", {}) or {}
        p_cfg = planets_config.get(coords_str, {})
        return p_cfg.get(setting_name, getattr(self.cfg, setting_name, default_val))

    def _origin_ok(self, loc, feature: str) -> bool:
        """¿Esta ubicación es origen válido para 'feature' según el selector
        <feature>_from por coordenada (both/planet/moon)? Default 'both'. Planeta y luna
        comparten coords, así que este ajuste decide desde cuál de los dos se lanza."""
        frm = self._get_planet_setting(loc, f"{feature}_from", "both")
        if frm not in ("planet", "moon"):
            return True   # both / valor desconocido -> ambos
        is_moon = getattr(loc.coords, "type", "planet") == "moon"
        return (frm == "moon") == is_moon

    # ------------------------------------------------------------------ #
    def run_forever(self):
        self.log.info("=== OGBot iniciado (dry_run=%s) ===", self.cfg.dry_run)
        # Arranque nuevo: persistir started_at ya (lo lee la GUI en /api/botstatus).
        self.started_at = time.time()
        self._save_state()
        self.client.start()
        if not self.client.login():
            self.log.error("Login fallido. Abortando.")
            self.client.stop()
            return
        self.initialize_session_stats()
        try:
            while self.running:
                # Regresos de flota pedidos desde la GUI (se atienden cuanto antes).
                try:
                    self._process_recall_requests()
                except Exception as e:
                    self.log.debug("Error procesando regresos de flota: %s", e)

                # Comandos remotos de la GUI (pause/resume/screenshot/...); se atienden
                # también estando pausado (el resume debe funcionar en pausa).
                try:
                    self._process_control_requests()
                except Exception as e:
                    self.log.debug("Error procesando comandos de la GUI: %s", e)

                # Comprobación de ataque prioritaria e incondicional (antes de evaluar franja horaria)
                if getattr(self.cfg, "enable_attack_escape", True):
                    try:
                        self.log.info("Comprobación prioritaria de ataques hostiles...")
                        self._check_and_escape_attacks()
                    except Exception as e:
                        self.log.debug("Error en comprobación prioritaria de ataques: %s", e)

                if utils.within_active_hours(self.cfg.active_hours):
                    # Nueva franja activa: se rearman los avisos de phalanx de la próxima noche.
                    self._phalanx_warned.clear()
                    if getattr(self.cfg, "monitor_only", False):
                        # En monitoreo cycle() no corre, así que recargamos aquí para captar
                        # que desactiven el modo desde la GUI sin reiniciar el bot.
                        self._reload_config()
                    if getattr(self.cfg, "monitor_only", False):
                        self.log.info("Modo solo-monitoreo: vigilando ataques/espionaje y fleetsave (sin economía/farming/expediciones).")
                    elif time.time() < self.paused_until:
                        self.log.info("Pausado vía Telegram hasta %s: sin ciclo, solo vigilancia de ataques/regresos.",
                                      time.strftime("%H:%M", time.localtime(self.paused_until)))
                    else:
                        try:
                            self.cycle()
                            self._continue_until_idle()
                        except Exception as e:
                            self.log.exception("Error en ciclo: %s", e)

                    sleep_s = utils.jitter(random.uniform(self.cfg.cycle_interval_min_s,
                                                           self.cfg.cycle_interval_max_s))
                    self._next_cycle_eta = time.time() + sleep_s
                    self.log.info("Próximo ciclo en %.0f min.", sleep_s / 60)
                    
                    # Esperar al siguiente ciclo en pequeños intervalos
                    start_sleep = time.time()
                    last_attack_check = time.time()  # Ya comprobamos justo antes de entrar al ciclo
                    next_attack_delay = self._attack_check_interval()
                    while self.running and (time.time() - start_sleep) < sleep_s:
                        if not utils.within_active_hours(self.cfg.active_hours):
                            break

                        # Reactivarse al volver/terminar una expedición para reenviar nuevas
                        if getattr(self.cfg, "expedition_smart_schedule", True) and self.next_expedition_event > 0:
                            if time.time() >= self.next_expedition_event:
                                self.log.info("Despertando por evento de expedición (vuelta/fin) para reenviar.")
                                self.next_expedition_event = 0.0
                                break

                        # Reactivarse al volver una flota de farmeo para relanzar la ronda
                        if getattr(self.cfg, "farming_smart_schedule", True) and self.next_farming_event > 0:
                            if time.time() >= self.next_farming_event:
                                self.log.info("Despertando por vuelta de flota de farmeo para relanzar.")
                                self.next_farming_event = 0.0
                                break

                        # Reactivarse en el momento justo para encolar la siguiente
                        # construcción (fin de la actual o cuando ya hay recursos).
                        if getattr(self.cfg, "enable_build_queue", True) and self.next_build_event > 0:
                            if time.time() >= self.next_build_event:
                                self.log.info("Despertando por cola de construcción.")
                                self.next_build_event = 0.0
                                break

                        # Comprobación periódica de ataques hostiles si está habilitado.
                        # El intervalo se re-sortea en cada comprobación (rango aleatorio).
                        if getattr(self.cfg, "enable_attack_escape", True):
                            now = time.time()
                            if now - last_attack_check >= next_attack_delay:
                                last_attack_check = now
                                next_attack_delay = self._attack_check_interval()
                                try:
                                    self._check_and_escape_attacks()
                                except Exception as e:
                                    self.log.debug("Error comprobando ataques en espera: %s", e)

                        # Atender regresos de flota pedidos desde la GUI casi al instante.
                        try:
                            self._process_recall_requests()
                        except Exception as e:
                            self.log.debug("Error procesando regresos de flota (espera): %s", e)

                        # Comandos remotos de la GUI (también con la pausa activa).
                        try:
                            self._process_control_requests()
                        except Exception as e:
                            self.log.debug("Error procesando comandos de la GUI (espera): %s", e)

                        # Comandos remotos de Telegram (/status, /fleetsave, /pausa...).
                        try:
                            self._poll_telegram_commands()
                        except Exception as e:
                            self.log.debug("Error atendiendo comandos de Telegram (espera): %s", e)

                        time.sleep(5)
                else:
                    hours_to_sleep = utils.hours_until_active(self.cfg.active_hours)
                    self.log.info("Fuera de franja horaria. Modo descanso activado por %.2f horas.", hours_to_sleep)
                    
                    # Realizar fleetsave una sola vez para cubrir todo el periodo de inactividad
                    self._fleetsave_all(offline_hours=hours_to_sleep)

                    # Publicar la agenda al entrar en la noche (pausa nocturna, retornos...).
                    try:
                        self._publish_agenda()
                    except Exception as e:
                        self.log.debug("No se pudo publicar la agenda nocturna: %s", e)

                    # Si el retorno a mitad de la noche está habilitado
                    recall_halfway = getattr(self.cfg, "fleetsave_recall_halfway", False)
                    if recall_halfway and hours_to_sleep > 0.5:
                        half_sleep_hours = hours_to_sleep / 2.0
                        half_sleep_seconds = half_sleep_hours * 3600
                        self.log.info("Retorno a mitad de la noche habilitado. Esperando la primera mitad (%.2f horas) antes de retornar...", half_sleep_hours)
                        
                        start_sleep = time.time()
                        while self.running and (time.time() - start_sleep) < half_sleep_seconds:
                            time.sleep(10)
                            
                        if self.running:
                            self.log.info("Despertando temporalmente para retornar despliegues de fleetsave...")
                            try:
                                if self.client.login():
                                    # Comprobar ataques una única vez al despertar a mitad de la noche
                                    if getattr(self.cfg, "enable_attack_escape", True):
                                        try:
                                            self._check_and_escape_attacks()
                                        except Exception as ae:
                                            self.log.debug("Error comprobando ataques a mitad de la noche: %s", ae)
                                    self._recall_sleep_fleetsaves()
                            except Exception as e:
                                self.log.error("Error al retornar despliegues nocturnos: %s", e)
                                
                        self.log.info("Retorno nocturno completado. Esperando la segunda mitad...")
                    
                    self.log.info("Esperando hasta el inicio de la franja horaria activa...")
                    # Barrido nocturno opcional: cada N horas despierta y vacía los planetas
                    # activados (para recoger la flota fabricada de noche). Si está apagado,
                    # espera en silencio hasta el horario activo (sin actividad).
                    night_sweep = getattr(self.cfg, "enable_night_sweep", False)
                    sweep_interval_s = max(0.25, float(getattr(self.cfg, "night_sweep_interval_hours", 2.0) or 2.0)) * 3600
                    last_sweep = time.time()
                    while self.running and not utils.within_active_hours(self.cfg.active_hours):
                        # Atender regresos de flota pedidos desde la GUI también de noche.
                        try:
                            self._process_recall_requests()
                        except Exception as e:
                            self.log.debug("Error procesando regresos de flota (noche): %s", e)
                        # Comandos remotos de la GUI también durante el descanso.
                        try:
                            self._process_control_requests()
                        except Exception as e:
                            self.log.debug("Error procesando comandos de la GUI (noche): %s", e)
                        # Comandos remotos de Telegram también durante el descanso.
                        try:
                            self._poll_telegram_commands()
                        except Exception as e:
                            self.log.debug("Error atendiendo comandos de Telegram (noche): %s", e)
                        if night_sweep and (time.time() - last_sweep) >= sweep_interval_s:
                            last_sweep = time.time()
                            self.log.info("Barrido nocturno: despertando para vaciar planetas...")
                            try:
                                if self.client.login():
                                    # Aprovechar este despertar (ya con login) para vigilar ataques.
                                    if getattr(self.cfg, "enable_attack_escape", True):
                                        try:
                                            self._check_and_escape_attacks()
                                        except Exception as ae:
                                            self.log.debug("Error comprobando ataques antes del barrido nocturno: %s", ae)
                                    self._night_sweep()
                            except Exception as e:
                                self.log.error("Error en barrido nocturno: %s", e)
                        time.sleep(10)
        except KeyboardInterrupt:
            self.log.info("Detenido por el usuario.")
        finally:
            self.client.stop()

    def _sleep_hours(self) -> float:
        return random.uniform(self.cfg.cycle_interval_min_s, self.cfg.cycle_interval_max_s) / 3600

    def _guard(self) -> bool:
        """Rate-limit + delay humanizado antes de cada acción."""
        if not self.rate.allow():
            self.log.info("Rate-limit alcanzado; salto acción.")
            return False
        self.rate.record()
        self.client._delay()
        return True

    # ------------------------------------------------------------------ #
    def _count_our_active_fleets(self, movements: List[dict], planets: List) -> int:
        # Coordenadas propias (planetas y lunas) para reconocer flotas nuestras.
        our_coords = set()
        for p in planets:
            c = p.coords
            our_coords.add(f"{c.galaxy}:{c.system}:{c.position}")
            moon = getattr(p, "moon", None)
            if moon is not None and getattr(moon, "coords", None) is not None:
                mc = moon.coords
                our_coords.add(f"{mc.galaxy}:{mc.system}:{mc.position}")

        seen = set()
        count = 0
        for m in movements:
            origin = m.get("origin", "").replace("[", "").replace("]", "").strip()
            dest = m.get("destination", "").replace("[", "").replace("]", "").strip()
            is_return = bool(m.get("is_return", False))

            # En OGame una misión (ida+vuelta) es UNA sola flota = UN slot, y aparece
            # como una única fila en cada instante. Si el DOM devuelve filas duplicadas
            # (selectores solapados), las colapsamos para no contar el mismo slot dos veces.
            ident = (origin, dest, m.get("mission", ""), m.get("arrival_text", ""), is_return)
            if ident in seen:
                continue

            # Solo flotas NUESTRAS: salientes desde un planeta/luna propio, o de regreso
            # a uno propio. Así se excluyen ataques enemigos entrantes (origen ajeno).
            mine = (origin in our_coords) if not is_return else (dest in our_coords)
            if mine:
                seen.add(ident)
                count += 1
        return count

    def _count_our_active_expeditions(self, movements: List[dict]) -> int:
        seen = set()
        count = 0
        for m in movements:
            origin = m.get("origin", "").replace("[", "").replace("]", "").strip()
            dest = m.get("destination", "").replace("[", "").replace("]", "").strip()
            is_return = bool(m.get("is_return", False))
            mission = m.get("mission", "")

            ident = (origin, dest, mission, m.get("arrival_text", ""), is_return)
            if ident in seen:
                continue

            if mission == "15" or "exped" in mission.lower():
                seen.add(ident)
                count += 1
        return count

    def _aggregate_ships_in_motion(self, movements: List[dict], planets: List) -> Dict[str, int]:
        """Suma las naves de nuestras flotas en vuelo (salientes y de regreso).

        Las naves en tránsito no están en ningún planeta, así que sin esto el inventario
        imperial las da por desaparecidas. Excluye ataques enemigos entrantes y deduplica
        filas repetidas del DOM (mismo origen/destino/misión/llegada = una sola flota).
        """
        from .client import _ship_name_to_key

        our_coords = set()
        for p in planets:
            c = p.coords
            our_coords.add(f"{c.galaxy}:{c.system}:{c.position}")
            moon = getattr(p, "moon", None)
            if moon is not None and getattr(moon, "coords", None) is not None:
                mc = moon.coords
                our_coords.add(f"{mc.galaxy}:{mc.system}:{mc.position}")

        seen = set()
        totals: Dict[str, int] = {}
        for m in movements:
            if m.get("is_hostile"):
                continue
            origin = m.get("origin", "").replace("[", "").replace("]", "").strip()
            dest = m.get("destination", "").replace("[", "").replace("]", "").strip()
            is_return = bool(m.get("is_return", False))

            ident = (origin, dest, m.get("mission", ""), m.get("arrival_text", ""), is_return)
            if ident in seen:
                continue

            mine = (origin in our_coords) if not is_return else (dest in our_coords)
            if not mine:
                continue
            seen.add(ident)

            for raw_name, cnt in (m.get("ships") or {}).items():
                key = _ship_name_to_key(raw_name)
                if not key:
                    continue
                try:
                    totals[key] = totals.get(key, 0) + int(cnt)
                except (TypeError, ValueError):
                    continue

        if totals:
            self.log.info("Naves en vuelo: %s",
                          ", ".join(f"{k}:{v}" for k, v in totals.items()))
        elif movements:
            self.log.debug("Naves en vuelo: 0 sumadas de %d movimientos "
                           "(¿sin desglose de naves en el tooltip?)", len(movements))
        return totals

    def _total_expe_slots_effective(self) -> int:
        """Slots de expedición totales: lo leído del juego, o el cálculo por astrofísica
        si aún no se ha leído (None). Sin astrofísica NO hay expediciones (0 slots)."""
        if self.total_expe_slots is not None:
            return self.total_expe_slots
        astro = self.research_levels.get("astrophysics", 0) if self.research_levels else 0
        return max(0, gd.expedition_slots(astro))

    def _has_free_expe_slots(self) -> bool:
        total = self._total_expe_slots_effective()
        if total <= 0:
            return False
        return self.active_expe_slots < total

    def _has_free_slots_for_mission(self) -> bool:
        total = self.total_fleet_slots if self.total_fleet_slots else (
            self.research_levels.get("computer_tech", 0) + 1 if self.research_levels else 1)
        free = total - self.active_slots
        reserve = max(0, int(getattr(self.cfg, "keep_free_fleet_slots", 1)))
        return free >= 1 + reserve

    def _has_free_slots_for_espionage(self) -> bool:
        return self._has_free_slots_for_mission()

    def _has_ships(self, planets, ship_type: str, min_count: int = 1) -> bool:
        return sum(p.ships.get(ship_type, 0) for p in planets) >= min_count

    def _deduct_ships(self, loc, ships: dict):
        """Descuenta del inventario local de 'loc' las naves de un send_fleet exitoso
        (floor 0), para que el resto del ciclo no cuente naves ya en vuelo."""
        for s, q in (ships or {}).items():
            loc.ships[s] = max(0, loc.ships.get(s, 0) - q)

    def _reload_config(self):
        """Recarga config.yaml para capturar cambios hechos desde la GUI sin reiniciar
        (incluye activar/desactivar el modo solo-monitoreo)."""
        try:
            path = getattr(self.cfg, "_path", "config.yaml")
            new_cfg = Config.load(path)
            for k, v in new_cfg.__dict__.items():
                setattr(self.cfg, k, v)
            self._apply_probe_cargo()   # aplicar cambios de bodega de sonda hechos desde la GUI
            self._apply_empire_auto()
            self.log.info("Configuración recargada desde el disco.")
        except Exception as e:
            self.log.warning("No se pudo recargar la configuración desde el disco: %s", e)

    def cycle(self):
        self.log.info("--- Nuevo ciclo ---")
        self._reload_config()

        # Correcciones de nivel / re-lectura forzada pedidas desde la GUI.
        self._apply_pending_gui_requests()

        planets = self.client.read_planets()
        if not planets:
            self.log.warning("Sin planetas legibles; revisa selectores.")
            return
        # En cuanto se leen, para que todo lo que corre después en este ciclo (p.ej. la
        # detección de espionaje en update_imperial_stats) vea las coords propias actuales.
        self.last_planets = planets

        slot_info = self.client.read_fleet_slots()
        # Leemos movimientos siempre: sirven de fallback para el conteo de slots y, sobre
        # todo, para sumar las naves en vuelo al inventario imperial de la GUI.
        # detailed=True -> página de movimientos, que trae el desglose por nave de cada flota
        # propia. Sin esto las expediciones (miles de cargueros) no se sumaban al "en vuelo".
        # ponytail: una sola navegación por ciclo; misma que antes, solo que a 'movement'.
        mvs = self.client.read_movements(detailed=True)
        if slot_info:
            self.active_slots = slot_info.get("fleet_used", 0)
            self.total_fleet_slots = slot_info.get("fleet_total", 0)
            # Si el juego no reportó expediciones, dejar None (desconocido) para que el
            # cálculo por astrofísica siga aplicando en vez de bloquear con 0.
            self.total_expe_slots = slot_info.get("expe_total")
            self.active_expe_slots = slot_info.get("expe_used", 0)
            self.log.info("Slots reales del juego: Flotas %d/%d, Expediciones %d/%d",
                          self.active_slots, self.total_fleet_slots,
                          self.active_expe_slots, self._total_expe_slots_effective())
        else:
            self.active_slots = self._count_our_active_fleets(mvs, planets)
            self.active_expe_slots = self._count_our_active_expeditions(mvs)
            self.total_expe_slots = gd.expedition_slots(self.research_levels.get("astrophysics", 0))
            self.log.info("Slots de flota (fallback movimientos): %d activos, Expediciones %d/%d",
                          self.active_slots, self.active_expe_slots, self.total_expe_slots)

        # Registro de misiones propias en vuelo por destino (dedup de recolecciones, etc.)
        self.inflight_dests = {}
        for m in mvs:
            if m.get("is_return") or m.get("is_hostile"):
                continue
            d = m.get("destination", "")
            if d:
                self.inflight_dests.setdefault(d, set()).add(str(m.get("mission", "")))

        ships_in_motion = self._aggregate_ships_in_motion(mvs, planets)

        # Leer estado de cada planeta y luna usando la memoria/caché (escaneo completo
        # solo al inicio o en el resync; el resto de ciclos, lectura ligera).
        for p in planets:
            self._read_location_state(p)
            if p.has_moon and p.moon:
                self._read_location_state(p.moon)

        # Construir lista de todas las ubicaciones (planetas y lunas)
        all_locations = []
        for p in planets:
            all_locations.append(p)
            if p.has_moon and p.moon:
                all_locations.append(p.moon)

        # Guardar planetas en caché para la GUI (incluyendo naves e información de luna)
        try:
            import json
            planets_data = []
            for p in planets:
                p_data = {
                    "name": p.name, 
                    "coords": f"{p.coords.galaxy}:{p.coords.system}:{p.coords.position}", 
                    "id": p.id,
                    "ships": p.ships,
                    "defenses": p.defenses,
                    "buildings": p.buildings,
                    "has_moon": p.has_moon,
                }
                if p.has_moon and p.moon:
                    p_data["moon"] = {
                        "name": p.moon.name,
                        "coords": f"{p.moon.coords.galaxy}:{p.moon.coords.system}:{p.moon.coords.position}",
                        "id": p.moon.id,
                        "ships": p.moon.ships,
                        "defenses": p.moon.defenses,
                        "buildings": p.moon.buildings
                    }
                planets_data.append(p_data)
            utils.atomic_write_json("planets_cache.json", planets_data)
            utils.atomic_write_json("fleet_in_motion.json", ships_in_motion)
            # Lista de vuelos para la pestaña "Vuelos" (datos completos: naves y carga). Si la
            # lectura vino vacía (fallo transitorio), NO vaciamos el panel: conservamos los
            # vuelos previos aún en curso (_retain_unlanded).
            prev_flights = []
            try:
                with open("fleet_flights.json", "r", encoding="utf-8") as pf:
                    prev_flights = json.load(pf).get("flights", [])
            except Exception:
                prev_flights = []
            now_ts = time.time()
            new_flights = build_flights(mvs, now_ts, prev=prev_flights)
            utils.atomic_write_json("fleet_flights.json",
                                    {"flights": _retain_unlanded(new_flights, prev_flights, now_ts),
                                     "updated": now_ts})
        except Exception as e:
            self.log.debug("No se pudo guardar planets_cache.json: %s", e)

        # Niveles de investigación: desde la caché (solo re-lee en resync o al terminar una)
        self.research_levels = self._read_research_smart()
        self._force_resync = False        # ya se aplicó esta ronda (edificios + investigación)
        self._resync_targets = set()      # las ubicaciones pedidas ya se releyeron
        if self.research_levels:
            self.my_tech.weapons = self.research_levels.get("weapons_tech", 0)
            self.my_tech.shielding = self.research_levels.get("shielding_tech", 0)
            self.my_tech.armor = self.research_levels.get("armor_tech", 0)

        # Cola de construcción por planeta: corre CADA ciclo (no depende del intervalo de
        # economía) para colocar la siguiente construcción en cuanto termine la anterior o
        # haya recursos, y arma el próximo 'despertar' por su fin/ETA.
        self._process_build_queues(planets)

        now = time.time()
        
        # Evaluar si toca ejecutar ronda de economía (construcción)
        run_economy = False
        economy_interval = getattr(self.cfg, "economy_run_interval_mins", 0)
        if economy_interval <= 0:
            run_economy = True
        else:
            elapsed_economy = now - self.last_economy_run_time
            if elapsed_economy >= economy_interval * 60:
                run_economy = True
            else:
                self.log.info("Ronda de economía omitida (última ejecución hace %.1f min; intervalo configurado: %d min)", elapsed_economy / 60, economy_interval)

        # Evaluar si toca ejecutar reciclaje
        run_recycling = False
        recycling_interval = getattr(self.cfg, "recycling_run_interval_mins", 0)
        if recycling_interval <= 0:
            run_recycling = True
        else:
            elapsed_recycling = now - self.last_recycling_run_time
            if elapsed_recycling >= recycling_interval * 60:
                run_recycling = True
            else:
                self.log.info("Reciclaje omitido (última ejecución hace %.1f min; intervalo configurado: %d min)", elapsed_recycling / 60, recycling_interval)

        # Evaluar si toca ejecutar expediciones
        run_expeditions = False
        expeditions_interval = getattr(self.cfg, "expeditions_run_interval_mins", 0)
        if expeditions_interval <= 0:
            run_expeditions = True
        else:
            elapsed_expeditions = now - self.last_expeditions_run_time
            if elapsed_expeditions >= expeditions_interval * 60:
                run_expeditions = True
            else:
                self.log.info("Expediciones omitidas (última ejecución hace %.1f min; intervalo configurado: %d min)", elapsed_expeditions / 60, expeditions_interval)

        # Evaluar si toca ejecutar ronda de farmeo (ataques a inactivos)
        run_farming = False
        farming_interval = getattr(self.cfg, "farming_run_interval_mins", 0)
        if farming_interval <= 0:
            run_farming = True
        else:
            elapsed_farming = now - self.last_farming_run_time
            if elapsed_farming >= farming_interval * 60:
                run_farming = True
            else:
                self.log.info("Ronda de farmeo omitida (última ejecución hace %.1f min; intervalo configurado: %d min)", elapsed_farming / 60, farming_interval)

        # Rondas del ciclo en el orden configurable (cfg.cycle_order, contrato C4).
        # La lógica interna y los timers de cada ronda no cambian; solo el orden.
        def round_recycling():
            if run_recycling and self.cfg.enable_recycling:
                if self._has_ships(all_locations, "recycler"):
                    self._recycle(all_locations)
                else:
                    self.log.debug("Reciclaje omitido: sin recicladores.")
                self.last_recycling_run_time = time.time()
                self._save_state()

        def round_expeditions():
            if run_expeditions and self.cfg.enable_expeditions:
                self._run_expeditions_round(planets, all_locations)
                self.last_expeditions_run_time = time.time()
                self._update_next_expedition_event()
                self._save_state()

        def round_farming():
            # Farmeo (ataques a inactivos) + colonización + lunas (timer independiente)
            if run_farming:
                self.log.info("Iniciando ronda de farmeo (ataques a inactivos)...")
                if self.cfg.enable_farming and self.api:
                    if self._has_ships(all_locations, "espionage_probe"):
                        self._farm(all_locations)
                    else:
                        self.log.info("Farmeo omitido: sin sondas de espionaje.")
                if self.cfg.enable_colonization:
                    self._colonize(planets)
                if self.cfg.enable_moon_creation:
                    self._moonshot(planets)
                self.last_farming_run_time = time.time()
                self._update_next_farming_event()
                self._save_state()

        def round_economy():
            if run_economy:
                self.log.info("Iniciando ronda de economía/construcción...")
                # Economía / defensa / formas de vida / instalaciones por planeta.
                # Los planetas con un programa especial activo (inicio de servidor /
                # planeta nuevo) siguen su orden fijo en lugar de la economía normal.
                for p in planets:
                    prog = self._special_program_for(p, planets)
                    if prog is not None and not self._special_program_step(p, prog):
                        continue
                    self._economy_step(p)
                    self._defense_step(p)
                    self._lifeforms_step(p)
                    self._facilities_step(p)
                # Inicio de servidor: la investigación la marca el propio programa.
                server_start_active = (
                    bool(getattr(self.cfg, "special_server_start", False)) and planets
                    and startorder.next_step(planets[0], self.research_levels,
                                             startorder.SERVER_START_ORDER) is not None)
                if not server_start_active:
                    self._research_step(planets)
                self._fleet_step(planets, ships_in_motion)
                self.last_economy_run_time = time.time()
                self._save_state()

        def round_feed():
            # Alimentación de planetas-objetivo; comparte el timer de la economía.
            if run_economy:
                self._feed_step(planets, mvs)

        rounds = {"economy": round_economy, "recycling": round_recycling,
                  "expeditions": round_expeditions, "farming": round_farming,
                  "feed": round_feed}
        order = []
        for k in (getattr(self.cfg, "cycle_order", None) or []):
            if k in rounds and k not in order:
                order.append(k)
            elif k not in rounds:
                self.log.debug("cycle_order: clave desconocida %r ignorada.", k)
        for k in ("economy", "recycling", "expeditions", "farming", "feed"):
            if k not in order:
                order.append(k)
        for k in order:
            rounds[k]()

        # Mantener armado el despertar por vuelta de expedición aunque la ronda no se haya
        # ejecutado este ciclo (p.ej. bloqueada por su intervalo), para no perder reenvíos.
        if self.cfg.enable_expeditions:
            self._update_next_expedition_event()
        if self.cfg.enable_farming:
            self._update_next_farming_event()

        # Publicar tiempos restantes de construcciones/investigación para la GUI
        self._write_build_status(planets)
        # Publicar el plan del modo automático (pestaña "Automático" de la GUI)
        self._write_auto_plan(planets)

        # 11. Actualizar estadísticas imperiales
        self.update_imperial_stats()

        # 12. Histórico diario (una línea por día para las gráficas de la GUI)
        try:
            self._append_daily_history()
        except Exception as e:
            self.log.debug("No se pudo actualizar el histórico diario: %s", e)

        # 13. Snapshot horario de recursos/acciones (una línea por hora, rolling 72h)
        try:
            self._write_hourly_snapshot()
        except Exception as e:
            self.log.debug("No se pudo escribir stats_hourly.jsonl: %s", e)

        # 14. Agenda de tareas para la GUI
        try:
            self._publish_agenda()
        except Exception as e:
            self.log.debug("No se pudo publicar task_agenda.json: %s", e)

    # ------------------------------------------------------------------ #
    # --- Configuraciones especiales: programas de desarrollo fijos --------- #
    def _special_program_for(self, planet, planets):
        """Orden de desarrollo especial aplicable a este planeta, o None."""
        if getattr(self.cfg, "special_server_start", False) and planet is planets[0]:
            return startorder.SERVER_START_ORDER
        cstr = f"{planet.coords.galaxy}:{planet.coords.system}:{planet.coords.position}"
        sel = (getattr(self.cfg, "special_new_planet", "") or "").strip()
        if sel and cstr == sel:
            return startorder.NEW_PLANET_ORDER
        # Colonias nuevas (p.ej. de autocolonizar): mientras no completen el programa.
        if getattr(self.cfg, "special_new_planet_auto", False) and \
                startorder.next_step(planet, self.research_levels, startorder.NEW_PLANET_ORDER):
            return startorder.NEW_PLANET_ORDER
        return None

    def _special_program_step(self, planet, order) -> bool:
        """Ejecuta (si puede) el siguiente paso del programa fijo. True = programa
        completado (el planeta vuelve a la economía normal este mismo ciclo)."""
        step = startorder.next_step(planet, self.research_levels, order)
        if step is None:
            self.log.debug("%s: programa especial completado; economía normal.", planet.coords)
            return True
        kind, name, target = step

        if kind == "research":
            rc = self.state_cache.get("research", {}) or {}
            if (rc.get("finish_epoch") or 0) > time.time():
                return False  # investigación en curso: esperar
            nxt = self.research_levels.get(name, 0) + 1
            cost = gd.research_cost(name, nxt)
            # Sin buffer: el programa gasta todo lo disponible ("sin parar").
            if not planet.resources.can_afford(cost):
                self.log.info("%s: programa especial ahorrando para %s %d.", planet.coords, name, nxt)
                return False
            if self._guard():
                ok = self.client.research(name, planet=planet)
                if ok:
                    self.record_session_action("research", name, nxt)
                    try:
                        secs = gd.research_time(cost, planet.lvl("research_lab"), self.cfg.universe_speed)
                        rc = self.state_cache.setdefault("research", {})
                        rc.setdefault("levels", {})[name] = nxt
                        self.research_levels[name] = nxt
                        rc["finish_epoch"] = time.time() + secs
                        rc["tech"] = name
                        self._save_state_cache()
                    except Exception as e:
                        self.log.debug("Programa especial: sin ETA de investigación: %s", e)
            return False

        # Edificio
        if self._active_queue_entry(planet) or planet.building_in_progress:
            return False
        nxt = planet.lvl(name) + 1
        cost = gd.building_cost(name, nxt)
        blocker = startorder.storage_blocker(cost, planet)
        if blocker:
            name, nxt = blocker, planet.lvl(blocker) + 1
            cost = gd.building_cost(name, nxt)
        if not planet.resources.can_afford(cost):
            self.log.info("%s: programa especial ahorrando para %s %d.", planet.coords, name, nxt)
            return False
        comp = "facilities" if name in ("robotics_factory", "nanite_factory",
                                        "shipyard", "research_lab") else "supplies"
        if self._guard():
            ok = self.client.build(planet, comp, name)
            if ok:
                self.record_session_action("buildings", name, nxt, str(planet.coords))
                self._mark_build_started(planet, name, cost)
        return False

    def _economy_step(self, planet):
        if not self._get_planet_setting(planet, "enable_economy", True):
            self.log.debug("%s: economía desactivada para este planeta.", planet.coords)
            return
        if self._active_queue_entry(planet):
            self.log.debug("%s: cola de construcción activa, la economía cede el paso.", planet.coords)
            return
        if planet.building_in_progress:
            self.log.debug("%s: construcción en progreso, saltando economía.", planet.coords)
            return
        plasma = self.research_levels.get("plasma_tech", 0)
        choice = economy.affordable_build(planet, self.cfg, plasma=plasma)
        if choice:
            name, cost = choice
            comp = "facilities" if name in ("robotics_factory", "nanite_factory",
                                            "shipyard", "research_lab") else "supplies"
            if self._guard():
                ok = self.client.build(planet, comp, name)
                if ok:
                    self.record_session_action("buildings", name, planet.lvl(name) + 1, str(planet.coords))
                    self._mark_build_started(planet, name, cost)
        else:
            self.log.info("%s: nada rentable que construir ahora.", planet.coords)

    def _defense_step(self, planet):
        if not self._get_planet_setting(planet, "enable_defense", True):
            self.log.debug("%s: defensa desactivada para este planeta.", planet.coords)
            return
        choice = economy.affordable_defense(planet, self.cfg)
        if choice:
            name, count, cost = choice
            if self._guard():
                ok = self.client.build_defense(planet, name, count)
                if ok:
                    self.record_session_action("defense", name, count, str(planet.coords))
                    self._cache_bump_defense(planet, name, count)
        else:
            self.log.debug("%s: sin defensa asequible.", planet.coords)

    def _lifeforms_step(self, planet):
        if not self._get_planet_setting(planet, "enable_lifeforms", True):
            return
        if not planet.lifeform_available:
            return
        if planet.lifeform_in_progress:
            self.log.debug("%s: cola de Formas de vida ocupada.", planet.coords)
            return
        if self._guard():
            self.client.build_lifeform(planet)

    def _research_step(self, planets: List):
        if not getattr(self.cfg, "enable_research", True):
            self.log.debug("Investigación desactivada globalmente.")
            return
        # Investigar desde el planeta con el laboratorio de mayor nivel
        best = max(planets, key=lambda p: p.lvl("research_lab"))
        choice = research_mod.next_research(self.research_levels, best, self.cfg)
        if choice and self._guard():
            ok = self.client.research(choice[0], planet=best)
            if ok:
                self.record_session_action("research", choice[0], self.research_levels.get(choice[0], 0) + 1)
                # Estimar cuánto tardará (sin leer página) y subir el nivel en la caché
                try:
                    secs = gd.research_time(choice[1], best.lvl("research_lab"), self.cfg.universe_speed)
                    r = self.state_cache.setdefault("research", {})
                    lv = r.setdefault("levels", {})
                    new_level = self.research_levels.get(choice[0], 0) + 1
                    lv[choice[0]] = new_level
                    self.research_levels[choice[0]] = new_level
                    r["finish_epoch"] = time.time() + secs
                    r["tech"] = choice[0]
                    self._save_state_cache()
                    self.log.info("Investigación %s en curso, ~%.0f min restantes.", choice[0], secs / 60.0)
                except Exception as e:
                    self.log.debug("No se pudo estimar ETA de investigación: %s", e)

    def _fleet_step(self, planets, ships_in_motion=None):
        """Fabrica cargueros/naves según los objetivos definidos en la configuración."""
        if not getattr(self.cfg, "enable_fleet_building", True):
            self.log.debug("Construcción de flota desactivada globalmente.")
            return

        # Filtrar los planetas donde la creación de flota está activada y tienen astillero
        eligible_planets = [p for p in planets if self._get_planet_setting(p, "enable_fleet_building", True) and p.lvl("shipyard") >= 1]
        if not eligible_planets:
            self.log.debug("Fleet step omitido: no hay planetas con astillero y creación de flota activada.")
            return

        # Usar el planeta con el astillero de nivel más alto
        home = max(eligible_planets, key=lambda p: p.lvl("shipyard"))
        if self._shipyard_pending(home):
            self.log.debug("%s: astillero ocupado con el lote anterior; esperamos.", home.coords)
            return

        # Objetivos: auto-gestión (escalan con la economía) o los fijados a mano
        if getattr(self.cfg, "fleet_auto_build", False):
            expe_total = 0
            if (getattr(self.cfg, "fleet_priority", "economy") or "").lower() == "expeditions":
                expe_total = self._expedition_optimal_cargo_total()
            fleet_targets = fleet_mod.auto_fleet_targets(
                home, planets, self.research_levels, self.cfg, expe_total)
            self.log.debug("Auto-flota (%s): objetivos calculados %s",
                           getattr(self.cfg, "fleet_priority", "economy"), fleet_targets)
        else:
            fleet_targets = getattr(self.cfg, "fleet_targets", {}) or {}
        in_motion = ships_in_motion or {}
        auto_build = getattr(self.cfg, "fleet_auto_build", False)
        if fleet_targets:
            for ship_name, target_qty in fleet_targets.items():
                if target_qty <= 0:
                    continue
                # Contamos las naves totales en todo el imperio (incluidas las que vuelan)
                current_qty = sum(p.ships.get(ship_name, 0) for p in planets) + in_motion.get(ship_name, 0)
                if current_qty < target_qty:
                    needed = target_qty - current_qty
                    batch = min(needed, 10)
                    # Comprobar recursos disponibles y ajustar el lote
                    unit = gd.SHIPS.get(ship_name)
                    if unit:
                        buf = 1 - getattr(self.cfg, "keep_resources_buffer", 0.0)
                        avail_m = home.resources.metal * buf
                        avail_c = home.resources.crystal * buf
                        avail_d = home.resources.deut * buf
                        max_m = int(avail_m // unit.cost.metal) if unit.cost.metal > 0 else batch
                        max_c = int(avail_c // unit.cost.crystal) if unit.cost.crystal > 0 else batch
                        max_d = int(avail_d // unit.cost.deut) if unit.cost.deut > 0 else batch
                        affordable = min(max_m, max_c, max_d)
                        if affordable <= 0:
                            self.log.debug("Sin recursos para %s (necesita %s); esperando.",
                                           ship_name, unit.cost)
                            continue
                        batch = min(batch, affordable)
                    if self._guard():
                        self.log.info("Fabricando %d %s (objetivo: %d, actual imperio: %d)",
                                       batch, ship_name, target_qty, current_qty)
                        ok = self.client.build_ships(home, ship_name, batch)
                        if ok:
                            self.record_session_action("fleet", ship_name, batch, str(home.coords))
                            self._mark_ships_started(home, ship_name, batch)
                            # Solo actualizar inventario local si la orden fue aceptada
                            home.ships[ship_name] = home.ships.get(ship_name, 0) + batch
                        break # Un solo lote de construcción por ciclo para balancear recursos
        elif not auto_build:
            # Fallback al comportamiento original solo cuando NO está en modo auto-flota.
            # Si auto_build=True pero no hay naves buildables (astillero bajo), no hacer nada.
            if not self.cfg.enable_farming:
                return
            if home.lvl("metal_mine") < 4:
                return
            have_lc = home.ships.get("large_cargo", 0) + in_motion.get("large_cargo", 0)
            if have_lc < 20 and self._guard():
                ok = self.client.build_ships(home, "large_cargo", 10)
                if ok:
                    self.record_session_action("fleet", "large_cargo", 10, str(home.coords))
                    self._mark_ships_started(home, "large_cargo", 10)

    def _apply_empire_auto(self):
        """Autogestión del imperio: un solo interruptor que enciende todos los
        subsistemas de decisión automática (qué subir, dónde, flota, expediciones,
        colonización y reparto de recursos). Solo fuerza flags en memoria; el
        config.yaml del usuario no se toca. No activa el farmeo (ataques): eso
        sigue siendo decisión explícita del usuario."""
        if not getattr(self.cfg, "empire_auto", False):
            return
        c = self.cfg
        c.enable_economy = True
        c.enable_facilities = True
        c.enable_research = True
        c.enable_fleet_building = True
        c.fleet_auto_build = True
        c.enable_expeditions = True
        c.expedition_auto_ships = True
        c.enable_colonization = True
        c.special_new_planet_auto = True   # colonias nuevas siguen el orden óptimo
        self.log.info("Autogestión del imperio ACTIVA: economía, instalaciones, "
                      "investigación, flota, expediciones, colonización y reparto "
                      "de recursos en automático (prioridad: %s).",
                      getattr(c, "fleet_priority", "economy"))

    def _expedition_optimal_cargo_total(self) -> int:
        """Cargueros necesarios para llenar TODOS los slots de expedición al óptimo
        (mismo dimensionado que usan las auto-expediciones)."""
        try:
            top1 = self._expedition_top1_points()
            hyper = int(getattr(self.cfg, "expedition_hyperspace_level", 0) or 0)
            if hyper <= 0:
                hyper = int(self.research_levels.get("hyperspace_tech", 0)) if self.research_levels else 0
            discoverer = bool(getattr(self.cfg, "expedition_discoverer_class", False))
            find = gd.expedition_max_find_units(top1, self.cfg.universe_speed, discoverer,
                                                bool(getattr(self.cfg, "expedition_use_pathfinder", False)))
            cargo_ship = getattr(self.cfg, "expedition_cargo_ship", "large_cargo") or "large_cargo"
            per_exp = fleet_mod.optimal_expedition_cargo(
                find, cargo_ship, float(getattr(self.cfg, "expedition_find_safety", 1.0)), hyper)
            max_cargo = int(getattr(self.cfg, "expedition_max_cargo", 0) or 0)
            if max_cargo > 0:
                per_exp = min(per_exp, max_cargo)
            slots = self._total_expe_slots_effective() or gd.expedition_slots(
                self.research_levels.get("astrophysics", 0))
            return per_exp * max(1, slots)
        except Exception as e:
            self.log.debug("Sin óptimo de expediciones para la auto-flota: %s", e)
            return 0

    def _apply_probe_cargo(self, warn: bool = False):
        """Servidores con bodega en sondas (raid con sondas): fija la capacidad real en el
        modelo de juego para que carga/botín/combate/combustible la usen. Solo si el raid
        con sondas está activo. Se llama al iniciar y al recargar config (cambios de la GUI)."""
        if not getattr(self.cfg, "farm_with_probes", False):
            return
        pc = int(getattr(self.cfg, "espionage_probe_cargo", 0) or 0)
        if pc > 0:
            gd.SHIPS["espionage_probe"].cargo = pc
        elif warn:
            self.log.warning(
                "farm_with_probes activo pero espionage_probe_cargo=0; uso la bodega de "
                "gamedata (%d u). Pon la bodega real de tu servidor o el raid con sondas "
                "saqueará casi nada.", gd.SHIPS["espionage_probe"].cargo)

    def _farm(self, locations):
        # Filtrar ubicaciones origen elegibles para farmeo (planetas y lunas)
        eligible_locations = [p for p in locations
                              if self._get_planet_setting(p, "enable_farming", True)
                              and self._origin_ok(p, "farming")]
        if not eligible_locations:
            self.log.info("Farmeo omitido: no hay ningún planeta o luna con farmeo activado.")
            return

        # 1. Template de flota de ataque
        use_probes = bool(getattr(self.cfg, "farm_with_probes", False))
        if use_probes:
            # Raid con sondas: van SOLAS, sin escoltas ni otras naves (mezclarlas no
            # tiene sentido). Se ignora attacker_fleet_template.
            template = {"espionage_probe": 1}
        else:
            template = {k: v for k, v in
                        (self.cfg.attacker_fleet_template or {}).items() if v > 0}
            if not template:
                template = {"large_cargo": 5}

        # Auto-flota: el bot elige la escolta militar por simulación en vez de la plantilla.
        use_auto = bool(getattr(self.cfg, "farm_auto_fleet", False)) and not use_probes

        # Dimensionado de la flota de ataque según el modo (sondas / auto / plantilla).
        # Devuelve None si en modo auto ni todo el hangar gana el combate.
        def size_fleet(p, full_loot, report=None):
            if use_probes:
                return fleet_mod.size_attack_fleet_probes(
                    p, full_loot, template, gd.SHIPS["espionage_probe"].cargo)
            if use_auto and report is not None:
                def_tech = combat.Tech(
                    weapons=report.research.get("weapons_tech", 0),
                    shielding=report.research.get("shielding_tech", 0),
                    armor=report.research.get("armor_tech", 0),
                )
                escort = fleet_mod.auto_military_escort(
                    p.ships, report.fleet, report.defense, self.my_tech, def_tech)
                if escort is None:
                    return None
                return fleet_mod.size_attack_fleet_for_planet(p, full_loot, escort)
            return fleet_mod.size_attack_fleet_for_planet(p, full_loot, template)

        # Comprobar si tenemos al menos una naves de estos tipos en algún origen elegible
        if use_auto:
            # La plantilla se ignora: basta con tener cargueros en algún origen elegible.
            has_farming_fleet = any(self._has_ships(eligible_locations, s, min_count=1)
                                    for s in ("small_cargo", "large_cargo"))
        else:
            has_farming_fleet = any(self._has_ships(eligible_locations, ship_type, min_count=1) for ship_type in template.keys())
        if not has_farming_fleet:
            self.log.info("Farmeo omitido: sin flota de ataque/cargueros configurados en %s en ubicaciones con farmeo activo.", list(template.keys()))
            return

        self.log.info("Buscando objetivos (API)...")
        # avoid_strong_players: los 100 primeros del ranking se consideran "fuertes"
        # y se excluyen (rank 1 = el más fuerte del universo).
        max_rank_safe = 100 if getattr(self.cfg, "avoid_strong_players", True) else 0
        candidates = self.api.farm_targets(
            only_inactive=bool(getattr(self.cfg, "only_inactive_targets", True)),
            max_rank_safe=max_rank_safe)

        # Filtrar candidatos por cooldown de ataques y blacklist de granjas pobres
        cooldown_s = getattr(self.cfg, "farming_attack_cooldown_hours", 2.0) * 3600
        bl_days = float(getattr(self.cfg, "farming_blacklist_days", 7.0) or 0.0)
        now = time.time()
        filtered_candidates = []
        for cand in candidates:
            coords_str = cand.get("coords")
            if not coords_str:
                continue
            last_atk = self.attack_history.get(coords_str, 0.0)
            if now - last_atk < cooldown_s:
                remaining_min = (cooldown_s - (now - last_atk)) / 60
                self.log.debug("Candidato %s omitido por cooldown de ataque (restan %.1f min).", coords_str, remaining_min)
                continue
            # Blacklist por botín real: granjas que demostraron no pagar no gastan sondas.
            bl = tgt.blacklist_state(self.target_stats.get(coords_str),
                                     self.cfg.min_loot_value, now, bl_days)
            if bl == "skip":
                self.log.debug("Candidato %s omitido por blacklist (botín real medio %.0f < mín %.0f).",
                               coords_str, tgt.avg_real_loot(self.target_stats.get(coords_str)),
                               self.cfg.min_loot_value)
                continue
            if bl == "reset":  # cumplió la condena: otra oportunidad con historial limpio
                self.target_stats.pop(coords_str, None)
            filtered_candidates.append(cand)

        origins = [p.coords for p in eligible_locations]
        pre = tgt.select_targets(filtered_candidates, origins, self.cfg.max_target_distance_systems)
        # Granjas con botín real demostrado primero (sort estable: el resto de candidatos
        # conserva el orden por distancia de select_targets).
        pre.sort(key=lambda c: -tgt.avg_real_loot(self.target_stats.get(c.get("coords", ""))))
        limit = self.cfg.max_attack_targets_per_cycle
        # Espiar el doble del límite para tener margen tras la evaluación
        spy_batch = pre[: limit * 2]
        self.log.info("%d candidatos en radio, espionando %d.", len(pre), len(spy_batch))
        if not spy_batch:
            return

        probes = max(1, self.research_levels.get("espionage_tech", 4))

        # Helper para encontrar el origen más cercano con sondas de espionaje
        def get_best_spy_origin(target_coords: Coords) -> Optional[Planet]:
            eligible = [p for p in eligible_locations if p.ships.get("espionage_probe", 0) >= probes]
            if not eligible:
                return None
            return min(eligible, key=lambda p: gd.distance(p.coords.tuple(), target_coords.tuple()))

        # ── Fase 1: enviar sondas a todos los candidatos ───────────────
        spied: List[Coords] = []
        for cand in spy_batch:
            if not self._has_free_slots_for_espionage():
                self.log.info("Farmeo: deteniendo envío de sondas por falta de slots libres.")
                break
            coords = tgt.parse_coords(cand["coords"])
            origin_planet = get_best_spy_origin(coords)
            if not origin_planet:
                self.log.info("Farmeo: no hay ninguna ubicación con al menos %d sondas disponibles.", probes)
                break
            if not self._guard():
                break
            ok = self.client.send_fleet(
                origin_planet.coords, coords, {"espionage_probe": probes}, mission="espionage"
            )
            if ok:
                self._deduct_ships(origin_planet, {"espionage_probe": probes})
                self.active_slots += 1
                spied.append(coords)
                self.log.debug("Sondas enviadas desde %s -> %s", origin_planet.coords, coords)
                self.record_session_action("espionage", f"{coords}", "Espionaje enviado", str(origin_planet.coords))

        if not spied:
            self.log.info("Farmeo: ninguna sonda enviada.")
            return

        # ── Fase 2: esperar retorno de las sondas (máx 5 min) ─────────
        self.log.info("Farmeo: %d sondas en vuelo, esperando retorno...", len(spied))
        deadline = time.time() + 300
        while time.time() < deadline:
            mvs = self.client.read_movements()
            spy_flying = [m for m in mvs
                          if m.get("mission") in ("6", "espionage", "Espionage")]
            if not spy_flying:
                break
            self.log.debug("Sondas aún en vuelo: %d. Comprobando en 15s.", len(spy_flying))
            time.sleep(15)

        # Recuentar slots reales activos después de que vuelvan las sondas
        slot_info = self.client.read_fleet_slots()
        if slot_info:
            self.active_slots = slot_info.get("fleet_used", 0)
            self.total_fleet_slots = slot_info.get("fleet_total", 0)
        else:
            mvs = self.client.read_movements()
            self.active_slots = self._count_our_active_fleets(mvs, locations)

        # ── Fase 3: leer informes, evaluar y atacar ────────────────────
        reports = self.client.read_all_spy_reports()
        
        # Helper para verificar si un origen tiene la flota necesaria
        def can_afford_fleet(p, fleet_req):
            return all(p.ships.get(ship, 0) >= qty for ship, qty in fleet_req.items())

        best_attacks_by_target = {}
        for coords in spied:
            key = f"{coords.galaxy}:{coords.system}:{coords.position}"
            report = reports.get(key)
            if not report:
                self.log.debug("Sin informe para %s.", coords)
                self.record_session_action("espionage", f"{coords}", "Sin informe: no se pudo leer el reporte de espionaje")
                continue

            # Actividad reciente en un "inactivo" = trampa probable o recursos a punto
            # de moverse: no arriesgar la flota.
            act = getattr(report, "activity_mins", None)
            if getattr(self.cfg, "farming_skip_active_targets", True) and act is not None and act < 60:
                self.log.info("Farmeo: %s descartado por actividad reciente (%d min).", coords, act)
                self.record_session_action("espionage", f"{coords}", f"Descartado: actividad reciente ({act} min)")
                continue

            # Calcular botín completo (sin limitar por la capacidad inicial del template)
            full_loot = tgt.estimate_loot(report.resources, 10**9, self.cfg.loot_percent)

            # Buscar el mejor origen para este objetivo
            best_atk_for_target = None
            reasons_by_planet = {}
            for p in eligible_locations:
                # 1. Dimensionar la flota específicamente para lo disponible en este origen
                atk_fleet = size_fleet(p, full_loot, report)
                if atk_fleet is None:
                    reasons_by_planet[str(p.coords)] = "Auto-flota: el hangar no gana el combate"
                    continue

                # 2. Verificar que el origen tenga toda la flota requerida (cargueros + cazas del template)
                if not can_afford_fleet(p, atk_fleet):
                    reasons_by_planet[str(p.coords)] = "Falta de flota en hangar"
                    continue
                
                # 3. Evitar ataques vacíos si no tenemos cargueros disponibles en absoluto
                if tgt.cargo_capacity(atk_fleet) == 0:
                    reasons_by_planet[str(p.coords)] = "Falta de cargueros en hangar"
                    continue

                # 4. Evaluar rentabilidad del objetivo desde este origen específico
                res_eval = tgt.evaluate(report, p.coords, atk_fleet, self.my_tech, self.cfg, return_reason=True)
                target, reason = res_eval
                if not target:
                    reasons_by_planet[str(p.coords)] = reason
                    continue

                # 5. Reserva de deuterio: no dejar el origen sin combustible de emergencia
                reserve = int(getattr(self.cfg, "deuterium_reserve", 0) or 0)
                if reserve > 0 and p.resources.deut - target.fuel_cost < reserve:
                    reasons_by_planet[str(p.coords)] = "Reserva de deuterio insuficiente"
                    continue

                dist_value = gd.distance(p.coords.tuple(), coords.tuple())
                atk_info = {
                    "target": target,
                    "origin": p,
                    "fleet": atk_fleet,
                    "distance": dist_value,
                    "full_loot": full_loot
                }

                # Conservar el origen que dé el mejor score
                if best_atk_for_target is None or target.score > best_atk_for_target["target"].score:
                    best_atk_for_target = atk_info

            if best_atk_for_target:
                best_attacks_by_target[key] = best_atk_for_target
                score_val = best_atk_for_target["target"].score
                origin_str = str(best_atk_for_target["origin"].coords)
                self.record_session_action("espionage", f"{coords}", f"Apto para ataque (Score: {score_val:.0f})", origin_str)
            else:
                if not reasons_by_planet:
                    summary_reason = "No rentable o sin flota"
                else:
                    summary_reason = "; ".join(f"{p_c}: {r}" for p_c, r in reasons_by_planet.items())
                self.record_session_action("espionage", f"{coords}", f"Descartado: {summary_reason}")

        valid_attacks = list(best_attacks_by_target.values())

        # Ordenar por distancia ascendente (prioridad para los más cercanos), y por score descendente en caso de empate
        valid_attacks.sort(key=lambda x: (x["distance"], -x["target"].score))

        # ── Fase 5: ejecutar ataques ──────────────────────────────────
        attacked = 0
        pending_harvests: List[dict] = []  # recolecciones tras ataques con combate
        for attack in valid_attacks:
            if attacked >= limit:
                break
            if not self._has_free_slots_for_mission():
                self.log.info("Farmeo: deteniendo ataques por falta de slots libres (se requiere dejar 1 libre).")
                break

            target = attack["target"]
            origin = attack["origin"]
            fleet_to_send = attack["fleet"]
            coords = target.coords
            full_loot = attack["full_loot"]

            # Comprobar de nuevo si el origen aún conserva la flota requerida (por si se enviaron naves en un ataque previo en este mismo ciclo)
            if not can_afford_fleet(origin, fleet_to_send):
                # Intentar buscar otro origen elegible sobre la marcha
                best_alt = None
                for p in eligible_locations:
                    alt_fleet = size_fleet(p, full_loot, target.report)
                    if alt_fleet is None:
                        continue
                    if not can_afford_fleet(p, alt_fleet):
                        continue
                    if tgt.cargo_capacity(alt_fleet) == 0:
                        continue
                    
                    alt_target = tgt.evaluate(target.report, p.coords, alt_fleet, self.my_tech, self.cfg)
                    if not alt_target:
                        continue

                    reserve = int(getattr(self.cfg, "deuterium_reserve", 0) or 0)
                    if reserve > 0 and p.resources.deut - alt_target.fuel_cost < reserve:
                        continue

                    if best_alt is None or alt_target.score > best_alt["target"].score:
                        best_alt = {
                            "target": alt_target,
                            "origin": p,
                            "fleet": alt_fleet
                        }
                
                if not best_alt:
                    self.log.debug("Ataque a %s omitido: ningún origen alternativo puede realizar el ataque.", coords)
                    continue
                
                origin = best_alt["origin"]
                target = best_alt["target"]
                fleet_to_send = best_alt["fleet"]

            # Calcular duración estimada del viaje de ataque (ida y vuelta)
            slowest_speed = min((gd.SHIPS[ship].speed for ship in fleet_to_send if fleet_to_send[ship] > 0), default=1)
            dist = gd.distance(origin.coords.tuple(), coords.tuple())
            ftime = gd.flight_time(dist, slowest_speed, 1.0, self.cfg.fleet_speed)
            total_duration = 2 * ftime
            
            # Segundos restantes del horario activo
            seconds_left = utils.seconds_until_inactive(self.cfg.active_hours)
            
            if total_duration > seconds_left:
                self.log.info("Farmeo: omitiendo ataque desde %s a %s por horario de descanso inminente (tardaría %.1f min y quedan %.1f min activos).",
                              origin.coords, coords, total_duration / 60.0, seconds_left / 60.0)
                continue

            if not self._guard():
                break

            ok = self.client.send_fleet(origin.coords, coords, fleet_to_send, mission="attack")
            if ok:
                self._deduct_ships(origin, fleet_to_send)
                self.active_slots += 1
                attacked += 1
                self.log.info("Ataque desde %s: %s loot~%d score=%.0f (distancia=%d)",
                               origin.coords, coords, int(target.expected_loot.value()), target.score, attack["distance"])
                # Registrar acción de la sesión
                self.record_session_action("farming", f"{coords}", 1, str(origin.coords))
                self.record_session_action("espionage", f"{coords}", f"Ataque enviado (Score: {target.score:.0f})", str(origin.coords))
                # Guardar timestamp de ataque para el cooldown
                coords_str = f"{coords.galaxy}:{coords.system}:{coords.position}"
                self.attack_history[coords_str] = time.time()
                # Vuelta estimada (ida+vuelta) para despertar y relanzar la ronda
                self._farming_returns.append(time.time() + total_duration)
                self._save_state()

                # Reciclaje del combate: sonda suicida para crear el campo de
                # escombros y poder despachar los recicladores calculados.
                if getattr(self.cfg, "farm_recycle_debris", True) and target.needs_clearing:
                    debris = dict(getattr(target, "expected_debris", None) or {})
                    if sum(debris.values()) >= getattr(self.cfg, "recycling_min_debris", 8000):
                        job = self._suicide_probe_for_debris(coords, debris, locations,
                                                             attack_eta_s=ftime)
                        if job:
                            pending_harvests.append(job)

        if pending_harvests:
            self._harvest_after_attacks(pending_harvests, locations)

        self.log.info("Farmeo completado: %d ataques en este ciclo.", attacked)

    def _suicide_probe_for_debris(self, coords: Coords, debris: dict, locations,
                                  attack_eta_s: float) -> Optional[dict]:
        """Lanza 1 sonda en misión de ATAQUE contra un objetivo defendido: la sonda
        muere en el combate y crea el campo de escombros, lo que permite despachar
        los recicladores sin esperar a que llegue la flota principal. Devuelve el
        trabajo de recolección pendiente ({coords, debris, ready_at}) o None."""
        if not any(p.ships.get("recycler", 0) >= 1 for p in locations):
            self.log.info("Farmeo: sin recicladores en ninguna ubicación; omito la sonda suicida hacia %s.", coords)
            return None
        candidates = [p for p in locations if p.ships.get("espionage_probe", 0) >= 1]
        if not candidates:
            self.log.info("Farmeo: sin sondas disponibles para la sonda suicida hacia %s.", coords)
            return None
        probe_origin = min(candidates, key=lambda p: gd.distance(p.coords.tuple(), coords.tuple()))
        if not self._has_free_slots_for_mission():
            self.log.info("Farmeo: sin slots libres para la sonda suicida hacia %s.", coords)
            return None
        if not self._guard():
            return None
        ok = self.client.send_fleet(probe_origin.coords, coords,
                                    {"espionage_probe": 1}, mission="attack")
        if not ok:
            self.log.warning("Farmeo: fallo al enviar la sonda suicida hacia %s.", coords)
            return None
        self._deduct_ships(probe_origin, {"espionage_probe": 1})
        self.active_slots += 1

        now = time.time()
        probe_speed = max(1, gd.effective_speed("espionage_probe", self.research_levels))
        probe_eta = now + gd.flight_time(gd.distance(probe_origin.coords.tuple(), coords.tuple()),
                                         probe_speed, 1.0, self.cfg.fleet_speed)
        # Los recicladores no deben llegar ANTES que la flota principal: solo
        # recogerían los restos de la sonda. Se retrasa el despacho para que su
        # llegada quede después del combate principal (con 60s de margen).
        rec_candidates = [p for p in locations if p.ships.get("recycler", 0) >= 1]
        rec_origin = min(rec_candidates, key=lambda p: gd.distance(p.coords.tuple(), coords.tuple()))
        rec_ftime = gd.flight_time(gd.distance(rec_origin.coords.tuple(), coords.tuple()),
                                   gd.SHIPS["recycler"].speed, 1.0, self.cfg.fleet_speed)
        ready_at = max(probe_eta + 15, now + attack_eta_s + 60 - rec_ftime)
        self.log.info("Farmeo: sonda suicida %s -> %s para crear el campo de escombros (recicladores en ~%.1f min).",
                      probe_origin.coords, coords, max(0.0, ready_at - now) / 60)
        self.record_session_action("espionage", f"{coords}", "Sonda suicida enviada (crear escombros)",
                                   str(probe_origin.coords))
        return {"coords": coords, "debris": debris, "ready_at": ready_at}

    def _harvest_after_attacks(self, jobs: List[dict], locations):
        """Espera a que la sonda suicida haya creado el campo de escombros y envía
        los recicladores dimensionados con los escombros simulados del combate."""
        jobs.sort(key=lambda j: j["ready_at"])
        for job in jobs:
            coords = job["coords"]
            wait = job["ready_at"] - time.time()
            # ponytail: espera bloqueante con tope de 10 min; si el despacho óptimo
            # queda más lejos, lo recogerá la ronda de reciclaje normal.
            if wait > 600:
                self.log.info("Farmeo: recolección en %s requeriría esperar %.1f min; la recogerá la ronda de reciclaje.",
                              coords, wait / 60)
                continue
            if wait > 0:
                self.log.info("Farmeo: esperando %.0f s al campo de escombros de %s...", wait, coords)
                time.sleep(wait)
            dest_key = f"{coords.galaxy}:{coords.system}:{coords.position}"
            if self.inflight_dests.get(dest_key, set()) & {"8", "harvest", "recycle"}:
                self.log.info("Farmeo: ya hay una recolección en vuelo hacia %s.", coords)
                continue
            candidates = [p for p in locations if p.ships.get("recycler", 0) >= 1]
            if not candidates:
                self.log.info("Farmeo: sin recicladores disponibles para los escombros de %s.", coords)
                break
            origin = min(candidates, key=lambda p: gd.distance(p.coords.tuple(), coords.tuple()))
            n = min(fleet_mod.recycler_count(job["debris"]), origin.ships.get("recycler", 0))
            if n <= 0:
                continue
            if not self._has_free_slots_for_mission():
                self.log.info("Farmeo: sin slots libres para los recicladores hacia %s.", coords)
                break
            if not self._guard():
                break
            plan = fleet_mod.harvest_plan(origin.coords, coords, job["debris"])
            plan["ships"] = {"recycler": n}
            ok = self.client.send_fleet(plan["origin"], plan["destination"],
                                        plan["ships"], mission="harvest")
            if ok:
                self._deduct_ships(origin, plan["ships"])
                self.inflight_dests.setdefault(dest_key, set()).add("harvest")
                self.active_slots += 1
                self.log.info("Farmeo: %d reciclador(es) %s -> escombros de %s (esperado: %s).",
                              n, origin.coords, coords,
                              {k: int(v) for k, v in job["debris"].items()})
                self.record_session_action("espionage", f"{coords}",
                                           f"Recicladores enviados ({n})", str(origin.coords))

    def _recycle(self, locations):
        locations_with_recyclers = [p for p in locations
                                    if p.ships.get("recycler", 0) >= 1
                                    and self._get_planet_setting(p, "enable_recycling", True)
                                    and self._origin_ok(p, "recycling")]
        if not locations_with_recyclers:
            self.log.debug("Reciclaje omitido: sin ubicaciones con recicladores disponibles y activos en hangar.")
            return

        fields = self.client.read_debris_fields(locations_with_recyclers)
        if not fields:
            return

        # Helper para encontrar la ubicación con recicladores más cercana
        def get_best_recycle_origin(target_coords) -> Optional[Planet]:
            eligible = [p for p in locations_with_recyclers if p.ships.get("recycler", 0) >= 1]
            if not eligible:
                return None
            return min(eligible, key=lambda p: gd.distance(p.coords.tuple(), target_coords.tuple()))

        for f in fields:
            if not self._has_free_slots_for_mission():
                self.log.info("Reciclaje: deteniendo envío por falta de slots libres.")
                break
            
            target_coords = tgt.parse_coords(f["coords"])
            # No duplicar recolecciones: si ya vuela una misión de recolección a esas
            # coords (leída de movimientos en este ciclo), saltar este campo.
            dest_key = f"{target_coords.galaxy}:{target_coords.system}:{target_coords.position}"
            if self.inflight_dests.get(dest_key, set()) & {"8", "harvest", "recycle"}:
                self.log.info("Reciclaje: omitido en %s: ya hay una recolección en vuelo hacia esas coordenadas.",
                              target_coords)
                continue
            origin_loc = get_best_recycle_origin(target_coords)
            if not origin_loc:
                self.log.info("Reciclaje: sin ubicaciones con recicladores disponibles para %s.", target_coords)
                break

            avail = origin_loc.ships.get("recycler", 0)
            needed = fleet_mod.recycler_count(f["debris"])
            n = min(needed, avail)
            
            self.log.info("Reciclaje: evaluando campo en %s (escombros=%s). Necesita %d reciclador(es).",
                          target_coords, f["debris"], needed)
            self.log.info("Reciclaje: ubicación con recicladores más cercana %s tiene %d libre(s).",
                          origin_loc.coords, avail)

            if n <= 0:
                if avail == 0:
                    self.log.info("Reciclaje: omitido en %s porque %s tiene 0 recicladores libres.", target_coords, origin_loc.coords)
                else:
                    self.log.info("Reciclaje: omitido en %s porque se requieren 0 recicladores.", target_coords)
                continue

            # Calcular duración estimada del viaje de reciclaje (ida y vuelta)
            dist = gd.distance(origin_loc.coords.tuple(), target_coords.tuple())
            slowest_speed = gd.SHIPS["recycler"].speed
            ftime = gd.flight_time(dist, slowest_speed, 1.0, self.cfg.fleet_speed)
            total_duration = 2 * ftime
            
            # Segundos restantes del horario activo
            seconds_left = utils.seconds_until_inactive(self.cfg.active_hours)
            
            if total_duration > seconds_left:
                self.log.info("Reciclaje: omitiendo envío a %s por horario de descanso inminente (tardaría %.1f min y quedan %.1f min activos).",
                              target_coords, total_duration / 60.0, seconds_left / 60.0)
                continue

            if not self._guard():
                break

            plan = fleet_mod.harvest_plan(origin_loc.coords, target_coords, f["debris"])
            plan["ships"] = {"recycler": n}

            ok = self.client.send_fleet(plan["origin"], plan["destination"],
                                   plan["ships"], mission="harvest")
            if ok:
                self._deduct_ships(origin_loc, plan["ships"])
                self.inflight_dests.setdefault(dest_key, set()).add("harvest")
                self.active_slots += 1

    def _expeditions(self, home: Coords, ships: Optional[dict] = None,
                     target_system: Optional[int] = None) -> bool:
        if not self._has_free_slots_for_mission():
            self.log.info("Expedición: deteniendo envío por falta de slots de flota libres.")
            return False
        if not self._has_free_expe_slots():
            self.log.info("Expedición: deteniendo envío por falta de slots de expedición libres (%d/%d).",
                          self.active_expe_slots, self._total_expe_slots_effective())
            return False
        destination = None
        if target_system is not None:
            destination = Coords(home.galaxy, target_system, self.cfg.expedition_position)
        plan = fleet_mod.expedition_plan(home, self.cfg, destination=destination, ships=ships)

        # Calcular duración estimada del vuelo (ida y vuelta + permanencia).
        # Usa la velocidad real (con motor) y el factor de calibración aprendido del
        # juego, para no rechazar expediciones por una estimación inflada.
        dist = gd.distance(home.tuple(), plan["destination"].tuple())
        slowest_speed = max(1, min((gd.effective_speed(ship, self.research_levels)
                                    for ship in plan["ships"] if plan["ships"][ship] > 0), default=1))
        ftime = gd.flight_time(dist, slowest_speed, 1.0, self.cfg.fleet_speed)
        cal = getattr(self, "expedition_flight_cal", 1.0) or 1.0
        est_oneway = ftime * cal  # solo respaldo por si falla la lectura real

        # Tiempo restante hasta el descanso. La decisión real (si vuelve a tiempo) la
        # toma send_fleet leyendo la DURACIÓN EXACTA en la propia página de envío.
        seconds_left = utils.seconds_until_inactive(self.cfg.active_hours)

        # Reserva de deuterio: no despegar si el combustible dejaría el depósito
        # por debajo del mínimo de emergencia (fleetsave/evasión).
        reserve = int(getattr(self.cfg, "deuterium_reserve", 0) or 0)
        if reserve > 0:
            origin_loc = next((l for p in self.last_planets
                               for l in [p] + ([p.moon] if getattr(p, "moon", None) else [])
                               if l.coords.tuple() == home.tuple() and l.coords.type == home.type), None)
            if origin_loc is not None:
                fuel = 2 * gd.fuel_cost(plan["ships"], dist, 1.0, self.cfg.fleet_speed)
                if origin_loc.resources.deut - fuel < reserve:
                    self.log.info("Expedición desde %s omitida: dejaría el deuterio bajo la reserva "
                                  "(%d - %.0f < %d).", home, origin_loc.resources.deut, fuel, reserve)
                    return False

        if self._guard():
            self.client.last_flight_seconds = None
            ok = self.client.send_fleet(home, plan["destination"], plan["ships"],
                                   mission="expedition", hold_hours=plan["hold_hours"],
                                   max_round_trip_s=seconds_left)
            if ok:
                self.active_slots += 1
                self.active_expe_slots += 1
                # Calibrar con el tiempo de vuelo REAL leído del juego (real/estimado)
                real_oneway = getattr(self.client, "last_flight_seconds", None)
                if real_oneway and ftime > 0:
                    ratio = real_oneway / ftime
                    if 0.02 <= ratio <= 5.0:
                        prev_cal = getattr(self, "expedition_flight_cal", None)
                        if prev_cal is None or prev_cal == 1.0:
                            # Primera muestra (valor por defecto): adoptarla tal cual.
                            self.expedition_flight_cal = ratio
                        else:
                            # EMA para que una muestra atípica no descalibre el factor.
                            self.expedition_flight_cal = 0.7 * prev_cal + 0.3 * ratio
                        self.log.info("Calibración de vuelo de expedición: real=%.0fs estimado=%.0fs "
                                      "-> muestra %.2f, factor %.2f",
                                      real_oneway, ftime, ratio, self.expedition_flight_cal)
                # Feature #2: registrar la vuelta (real si la tenemos) para reactivarse
                ret_oneway = real_oneway if real_oneway else est_oneway
                self._expedition_returns.append(time.time() + 2 * ret_oneway + plan["hold_hours"] * 3600)
                self.record_session_action("expeditions", f"{plan['destination']}", 1, str(home))
                return True
        return False

    def _run_expeditions_round(self, planets, all_locations):
        """
        Lanza expediciones desde cada ubicación habilitada. Dos modos:
          - manual: usa cfg.expedition_ships tal cual.
          - auto: calcula los cargueros óptimos para el botín máximo del universo
            y los reparte entre todos los slots libres para maximizar el nº de
            expediciones (y la rentabilidad). Rota el sistema destino si procede.
        """
        auto = bool(getattr(self.cfg, "expedition_auto_ships", False))
        cargo_ship = getattr(self.cfg, "expedition_cargo_ship", "large_cargo") or "large_cargo"
        use_pf = bool(getattr(self.cfg, "expedition_use_pathfinder", False))
        min_cargo = max(1, int(getattr(self.cfg, "expedition_min_cargo", 1) or 1))

        def parent_of(loc):
            return loc if loc.coords.type == "planet" else next(
                p for p in planets if p.coords.tuple() == loc.coords.tuple())

        enabled_locs = [loc for loc in all_locations
                        if self._get_planet_setting(parent_of(loc), "enable_expeditions", True)
                        and self._origin_ok(loc, "expeditions")]

        top1 = max_find = optimal = per_exp_auto = 0
        optimal_nopf = optimal_pf = 0
        sc_optimal_nopf = sc_optimal_pf = 0  # fallback NPG por ubicación
        spread_cap = None  # None = cada expedición a su óptimo; int = reparto por slot
        if auto:
            top1 = self._expedition_top1_points()
            safety = float(getattr(self.cfg, "expedition_find_safety", 1.0))
            discoverer = bool(getattr(self.cfg, "expedition_discoverer_class", False))
            # Dimensionado distinto con/sin pathfinder: solo se dobla el botín en las
            # expediciones que de verdad llevan un pathfinder (no en todas).
            hyper = int(getattr(self.cfg, "expedition_hyperspace_level", 0) or 0)
            if hyper <= 0:
                hyper = int(self.research_levels.get("hyperspace_tech", 0)) if self.research_levels else 0
            base_find = gd.expedition_max_find_units(top1, self.cfg.universe_speed, discoverer, False)
            optimal_nopf = fleet_mod.optimal_expedition_cargo(base_find, cargo_ship, safety, hyper)
            if use_pf:
                pf_find = gd.expedition_max_find_units(top1, self.cfg.universe_speed, discoverer, True)
                optimal_pf = fleet_mod.optimal_expedition_cargo(pf_find, cargo_ship, safety, hyper)
            else:
                pf_find = base_find
                optimal_pf = optimal_nopf
            max_cargo = int(getattr(self.cfg, "expedition_max_cargo", 0) or 0)
            if max_cargo > 0:
                optimal_nopf = min(optimal_nopf, max_cargo)
                optimal_pf = min(optimal_pf, max_cargo)
            # Precalcular óptimo de NPG para fallback por ubicación (si cargo_ship es NGC)
            if cargo_ship == "large_cargo":
                sc_optimal_nopf = fleet_mod.optimal_expedition_cargo(base_find, "small_cargo", safety, hyper)
                sc_pf_find = pf_find if use_pf else base_find
                sc_optimal_pf = fleet_mod.optimal_expedition_cargo(sc_pf_find, "small_cargo", safety, hyper)
                if max_cargo > 0:
                    sc_optimal_nopf = min(sc_optimal_nopf, max_cargo)
                    sc_optimal_pf = min(sc_optimal_pf, max_cargo)
            free_slots = max(1, (self._total_expe_slots_effective() or 1) - self.active_expe_slots)
            # total_avail: NGC de todo el imperio; también contar NPG para el spread si no hay NGC
            total_avail = sum(loc.ships.get(cargo_ship, 0) for loc in enabled_locs)
            if total_avail == 0 and cargo_ship == "large_cargo":
                total_avail = sum(loc.ships.get("small_cargo", 0) for loc in enabled_locs)
            # ¿Hay cargueros de sobra para llenar todos los slots al óptimo? Si no, repartir.
            if total_avail >= optimal_nopf * free_slots:
                spread_cap = None
            else:
                spread_cap = max(min_cargo, total_avail // free_slots)
            max_find = pf_find if use_pf else base_find
            optimal = optimal_pf if use_pf else optimal_nopf
            per_exp_auto = optimal if spread_cap is None else spread_cap
            self.log.info(
                "Auto-expediciones: Top1=%s pts, botín_máx=%s u, óptimo x1=%d / pf=%d %s, "
                "slots libres=%d, disponibles=%d, reparto=%s",
                top1, max_find, optimal_nopf, optimal_pf, cargo_ship,
                free_slots, total_avail, "óptimo" if spread_cap is None else per_exp_auto)

        for loc in enabled_locs:
            manual_ships = None
            if not auto:
                manual_ships = {k: v for k, v in self.cfg.expedition_ships.items() if v > 0}
                if not manual_ships:
                    manual_ships = {"large_cargo": 1}

            while self._has_free_slots_for_mission() and self._has_free_expe_slots():
                if auto:
                    ships = self._auto_exp_ships(cargo_ship, optimal_nopf, optimal_pf,
                                                 spread_cap, use_pf, loc.ships, min_cargo,
                                                 sc_optimal_nopf, sc_optimal_pf)
                else:
                    ships = dict(manual_ships)
                    if not all(loc.ships.get(s, 0) >= q for s, q in ships.items()):
                        ships = None
                    else:
                        # Escolta de destructores (capada al hangar; no bloquea la expedición
                        # si tienes menos). Solo si no los listaste ya a mano.
                        dest_n = int(getattr(self.cfg, "expedition_destroyer_count", 0) or 0)
                        if dest_n > 0 and "destroyer" not in ships and loc.ships.get("destroyer", 0) > 0:
                            ships["destroyer"] = min(dest_n, loc.ships.get("destroyer", 0))
                if not ships:
                    break

                target_system = None
                if getattr(self.cfg, "expedition_rotate_systems", True):
                    target_system = fleet_mod.expedition_rotation_system(
                        loc.coords.system,
                        int(getattr(self.cfg, "expedition_system_range", 15)),
                        self.expedition_rotation_index)

                if self._expeditions(loc.coords, ships=ships, target_system=target_system):
                    self.expedition_rotation_index += 1
                    self._deduct_ships(loc, ships)
                else:
                    break

        self._write_expedition_status(top1, max_find, optimal, per_exp_auto, cargo_ship, auto)

    def _auto_exp_ships(self, cargo_ship, optimal_nopf, optimal_pf, spread_cap,
                        use_pf, avail, min_cargo, sc_optimal_nopf=0, sc_optimal_pf=0):
        """
        Dict de naves para una expedición auto, limitado por el hangar. Decide PRIMERO
        si esta expedición lleva pathfinder (solo si lo hay) y dimensiona la carga al
        óptimo correspondiente (x2 con pathfinder, x1 sin él), sin pasar del reparto.
        Si esta ubicación no tiene el carguero principal (NGC) pero sí NPG, usa NPG
        como fallback sin afectar el resto de ubicaciones del mismo ciclo.
        """
        will_pf = use_pf and cargo_ship != "pathfinder" and avail.get("pathfinder", 0) >= 1
        loc_optimal = optimal_pf if will_pf else optimal_nopf
        target = loc_optimal if spread_cap is None else min(loc_optimal, spread_cap)
        n = min(target, avail.get(cargo_ship, 0))
        # Fallback por ubicación: si no hay NGC aquí pero sí NPG, usarlos
        effective_ship = cargo_ship
        if n < min_cargo and cargo_ship == "large_cargo" and sc_optimal_nopf > 0:
            sc_loc_opt = sc_optimal_pf if will_pf else sc_optimal_nopf
            sc_target = sc_loc_opt if spread_cap is None else min(sc_loc_opt, spread_cap)
            sc_n = min(sc_target, avail.get("small_cargo", 0))
            if sc_n >= min_cargo:
                effective_ship = "small_cargo"
                n = sc_n
        if n < min_cargo:
            return None
        ships = {effective_ship: n}
        if will_pf:
            ships["pathfinder"] = 1
        # Destructor(es) opcionales para sobrevivir/ganar combates de expedición (si los hay)
        dest_n = int(getattr(self.cfg, "expedition_destroyer_count", 0) or 0)
        if dest_n > 0 and cargo_ship != "destroyer" and avail.get("destroyer", 0) > 0:
            ships["destroyer"] = min(dest_n, avail.get("destroyer", 0))
        # Sonda(s) de espionaje opcionales con cada expedición (si las hay en el hangar)
        if getattr(self.cfg, "expedition_send_probe", False) and cargo_ship != "espionage_probe":
            pc = max(1, int(getattr(self.cfg, "expedition_probe_count", 1) or 1))
            if avail.get("espionage_probe", 0) >= pc:
                ships["espionage_probe"] = pc
        return ships

    def _expedition_top1_points(self) -> int:
        """Puntos del Top-1 del universo (override de config, o API con caché de 6h)."""
        override = int(getattr(self.cfg, "expedition_top1_points", 0) or 0)
        if override > 0:
            return override
        now = time.time()
        ts, val = self._expedition_top1_cache
        if val > 0 and now - ts < 6 * 3600:
            return val
        pts = 0
        if self.api:
            try:
                pts = self.api.top_player_points(0)
            except Exception as e:
                self.log.debug("No se pudo leer puntos Top-1 del universo: %s", e)
        self._expedition_top1_cache = (now, pts)
        return pts

    def _update_next_expedition_event(self):
        """
        Próximo instante (epoch) en que conviene despertar para reenviar expediciones:
        cuando vuelve la primera, pero NUNCA antes de que el intervalo de ronda lo permita
        (así el despertar coincide con que el envío vuelva a estar autorizado).
        """
        now = time.time()
        self._expedition_returns = [e for e in self._expedition_returns if e > now]
        if not self._expedition_returns:
            self.next_expedition_event = 0.0
            return
        soonest = min(self._expedition_returns)
        interval = max(0, int(getattr(self.cfg, "expeditions_run_interval_mins", 0) or 0))
        boundary = self.last_expeditions_run_time + interval * 60
        self.next_expedition_event = max(soonest, boundary)

    def _update_next_farming_event(self):
        """
        Próximo instante (epoch) para despertar y relanzar el farmeo: cuando vuelve la
        primera flota de ataque, pero NUNCA antes de que el intervalo de ronda lo permita.
        Mismo patrón que _update_next_expedition_event.
        """
        now = time.time()
        self._farming_returns = [e for e in self._farming_returns if e > now]
        if not self._farming_returns:
            self.next_farming_event = 0.0
            return
        soonest = min(self._farming_returns)
        interval = max(0, int(getattr(self.cfg, "farming_run_interval_mins", 0) or 0))
        boundary = self.last_farming_run_time + interval * 60
        self.next_farming_event = max(soonest, boundary)

    def _record_target_loot(self, coords_str: str, metal: int, crystal: int, deut: int):
        """Acumula el botín REAL de un combate ganado en el historial del objetivo
        (aprendizaje del farmeo). Ignora coordenadas propias (defensas ganadas)."""
        own = {f"{p.coords.galaxy}:{p.coords.system}:{p.coords.position}"
               for p in (self.last_planets or [])}
        if not coords_str or coords_str in own:
            return
        val = Resources(metal, crystal, deut).value(self.cfg.trade_ratio)
        e = self.target_stats.setdefault(coords_str, {"raids": 0, "loot": 0.0, "last": 0.0})
        e["raids"] = int(e.get("raids", 0) or 0) + 1
        e["loot"] = float(e.get("loot", 0.0) or 0.0) + val
        e["last"] = time.time()

    def _write_expedition_status(self, top1, max_find, optimal, per_exp, cargo_ship, auto):
        """Escribe expedition_status.json para que la GUI muestre cálculo y temporizadores."""
        try:
            import json
            now = time.time()
            returns = sorted(e for e in self._expedition_returns if e > now)
            status = {
                "auto_ships": bool(auto),
                "top1_points": top1,
                "max_find_units": max_find,
                "optimal_cargo": optimal,
                "cargo_per_expedition": per_exp,
                "cargo_ship": cargo_ship,
                "rotate_systems": bool(getattr(self.cfg, "expedition_rotate_systems", True)),
                "system_range": int(getattr(self.cfg, "expedition_system_range", 15)),
                "rotation_index": self.expedition_rotation_index,
                "active_expe_slots": self.active_expe_slots,
                "total_expe_slots": self._total_expe_slots_effective(),
                "next_event_epoch": self.next_expedition_event,
                "returns_epochs": returns,
                "updated_at": now,
            }
            utils.atomic_write_json("expedition_status.json", status)
        except Exception as e:
            self.log.debug("No se pudo guardar expedition_status.json: %s", e)

    def _colonize(self, planets):
        occupied = {p.coords.tuple() for p in planets}
        # Añadir las posiciones ocupadas de TODO el universo (API): sin esto solo se
        # evitaban nuestras propias coordenadas y se intentaba colonizar sitios llenos.
        if self.api:
            try:
                occupied |= self.api.occupied_positions()
            except Exception as e:
                self.log.debug("No se pudieron leer posiciones ocupadas del universo: %s", e)
        if len(planets) >= self.cfg.max_colonies:
            return
        if not self._has_ships(planets, "colony_ship"):
            self.log.debug("Colonización omitida: sin nave de colonización.")
            return
        if not self._has_free_slots_for_mission():
            self.log.info("Colonización: deteniendo envío por falta de slots libres.")
            return
        dest = moons.pick_colony(occupied, self.cfg, home_coords=planets[0].coords)
        if dest and self._guard():
            self.log.info("Colonizando %s", dest)
            ok = self.client.send_fleet(planets[0].coords, dest,
                                   {"colony_ship": 1}, mission="colonize")
            if ok:
                self._deduct_ships(planets[0], {"colony_ship": 1})
                self.active_slots += 1

    def _moonshot(self, planets):
        plan = moons.plan_moonshot(planets[0].coords, self.cfg)
        self.log.info("Plan de luna: %s naves %s (prob %.0f%%). %s",
                      plan["ships_to_crash"], plan["sacrifice_ship"],
                      plan["expected_chance"] * 100, plan["note"])

    def _continue_until_idle(self):
        """
        Tras el ciclo principal, sondea las colas de construcción cada ~45s.
        En cuanto un slot se libera, inicia la siguiente construcción de inmediato
        en lugar de esperar el intervalo de ciclo completo.
        Garantiza progreso continuo al inicio del juego (donde los builds son breves).
        """
        if not getattr(self.cfg, "enable_economy", True) and not getattr(self.cfg, "enable_lifeforms", True):
            return
        planets = self.last_planets
        if not planets:
            return

        POLL = 45  # segundos entre sondeos
        # No extendemos más allá del intervalo de ciclo mínimo configurado
        deadline = time.time() + self.cfg.cycle_interval_min_s
        last_attack_check = time.time()  # ya se comprobó justo antes del ciclo
        next_attack_delay = self._attack_check_interval()

        while time.time() < deadline:
            still_busy = [p for p in planets
                          if p.building_in_progress or p.lifeform_in_progress]
            if not still_busy:
                self.log.debug("Todas las colas libres; terminando _continue_until_idle.")
                break

            wait = min(POLL, deadline - time.time() - 5)
            if wait <= 0:
                break

            self.log.info("Construcciones activas (%d planeta/s). Sondeo en %.0fs.",
                          len(still_busy), wait)
            time.sleep(wait)

            # Comprobar ataques hostiles también mientras esperamos construcciones:
            # reduce la latencia de detección en el hueco largo entre ciclos.
            if getattr(self.cfg, "enable_attack_escape", True) and \
               time.time() - last_attack_check >= next_attack_delay:
                last_attack_check = time.time()
                next_attack_delay = self._attack_check_interval()
                try:
                    self._check_and_escape_attacks()
                except Exception as e:
                    self.log.debug("Error comprobando ataques en _continue_until_idle: %s", e)

            for p in planets:
                try:
                    # Comprobar cola de suministros/instalaciones
                    if p.building_in_progress:
                        self.client._goto("overview", p)
                        prev = p.building_in_progress
                        p.building_in_progress = self.client._is_build_queue_active_from_overview()
                        if prev and not p.building_in_progress:
                            self.log.info("%s: construcción completada. Actualizando estado.", p.coords)
                            p.resources = self.client.read_resources()
                            for comp in ("supplies", "facilities"):
                                p.buildings.update(self.client._read_tech_page(comp, p))

                    # Comprobar cola de Formas de vida
                    if p.lifeform_in_progress:
                        self.client._goto("lfbuildings", p)
                        try:
                            self.client._wait_tech(timeout=4000)
                            prev_lf = p.lifeform_in_progress
                            p.lifeform_in_progress = self.client._is_lf_queue_active()
                            if prev_lf and not p.lifeform_in_progress:
                                self.log.info("%s: Forma de vida completada.", p.coords)
                        except Exception:
                            p.lifeform_in_progress = False
                except Exception as e:
                    self.log.debug("Poll error en %s: %s", p.coords, e)

            # Construir lo siguiente en los planetas con slot libre
            for p in planets:
                if not p.building_in_progress:
                    self._economy_step(p)
                    self._facilities_step(p)
                if not p.lifeform_in_progress:
                    self._lifeforms_step(p)
                self._defense_step(p)
                self._facilities_step(p)

    def _facilities_step(self, planet):
        if not self._get_planet_setting(planet, "enable_facilities", True):
            self.log.debug("%s: instalaciones desactivadas para este planeta.", planet.coords)
            return
        if self._active_queue_entry(planet):
            self.log.debug("%s: cola de construcción activa, las instalaciones ceden el paso.", planet.coords)
            return
        if planet.building_in_progress:
            self.log.debug("%s: construcción en progreso, saltando instalaciones.", planet.coords)
            return

        target_robotics = self._get_planet_setting(planet, "target_robotics_factory", 0)
        target_shipyard = self._get_planet_setting(planet, "target_shipyard", 0)
        target_lab = self._get_planet_setting(planet, "target_research_lab", 0)
        target_nanite = self._get_planet_setting(planet, "target_nanite_factory", 0)
        if getattr(self.cfg, "empire_auto", False):
            # Autogestión: objetivos de instalaciones si el usuario no fijó otros mayores.
            # ponytail: niveles fijos de medio juego (astillero 8 = naves de batalla);
            # escalarlos con la economía si el imperio los deja atrás.
            target_robotics = max(target_robotics, 10)
            target_shipyard = max(target_shipyard, 8)
            target_lab = max(target_lab, 12)

        # Buscar qué instalación subir de nivel
        to_upgrade = None
        for facility, target_val in [
            ("robotics_factory", target_robotics),
            ("shipyard", target_shipyard),
            ("research_lab", target_lab),
            ("nanite_factory", target_nanite),
        ]:
            current_lvl = planet.lvl(facility)
            if current_lvl < target_val:
                from .prereqs import resolve_prerequisites
                res = resolve_prerequisites("building", facility, current_lvl + 1, planet, self.research_levels)
                if res and res[0] == "building":
                    actual_name = res[1]
                    actual_lvl = res[2]
                    cost = gd.building_cost(actual_name, actual_lvl)
                    to_upgrade = (actual_name, cost)
                    break

        if to_upgrade:
            name, cost = to_upgrade
            buf = 1 - self.cfg.keep_resources_buffer
            avail = Resources(planet.resources.metal * buf,
                              planet.resources.crystal * buf,
                              planet.resources.deut * buf)

            if avail.can_afford(cost):
                if self._guard():
                    self.log.info("Instalaciones %s: construyendo %s (nivel %d) para alcanzar objetivo.",
                                  planet.coords, name, planet.lvl(name) + 1)
                    comp = "facilities" if name in ("robotics_factory", "nanite_factory", "shipyard", "research_lab") else "supplies"
                    ok = self.client.build(planet, comp, name)
                    if ok:
                        self.record_session_action("buildings", name, planet.lvl(name) + 1, str(planet.coords))
                        self._mark_build_started(planet, name, cost)
                    planet.building_in_progress = True
            else:
                plasma = self.research_levels.get("plasma_tech", 0)
                t = economy.time_to_accumulate(cost, planet, self.cfg, plasma)
                max_wait = getattr(self.cfg, "max_saving_hours_economy", 4.0)
                if t <= max_wait:
                    # No marcamos building_in_progress: nada se está construyendo aún. Si lo
                    # marcáramos, _feed_step se saltaría este planeta-objetivo y nunca lo
                    # alimentaría. El próximo ciclo reevalúa con el estado real del juego.
                    self.log.info("%s: ahorrando para instalaciones: %s (tiempo estimado: %.1fh)",
                                  planet.coords, name, t)

    # ------------------------------------------------------------------ #
    # Cola de construcción manual por planeta (tipo Comandante)
    # ------------------------------------------------------------------ #
    def _active_queue_entry(self, planet):
        """Construcción que la cola debe hacer AHORA en este planeta. La cola es DECLARATIVA
        ([{building, target_level}, ...], no se muta): se toma la primera entrada no cumplida
        que sea un edificio CONOCIDO y con prerequisitos satisfechos, y se devuelve
        (real_name, real_lvl, cost). Si la primera entrada pendiente está BLOQUEADA (necesita
        una investigación) o es inválida, se devuelve None: así la economía automática NO cede
        el paso y el planeta sigue progresando en vez de quedarse parado."""
        queue = self._get_planet_setting(planet, "build_queue", []) or []
        from .prereqs import resolve_prerequisites
        for entry in queue:
            name = (entry or {}).get("building")
            try:
                target = int(entry.get("target_level", 0) or 0)
            except (TypeError, ValueError):
                continue
            if not name or name not in gd.BUILDING_COST:
                continue  # entrada inválida/desconocida: la ignoramos (ni construye ni bloquea)
            if planet.lvl(name) >= target:
                continue  # objetivo ya alcanzado: siguiente entrada
            res = resolve_prerequisites("building", name, planet.lvl(name) + 1, planet, self.research_levels)
            if res and res[0] == "building":
                return res[1], res[2], gd.building_cost(res[1], res[2])
            # Bloqueada (p.ej. requiere una investigación): la cola espera aquí, pero dejamos
            # que la economía normal siga para no dejar el planeta sin hacer nada.
            self.log.debug("Cola %s: %s espera prerequisitos (%s); la economía sigue.",
                           planet.coords, name, res)
            return None
        return None

    def _hourly_production(self, planet, plasma):
        """Producción real/hora (metal, crystal, deut). Usa la página de recursos cacheada;
        la relee si falta, si está obsoleta (>1h) o —importante— si han CAMBIADO los niveles
        que afectan a la producción (minas y energía), p.ej. tras construir una mina."""
        key = self._loc_key(planet.coords)
        entry = self.state_cache["planets"].get(key)
        # Firma de los niveles que determinan la producción del planeta.
        sig = [planet.lvl("metal_mine"), planet.lvl("crystal_mine"), planet.lvl("deut_synth"),
               planet.lvl("solar_plant"), planet.lvl("fusion_reactor")]
        prod = (entry or {}).get("hourly_production") if entry else None
        ts = (entry or {}).get("hourly_production_at", 0.0) if entry else 0.0
        cached_sig = (entry or {}).get("hourly_production_levels") if entry else None
        stale = (not prod) or (time.time() - ts > 3600) or (cached_sig != sig)
        if stale and entry is not None:
            fresh = self.client.read_hourly_production(planet)
            if fresh:
                entry["hourly_production"] = fresh
                entry["hourly_production_at"] = time.time()
                entry["hourly_production_levels"] = sig
                self._save_state_cache()
                prod = fresh
        if prod:
            return prod
        return {
            "metal": gd.metal_production(planet.lvl("metal_mine"), plasma, self.cfg.universe_speed),
            "crystal": gd.crystal_production(planet.lvl("crystal_mine"), plasma, self.cfg.universe_speed),
            "deut": gd.deut_production(planet.lvl("deut_synth"), planet.max_temp, plasma, self.cfg.universe_speed),
        }

    def _eta_to_afford(self, planet, cost) -> float:
        """Horas estimadas hasta poder pagar 'cost' con la producción del planeta."""
        plasma = self.research_levels.get("plasma_tech", 0)
        prod = self._hourly_production(planet, plasma)
        avail = planet.resources

        def hrs(need, rate):
            if need <= 0:
                return 0.0
            return need / rate if rate and rate > 0.1 else 999.0

        return max(hrs(cost.metal - avail.metal, prod.get("metal", 0)),
                   hrs(cost.crystal - avail.crystal, prod.get("crystal", 0)),
                   hrs(cost.deut - avail.deut, prod.get("deut", 0)))

    def _build_queue_step(self, planet):
        """Procesa la cola del planeta. Construye la siguiente entrada si hay recursos.
        Devuelve el epoch en que conviene volver (fin de la construcción en curso o cuándo
        habrá recursos), o None si no hay nada accionable."""
        if not self._get_planet_setting(planet, "enable_build_queue", True):
            return None
        active = self._active_queue_entry(planet)
        if not active:
            return None
        real_name, real_lvl, cost = active

        # Si ya hay algo construyéndose en el juego, esperar a que termine para encolar.
        if getattr(planet, "building_in_progress", False):
            rem = getattr(planet, "building_remaining_seconds", 0) or 0
            return time.time() + (rem if rem > 0 else _QUEUE_RETRY_S)

        avail = planet.resources
        if (avail.metal >= cost.metal and avail.crystal >= cost.crystal and avail.deut >= cost.deut):
            if not self._guard():
                return time.time() + _QUEUE_RETRY_S   # rate-limit: reintentar pronto
            comp = "facilities" if real_name in ("robotics_factory", "nanite_factory",
                                                 "shipyard", "research_lab") else "supplies"
            self.log.info("Cola %s: construyendo %s (nivel %d).", planet.coords, real_name, real_lvl)
            ok = self.client.build(planet, comp, real_name)
            if ok:
                self.record_session_action("buildings", real_name, real_lvl, str(planet.coords))
                dur = self._mark_build_started(planet, real_name, cost)
                return time.time() + max(30.0, dur)
            return time.time() + _QUEUE_RETRY_S       # el envío falló: reintentar pronto

        # Sin recursos: estimar cuándo los habrá según la producción real.
        eta_h = self._eta_to_afford(planet, cost)
        self.log.info("Cola %s: ahorrando para %s (nivel %d); ETA %.1f h.",
                      planet.coords, real_name, real_lvl, eta_h)
        if eta_h >= 999:
            return None   # producción nula: reevaluar el próximo ciclo
        return time.time() + max(_QUEUE_ETA_FLOOR_S, eta_h * 3600)

    def _process_build_queues(self, planets):
        """Corre la cola de cada planeta y arma el próximo 'despertar' (el más cercano)."""
        if not getattr(self.cfg, "enable_build_queue", True):
            return
        soonest = 0.0
        for p in planets:
            try:
                wake = self._build_queue_step(p)
            except Exception as e:
                self.log.warning("Cola de construcción %s: error procesando la cola: %s", p.coords, e)
                wake = None
            if wake and wake > time.time():
                soonest = wake if soonest == 0.0 else min(soonest, wake)
        self.next_build_event = soonest

    # ------------------------------------------------------------------ #
    # Alimentación de recursos entre planetas (transporte para construir)
    # ------------------------------------------------------------------ #
    def _feed_step(self, planets, movements=None):
        """Manda el excedente de los planetas-fuente a los planetas-destino que no
        pueden pagar su próxima construcción (p.ej. lab a 12). Destino y fuentes se
        marcan a mano en la pestaña 'Por Planeta'. La luna propia de cada destino es
        candidata automática (sin marcarla). Prioridad de fuente: 1) menos transportes
        (si una sola lo cubre todo, se manda junto); 2) la más cercana."""
        destinos = [p for p in planets if self._get_planet_setting(p, "feed_target", False)]
        # Fuentes: planetas marcados como 'cede recursos' + SUS LUNAS (comparten coords; la luna
        # también puede ceder su excedente, p.ej. lo reciclado). Así, si una sola ubicación
        # —planeta o luna— cubre todo el déficit, se usa esa (1 transporte) en vez de repartir.
        fuentes = []
        for p in planets:
            if self._get_planet_setting(p, "feed_source", False):
                fuentes.append(p)
                moon = getattr(p, "moon", None) if getattr(p, "has_moon", False) else None
                if moon:
                    fuentes.append(moon)
        # Autogestión del imperio: sin marcas manuales, los destinos son los planetas
        # en programa especial (colonias nuevas creciendo) y las fuentes el resto.
        if getattr(self.cfg, "empire_auto", False) and not destinos:
            destinos = [p for p in planets if self._special_program_for(p, planets)]
            if destinos and not fuentes:
                for p in planets:
                    if p in destinos:
                        continue
                    fuentes.append(p)
                    moon = getattr(p, "moon", None) if getattr(p, "has_moon", False) else None
                    if moon:
                        fuentes.append(moon)
        # La luna propia de cada destino alimenta primero (no hace falta marcarla), así que
        # basta con tener destinos: si no hay fuentes marcadas, las lunas pueden cubrirlo.
        if not destinos:
            return

        # Destinos que YA tienen un vuelo entrante: no reenviamos hasta que llegue, para no
        # mandar de más. Fail-closed: cuenta un transporte (3) o una misión DESCONOCIDA (la
        # detección de misión a veces devuelve ''); solo se descartan misiones claramente
        # NO-transporte. En especial deploy (4): los fleetsave son deploy y darían falso
        # positivo. Pasarse de prudente solo retrasa un ciclo; ayuda a no repetir envíos.
        non_transport = {"1", "2", "4", "5", "6", "7", "8", "9", "15",
                         "attack", "deploy", "espionage", "recycle", "colonize",
                         "expedition", "hold", "acs", "destroy"}
        inbound = {}
        for m in (movements or []):
            if m.get("is_hostile") or m.get("is_return"):
                continue
            mission = str(m.get("mission", "")).lower()
            if mission in non_transport:
                continue
            d = m.get("destination", "").replace("[", "").replace("]", "").strip()
            o = m.get("origin", "").replace("[", "").replace("]", "").strip()
            if d and d != o:
                inbound.setdefault(d, m)

        for dst in destinos:
            # Si ya está construyendo algo, lo que pediría está en cola y pagado: no
            # mandar recursos para una subida en curso (evita mandar de más). Respaldamos
            # el flag en vivo (que puede dar falso negativo) con el epoch de la caché.
            if getattr(dst, "building_in_progress", False) or self._build_finish_pending(dst):
                self.log.info("Alimentación: %s ya está construyendo algo; espero a que termine.",
                              dst.coords)
                continue
            dst_key = f"{dst.coords.galaxy}:{dst.coords.system}:{dst.coords.position}"
            if dst_key in inbound:
                mv = inbound[dst_key]
                self.log.info("Alimentación: %s ya tiene un TRANSPORTE entrante (%s -> %s mision=%s ret=%s); "
                              "espero a que llegue.",
                              dst.coords, mv.get("origin"), mv.get("destination"),
                              mv.get("mission"), mv.get("is_return"))
                continue
            need = self._feed_deficit(dst)
            if not need:
                continue
            # Candidatos: la luna propia del destino (sin gasto de vuelo) + las fuentes
            # marcadas. Se excluye el propio destino (un planeta no se alimenta a sí mismo;
            # su luna, misma coords, SÍ vale).
            own_moon = getattr(dst, "moon", None) if getattr(dst, "has_moon", False) else None
            candidates = ([own_moon] if own_moon else []) + [
                s for s in fuentes if s is not dst and s.coords.tuple() != dst.coords.tuple()
            ]
            if not candidates:
                continue
            dist = lambda s: gd.distance(s.coords.tuple(), dst.coords.tuple())
            # Prioridad: 1) MENOS transportes -> si una sola ubicación puede mandarlo TODO,
            # usarla (1 envío) en vez de repartir en varios; 2) la más cercana. Si nadie
            # cubre todo, el que más aporte primero (menos envíos), desempatando por cercanía.
            full = [s for s in candidates
                    if self._feed_sendable(s, need).total() >= need.total() - 1.0]
            if full:
                ordered_srcs = sorted(full, key=dist)
            else:
                ordered_srcs = sorted(
                    candidates,
                    key=lambda s: (-self._feed_sendable(s, need).total(), dist(s)))
            for src in ordered_srcs:
                if not self._has_free_slots_for_mission():
                    self.log.info("Alimentación: sin slots de flota libres; sigo el próximo ciclo.")
                    return
                sent = self._feed_transport(src, dst, need)
                if sent:
                    self.active_slots += 1   # el transporte ocupa un slot hasta que vuelve
                    # UN solo transporte por destino y ciclo: el mejor origen (uno que lo cubra
                    # todo si existe; si no, el que más aporte). Lo que falte se completa en
                    # ciclos siguientes, que la dedupe 'inbound' serializa -> nunca 2 a la vez.
                    # ponytail: menos slots simultáneos; más lento, pero el usuario lo prefiere.
                    break
                # Este origen no pudo enviar (p.ej. < feed_min_send): probar el siguiente.

    def _target_next_build(self, planet):
        """(nombre, coste) de lo próximo que el planeta-destino quiere construir, o None.
        Prioriza los objetivos de instalaciones (lab, astillero...) y, si no hay,
        usa la siguiente construcción que pediría la economía (minas/energía).
        Devuelve None si el objetivo de instalación está BLOQUEADO por una investigación
        pendiente: no tiene sentido alimentar algo que aún no se puede construir."""
        from .prereqs import resolve_prerequisites
        pending_blocked = False
        for facility in ("robotics_factory", "shipyard", "research_lab", "nanite_factory"):
            target_val = self._get_planet_setting(planet, f"target_{facility}", 0)
            if planet.lvl(facility) < target_val:
                res = resolve_prerequisites("building", facility, planet.lvl(facility) + 1,
                                            planet, self.research_levels)
                if res and res[0] == "building":
                    return res[1], gd.building_cost(res[1], res[2])
                # Objetivo pendiente pero bloqueado (espera una investigación que no se
                # construye alimentando este planeta).
                pending_blocked = True
                self.log.info("Alimentación: %s objetivo %s espera investigación (%s); no alimento.",
                              planet.coords, facility, res)
        if pending_blocked:
            return None
        plasma = self.research_levels.get("plasma_tech", 0)
        return economy.next_build(planet, self.cfg, plasma=plasma,
                                  research_levels=self.research_levels)

    def _feed_deficit(self, planet):
        """Resources que le faltan al destino para pagar su próxima construcción, o None."""
        choice = self._target_next_build(planet)
        if not choice:
            return None
        name, cost = choice
        # El paso de construcción exige recursos*(1-buffer) >= coste, así que hay que
        # alimentar hasta coste/(1-buffer); si solo llegáramos al coste exacto, el destino
        # quedaría ~buffer% corto y nunca construiría (y el déficit caería a 0, en bucle).
        buf = 1 - self.cfg.keep_resources_buffer
        if buf <= 0:
            buf = 1.0
        target = Resources(cost.metal / buf, cost.crystal / buf, cost.deut / buf)
        avail = planet.resources
        need = Resources(max(0.0, target.metal - avail.metal),
                         max(0.0, target.crystal - avail.crystal),
                         max(0.0, target.deut - avail.deut))
        if need.total() <= 0:
            return None   # ya puede pagarlo (con su colchón); lo construye el paso normal
        # Redondear cada componente hacia arriba: 51k->52k, 20k->21k. Asegura siempre
        # >=feed_round_up de margen para que el destino no quede corto y construya ya.
        k = getattr(self.cfg, "feed_round_up", 1000)
        if k > 0:
            roundup = lambda x: ((int(x) + k - 1) // k + 1) * k if x > 0 else 0.0
            need = Resources(roundup(need.metal), roundup(need.crystal), roundup(need.deut))
        self.log.info("Alimentación: %s quiere %s (coste M:%d C:%d D:%d); le faltan M:%d C:%d D:%d",
                      planet.coords, name, int(cost.metal), int(cost.crystal), int(cost.deut),
                      int(need.metal), int(need.crystal), int(need.deut))
        return need

    def _feed_sendable(self, src, need) -> Resources:
        """Cuánto (Resources) podría enviar 'src' hacia 'need' en UN transporte, sin pasar
        de su excedente (keep_resources_buffer) ni de la capacidad de sus cargueros. Solo
        estima (no envía); se usa para ordenar fuentes y dentro de _feed_transport."""
        buf = 1 - self.cfg.keep_resources_buffer
        avail = Resources(src.resources.metal * buf,
                          src.resources.crystal * buf,
                          src.resources.deut * buf)
        send = Resources(min(avail.metal, need.metal),
                         min(avail.crystal, need.crystal),
                         min(avail.deut, need.deut))
        ships = fleet_mod.pick_cargo_ships(src.ships, send.total())
        if not ships:
            return Resources(0.0, 0.0, 0.0)
        # No sobrecargar: limitar a la capacidad real de los cargueros elegidos.
        cap = tgt.cargo_capacity(ships)
        total = send.total()
        if total > cap and total > 0:
            f = cap / total
            send = Resources(send.metal * f, send.crystal * f, send.deut * f)
        return send

    def _feed_transport(self, src, dst, need):
        """Envía el excedente de 'src' hacia 'dst' (lo que falte y quepa). Devuelve los
        Resources realmente enviados, o None."""
        send = self._feed_sendable(src, need)
        floor = getattr(self.cfg, "feed_min_send", 5000)
        if send.total() < floor:
            self.log.info("Alimentación: %s -> %s omitido: solo %d enviables (< feed_min_send=%d). "
                          "El destino lo cubrirá con su producción, o baja feed_min_send.",
                          src.coords, dst.coords, int(send.total()), floor)
            return None

        ships = fleet_mod.pick_cargo_ships(src.ships, send.total())
        if not ships:
            self.log.info("Alimentación: %s no tiene cargueros para alimentar a %s.",
                          src.coords, dst.coords)
            return None

        if self._guard():
            ok = self.client.send_fleet(src.coords, dst.coords, ships,
                                        mission="transport", resources=send)
            if ok:
                self.log.info("Alimentación: %s -> %s transporta M:%d C:%d D:%d (%s)",
                              src.coords, dst.coords, int(send.metal), int(send.crystal),
                              int(send.deut), ships)
                # Descontar de la caché local para no reenviar lo mismo en este ciclo.
                src.resources.metal -= send.metal
                src.resources.crystal -= send.crystal
                src.resources.deut -= send.deut
                self._deduct_ships(src, ships)
                return send
        return None

    def _fleetsave_all(self, offline_hours: float):
        if not self.cfg.enable_fleetsave:
            return
        # ponytail: ventana fija 12h.
        if getattr(self.cfg, "fleetsave_only_if_hostile", False) and \
                (time.time() - self.last_hostile_epoch) > 12 * 3600:
            self.log.info("Sin actividad hostil reciente, fleetsave omitido.")
            return
        planets = self.client.read_planets()
        # Leer lunas para tener sus datos también
        for p in planets:
            if p.has_moon and p.moon:
                self.client.read_planet_state(p.moon)

        # Construir lista de todas las ubicaciones (planetas y lunas)
        all_locations = []
        for p in planets:
            all_locations.append(p)
            if p.has_moon and p.moon:
                all_locations.append(p.moon)

        coords = [loc.coords for loc in all_locations]
        
        recall_halfway = getattr(self.cfg, "fleetsave_recall_halfway", False)
        if recall_halfway:
            target_dur = (offline_hours / 2.0) * 3600
        else:
            target_dur = offline_hours * 3600

        for loc in all_locations:
            # Rellenar estado para saber las naves actuales
            self.client.read_planet_state(loc)
            # Omitir satélites solares porque no vuelan
            flyable_ships = {k: v for k, v in loc.ships.items() if k != "solar_satellite" and v > 0}
            if not flyable_ships:
                continue

            plan = fleet_mod.fleetsave_plan(loc.coords, coords, self.cfg, offline_hours,
                                            fleet_ships=flyable_ships,
                                            research_levels=self.research_levels)
            if not plan:
                continue
            self._warn_phalanx_exposure(loc.coords, plan)
            # Fleetsave is safety-critical and must run even if hourly action rate limits are reached.
            self.rate.record()
            self.client._delay()
            self.log.info("Fleetsave %s -> %s (objetivo: %.2fh)", loc.coords, plan["destination"], target_dur / 3600)
            res = "all" if getattr(self.cfg, "fleetsave_carry_resources", True) else None
            ok = self.client.send_fleet(loc.coords, plan["destination"], {},
                                        mission=plan["mission"],
                                        resources=res,
                                        speed_percent=plan.get("speed_percent", 1.0),
                                        target_duration_s=target_dur)
            if ok:
                self._register_sent_fleetsave(loc.coords, plan["destination"])
        self._save_state()

    def _night_sweep(self):
        """
        Barrido nocturno: durante el descanso, vacía (fleetsave) los planetas/lunas
        con barrido activado para recoger la flota fabricada por la noche y los
        recursos acumulados. Solo toca las ubicaciones que el usuario active.
        """
        if not self.cfg.enable_fleetsave:
            return
        remaining_h = utils.hours_until_active(self.cfg.active_hours)
        if remaining_h <= 0.15:
            return
        planets = self.client.read_planets()
        if not planets:
            return
        all_locations = []
        for p in planets:
            all_locations.append(p)
            if p.has_moon and p.moon:
                all_locations.append(p.moon)
        coords = [loc.coords for loc in all_locations]

        swept = 0
        for loc in all_locations:
            parent = loc if loc.coords.type == "planet" else next(
                (p for p in planets if p.coords.tuple() == loc.coords.tuple()), loc)
            # Opt-in estricto por planeta (no hereda el toggle global): solo se barre
            # lo que el usuario marque explícitamente, para no hacer mucha actividad.
            pkey = f"{parent.coords.galaxy}:{parent.coords.system}:{parent.coords.position}"
            if not (getattr(self.cfg, "planets_config", {}) or {}).get(pkey, {}).get("enable_night_sweep", False):
                continue
            if not self._origin_ok(loc, "night_sweep"):
                continue
            self.client.read_planet_state(loc)
            flyable = {k: v for k, v in loc.ships.items() if k != "solar_satellite" and v > 0}
            if not flyable:
                continue
            plan = fleet_mod.fleetsave_plan(loc.coords, coords, self.cfg, remaining_h,
                                            fleet_ships=flyable,
                                            research_levels=self.research_levels)
            if not plan:
                continue
            self._warn_phalanx_exposure(loc.coords, plan)
            self.rate.record()
            self.client._delay()
            res = "all" if getattr(self.cfg, "fleetsave_carry_resources", True) else None
            self.log.info("Barrido nocturno: vaciando %s -> %s (vuelve en ~%.1fh)",
                          loc.coords, plan["destination"], remaining_h)
            ok = self.client.send_fleet(loc.coords, plan["destination"], {},
                                        mission=plan["mission"], resources=res,
                                        speed_percent=plan.get("speed_percent", 1.0),
                                        target_duration_s=remaining_h * 3600)
            if ok:
                self._register_sent_fleetsave(loc.coords, plan["destination"])
                swept += 1
        self._save_state()
        self.log.info("Barrido nocturno completado: %d ubicación(es) vaciada(s).", swept)

    def _register_sent_fleetsave(self, origin: Coords, dest: Coords):
        """Registra un fleetsave enviado para que el recall nocturno retorne SOLO estos
        despliegues (y no arrastre evasiones de ataque u otros deploys)."""
        self.sent_fleetsaves.append({
            "origin": f"{origin.galaxy}:{origin.system}:{origin.position}",
            "origin_type": origin.type,
            "dest": f"{dest.galaxy}:{dest.system}:{dest.position}",
            "dest_type": dest.type,
            "sent_at": time.time(),
        })

    def _warn_phalanx_exposure(self, origin: Coords, plan: dict):
        """Aviso por Telegram (UNO por origen y noche) cuando el plan de fleetsave queda
        expuesto a un sensor phalanx (destino planeta, o fallback a expedición real)."""
        if not plan.get("phalanx_exposed"):
            return
        if not getattr(self.cfg, "fleetsave_warn_phalanx", True):
            return
        token = getattr(self.cfg, "telegram_token", "")
        chat_id = getattr(self.cfg, "telegram_chat_id", "")
        if not token or not chat_id:
            return
        key = f"{origin.galaxy}:{origin.system}:{origin.position}:{origin.type}"
        if key in self._phalanx_warned:
            return
        self._phalanx_warned.add(key)
        dest = plan.get("destination")
        if plan.get("fallback") == "expedition":
            msg = (f"ATENCIÓN: fleetsave de {origin} sin destino seguro: la flota sale en una "
                   f"EXPEDICIÓN REAL a {dest} (riesgo de pérdidas y visible en phalanx). "
                   f"Considera crear/usar una luna.")
        else:
            msg = (f"Fleetsave de {origin} expuesto a phalanx (destino planeta {dest}). "
                   f"Considera crear/usar una luna.")
        utils.send_telegram_message(token, chat_id, utils.tg_escape(msg), logger=self.log)

    def _recall_sleep_fleetsaves(self):
        now = time.time()
        # Poda: un despliegue de fleetsave no vive más de 24h.
        self.sent_fleetsaves = [e for e in self.sent_fleetsaves
                                if now - e.get("sent_at", 0) <= 86400]
        if not self.sent_fleetsaves:
            self.log.info("Retorno nocturno: sin fleetsaves registrados que retornar.")
            return

        mvs = self.client.read_movements()
        escaped_origins = {f"{e['origin'].galaxy}:{e['origin'].system}:{e['origin'].position}"
                           for e in self.escaped_fleets}
        for mv in mvs:
            if mv.get("mission") not in ("4", "deploy") or mv.get("is_return", False):
                continue
            origin = mv.get("origin")
            dest = mv.get("destination")
            if not origin or not dest or origin == dest:
                continue
            if origin in escaped_origins:
                self.log.info("Retorno nocturno: %s -> %s omitido (es una evasión de ataque).",
                              origin, dest)
                continue
            matches = [e for e in self.sent_fleetsaves
                       if e.get("origin") == origin and e.get("dest") == dest]
            if not matches:
                continue
            # Si hay varias entradas con las mismas coords (planeta y luna comparten g:s:p),
            # preferir la que case también en tipo.
            entry = next((e for e in matches
                          if e.get("origin_type") == mv.get("origin_type")
                          and e.get("dest_type") == mv.get("dest_type")), matches[0])
            self.log.info("Retornando despliegue de fleetsave: %s -> %s", origin, dest)
            ok = self.client.recall_fleet(origin, dest, mission="deploy")
            if ok:
                self.sent_fleetsaves.remove(entry)
        self._save_state()

    # ------------------------------------------------------------------ #
    #  Comandos remotos por Telegram (/status, /fleetsave, /recall, /pausa...)
    # ------------------------------------------------------------------ #
    def _poll_telegram_commands(self):
        if not getattr(self.cfg, "enable_telegram_commands", False):
            return
        token = getattr(self.cfg, "telegram_token", "")
        chat_id = str(getattr(self.cfg, "telegram_chat_id", "") or "")
        if not token or not chat_id:
            return
        now = time.time()
        if now - self._tg_last_poll < 15:
            return
        self._tg_last_poll = now
        updates = utils.telegram_get_updates(token, self._tg_offset)
        if not updates:
            return
        # Primer arranque (offset 0): descartar el backlog de mensajes antiguos
        # para no ejecutar comandos enviados antes de encender el bot.
        skip_backlog = self._tg_offset == 0
        offset_changed = False
        for upd in updates:
            uid = int(upd.get("update_id", 0) or 0)
            if uid >= self._tg_offset:
                self._tg_offset = uid + 1
                offset_changed = True
            if skip_backlog:
                continue
            msg = upd.get("message") or upd.get("edited_message") or {}
            # Solo obedecer al chat configurado (cualquier otro chat se ignora).
            if str((msg.get("chat") or {}).get("id", "")) != chat_id:
                continue
            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                continue
            try:
                self._handle_telegram_command(text, token, chat_id)
            except Exception as e:
                self.log.debug("Error ejecutando comando de Telegram %r: %s", text, e)
        if offset_changed:
            self._save_state()

    def _handle_telegram_command(self, text: str, token: str, chat_id: str):
        parts = text.split()
        cmd = parts[0].lower().split("@")[0]  # soporta "/status@MiBot"
        args = parts[1:]

        def send(body: str):
            utils.send_telegram_message(token, chat_id, body, logger=self.log)

        if cmd == "/status":
            m = c = d = 0
            for p in self.last_planets:
                for loc in [p] + ([p.moon] if getattr(p, "moon", None) else []):
                    r = getattr(loc, "resources", None)
                    if r is not None:
                        m += int(r.metal); c += int(r.crystal); d += int(r.deut)
            fmt = lambda v: f"{v:,}".replace(",", ".")
            total_expe = self.total_expe_slots if self.total_expe_slots is not None else "?"
            lines = [
                f"Planetas: {len(self.last_planets)}",
                f"Recursos: M {fmt(m)} · C {fmt(c)} · D {fmt(d)}",
                f"Slots flota: {self.active_slots}/{self.total_fleet_slots} · Expe: {self.active_expe_slots}/{total_expe}",
                f"Monitor: {'sí' if getattr(self.cfg, 'monitor_only', False) else 'no'} · Dry-run: {'sí' if self.cfg.dry_run else 'no'}",
            ]
            if time.time() < self.paused_until:
                lines.append(f"Pausado hasta {time.strftime('%H:%M', time.localtime(self.paused_until))}")
            if self._next_cycle_eta > time.time():
                lines.append(f"Próximo ciclo ~{time.strftime('%H:%M', time.localtime(self._next_cycle_eta))}")
            send(utils.tg_escape("\n".join(lines)))
        elif cmd == "/fleetsave":
            if utils.within_active_hours(self.cfg.active_hours):
                hours = 8.0
            else:
                hours = max(1.0, utils.hours_until_active(self.cfg.active_hours))
            send(utils.tg_escape(f"Ejecutando fleetsave (~{hours:.1f}h)..."))
            self.client.login()
            self._fleetsave_all(offline_hours=hours)
            send("Fleetsave completado.")
        elif cmd == "/recall":
            self.client.login()
            n_fs_before = len(self.sent_fleetsaves)
            self._recall_sleep_fleetsaves()
            n_fs = n_fs_before - len(self.sent_fleetsaves)
            # Retornar también las evasiones de ataque (mismo mecanismo que el bloque
            # de "ataque retirado"); los despliegues hermano son permanentes y se omiten.
            n_esc = 0
            still_escaped = []
            for esc in self.escaped_fleets:
                if esc.get("is_sibling"):
                    still_escaped.append(esc)
                    continue
                o, dst = esc["origin"], esc["destination"]
                ok = self.client.recall_fleet(f"{o.galaxy}:{o.system}:{o.position}",
                                              f"{dst.galaxy}:{dst.system}:{dst.position}",
                                              mission="deploy")
                if ok:
                    n_esc += 1
                else:
                    still_escaped.append(esc)
            self.escaped_fleets = still_escaped
            self._save_state()
            send(utils.tg_escape(f"Recall: {n_fs} fleetsave(s) y {n_esc} evasión(es) retornadas."))
        elif cmd == "/pausa":
            mins = 60
            if args:
                try:
                    mins = max(1, int(args[0]))
                except ValueError:
                    pass
            self.paused_until = time.time() + mins * 60
            self._save_state()
            send(utils.tg_escape(
                f"Bot pausado {mins} min (hasta {time.strftime('%H:%M', time.localtime(self.paused_until))})."))
        elif cmd == "/reanudar":
            self.paused_until = 0.0
            self._save_state()
            send("Bot reanudado.")
        elif cmd == "/ayuda":
            send("Comandos:\n/status - estado del imperio\n/fleetsave - guardar flotas ya\n"
                 "/recall - retornar fleetsaves y evasiones\n/pausa [min] - pausar el bot (60 por defecto)\n"
                 "/reanudar - quitar la pausa\n/ayuda - esta lista")
        else:
            send(utils.tg_escape(f"Comando desconocido: {cmd}. Usa /ayuda."))

    def _last_known_ships(self, coords) -> dict:
        """Naves conocidas (del último ciclo) en una ubicación, para las alertas de
        ataque (sin navegación extra; cuando aún no hay datos devuelve {})."""
        for p in self.last_planets:
            locs = [p] + ([p.moon] if getattr(p, "moon", None) else [])
            for loc in locs:
                if loc.coords.tuple() == coords.tuple() and loc.coords.type == coords.type:
                    return getattr(loc, "ships", {}) or {}
        return {}

    def _last_known_loc(self, coords_str: str, ltype: str):
        """Ubicación propia (planeta o luna) con coords 'g:s:p' y ese tipo exacto, con los
        datos del último ciclo (last_planets). None si no se conoce."""
        for p in self.last_planets:
            locs = [p] + ([p.moon] if getattr(p, "moon", None) else [])
            for loc in locs:
                c = loc.coords
                if f"{c.galaxy}:{c.system}:{c.position}" == coords_str and c.type == ltype:
                    return loc
        return None

    def _attack_check_interval(self) -> float:
        """Segundos hasta la próxima comprobación de ataque: aleatorio en [min,max]
        para no dar un patrón fijo de sondeo que delate el bot."""
        lo = float(getattr(self.cfg, "attack_check_interval_min_s", 300))
        hi = float(getattr(self.cfg, "attack_check_interval_max_s", 780))
        if hi < lo:
            lo, hi = hi, lo
        return random.uniform(lo, hi)

    def _watch_incoming_spy(self, mvs, our_by_coords):
        """Avisa por Telegram de espionaje hostil entrante (misión 6) a coords propias.
        Un sondeo suele preceder a un ataque. Cooldown por origen para no spamear con
        las sondas de rutina de los vecinos."""
        # La marca de hostilidad (C7) se actualiza SIEMPRE que hay sondeo entrante;
        # el aviso por Telegram solo si la vigilancia está activa y el bot configurado.
        notify = bool(getattr(self.cfg, "enable_spy_watch", True)
                      and getattr(self.cfg, "telegram_token", "")
                      and getattr(self.cfg, "telegram_chat_id", ""))
        now = time.time()
        cooldown = max(0, int(getattr(self.cfg, "spy_watch_cooldown_mins", 30))) * 60
        for mv in mvs:
            if str(mv.get("mission", "")) != "6" or mv.get("is_return", False):
                continue
            dest = mv.get("destination", "")
            if dest not in our_by_coords:
                continue
            self.last_hostile_epoch = now   # sondeo entrante = actividad hostil (C7)
            if not notify:
                continue
            origin = mv.get("origin", "Desconocido")
            key = f"{origin}->{dest}"
            if key in self._spy_seen and now - self._spy_seen[key] < cooldown:
                continue
            self._spy_seen[key] = now
            self._save_state()
            # Enriquecer con el tipo real del objetivo (planeta/luna) y los últimos datos
            # conocidos de ESA ubicación exacta (recursos y naves volables).
            dest_type = mv.get("dest_type") or "planet"
            tipo = "luna" if dest_type == "moon" else "planeta"
            info_extra = ""
            loc = self._last_known_loc(dest, dest_type)
            if loc is not None:
                r = getattr(loc, "resources", None)
                if r is not None:
                    info_extra += (f"• <b>Recursos conocidos:</b> "
                                   f"M:{int(r.metal):,} C:{int(r.crystal):,} D:{int(r.deut):,}\n")
                flyable = {k: v for k, v in (getattr(loc, "ships", {}) or {}).items()
                           if k != "solar_satellite" and v > 0}
                if flyable:
                    info_extra += ("• <b>Naves conocidas:</b> "
                                   + utils.tg_escape(", ".join(f"{k}: {v}" for k, v in flyable.items()))
                                   + "\n")
                else:
                    info_extra += "• <b>Naves conocidas:</b> ninguna voladora en hangar.\n"
            msg = (
                f"🔍 <b>¡Te están sondeando en OGame!</b>\n\n"
                f"• <b>Origen:</b> [{utils.tg_escape(origin)}]\n"
                f"• <b>Destino:</b> [{utils.tg_escape(dest)}] ({tipo})\n"
                f"• <b>Llegada de las sondas:</b> {utils.tg_escape(mv.get('arrival_text', ''))}\n"
                + info_extra +
                f"\n<i>Un sondeo suele preceder a un ataque. Revisa o saca la flota.</i>"
            )
            self.log.info("Vigilancia de espionaje: sondeo entrante %s", key)
            utils.send_telegram_message(self.cfg.telegram_token, self.cfg.telegram_chat_id,
                                        msg, logger=self.log)

    def _check_and_escape_attacks(self):
        if not getattr(self.cfg, "enable_attack_escape", True):
            return
        mvs = self.client.read_movements()
        # Refrescar la pestaña "Vuelos" aprovechando esta lectura (corre más a menudo que el
        # ciclo). El event_list no trae el desglose de naves, así que lo conservamos del
        # fichero previo (que escribe el ciclo con datos completos).
        try:
            import json
            import os
            prev = []
            if os.path.exists("fleet_flights.json"):
                with open("fleet_flights.json", "r", encoding="utf-8") as f:
                    prev = json.load(f).get("flights", [])
            now_ts = time.time()
            utils.atomic_write_json("fleet_flights.json",
                                    {"flights": _retain_unlanded(build_flights(mvs, now_ts, prev=prev), prev, now_ts),
                                     "updated": now_ts})
        except Exception as e:
            self.log.debug("No se pudo refrescar fleet_flights.json: %s", e)
        planets = self.client.read_planets()
        if not planets:
            return
            
        # Construir lista de todas las ubicaciones (planetas y lunas)
        all_locations = []
        for p in planets:
            all_locations.append(p)
            if p.has_moon and p.moon:
                all_locations.append(p.moon)

        # Mapa por COORDENADAS (g:s:p) -> ubicaciones propias (planeta y/o luna).
        # Matcheamos por coordenadas, no por tipo, para no fallar si la lectura de
        # luna/planeta del evento es errónea: ante la duda, evacuamos lo que haya ahí.
        our_by_coords = {}
        for loc in all_locations:
            ck = f"{loc.coords.galaxy}:{loc.coords.system}:{loc.coords.position}"
            our_by_coords.setdefault(ck, []).append(loc)

        # Etiquetas reales de misión hostil (9 = destrucción de luna, no espionaje)
        mission_labels = {"1": "Ataque", "2": "Ataque (ACS)", "9": "Destrucción de luna"}

        # 1. Encontrar planetas/lunas bajo ataque con su tiempo de llegada mínimo.
        # under_attack (por coords, ambos tipos) decide QUÉ evacuar; attacked_exact
        # (con el dest_type REAL del movimiento) decide si el hermano está libre.
        under_attack = {}
        attacked_exact = set()
        seen_attack_keys = set()
        for mv in mvs:
            if not ((mv.get("is_hostile") or mv.get("mission") in ("1", "2", "9")) and not mv.get("is_return", False)):
                continue
            dest_coords = mv.get("destination", "")
            targets = our_by_coords.get(dest_coords, [])
            if not targets:
                continue

            arr_text = mv.get("arrival_text", "")
            # El epoch absoluto del DOM es más fiable que parsear el contador.
            arr_epoch_mv = mv.get("arrival_epoch") or 0
            if arr_epoch_mv:
                arr_sec = max(0, int(arr_epoch_mv - time.time()))
            else:
                arr_sec = parse_time_to_seconds(arr_text)
                if arr_sec is None:
                    arr_sec = 999999  # valor por defecto si no es parseable

            # Marcar bajo ataque TODAS las ubicaciones propias en esas coordenadas
            for loc in targets:
                dest_key = f"{loc.coords.galaxy}:{loc.coords.system}:{loc.coords.position}:{loc.coords.type}"
                if dest_key not in under_attack or arr_sec < under_attack[dest_key]:
                    under_attack[dest_key] = arr_sec

            # Registrar / notificar una vez por movimiento hostil
            origin_coords_str = mv.get("origin", "Desconocido")
            dest_type = mv.get("dest_type", "planet")
            # Tipo exacto atacado, según el movimiento (ante la duda, ambos tipos).
            if mv.get("dest_type"):
                attacked_exact.add(f"{dest_coords}:{mv['dest_type']}")
            else:
                attacked_exact.add(f"{dest_coords}:planet")
                attacked_exact.add(f"{dest_coords}:moon")
            mission_str = mission_labels.get(str(mv.get("mission", "")), "Ataque")
            self.record_session_action("hostile_attacks", f"{mission_str} desde {origin_coords_str}", arr_sec, dest_coords)

            # Clave estable por ataque (sin epoch: el countdown parseado deriva y duplicaba alertas)
            attack_key = f"{origin_coords_str}->{dest_coords}:{mission_str}"
            seen_attack_keys.add(attack_key)

            # Enviar notificación de Telegram si está configurado
            if getattr(self.cfg, "telegram_token", "") and getattr(self.cfg, "telegram_chat_id", ""):
                is_new_attack = attack_key not in self.telegram_notified_attacks
                # Refrescar "última vez visto" en cada barrido mientras el ataque siga visible.
                self.telegram_notified_attacks[attack_key] = time.time()
                if is_new_attack:
                    self._save_state()

                    disp = next((l for l in targets if l.coords.type == dest_type), targets[0])
                    msg = (
                        f"⚠️ <b>¡ALERTA DE ATAQUE EN OGAME!</b>\n\n"
                        f"• <b>Misión:</b> {mission_str}\n"
                        f"• <b>Origen:</b> [{origin_coords_str}]\n"
                        f"• <b>Destino:</b> [{dest_coords}] ({dest_type})\n"
                        f"• <b>Tiempo de llegada estimado:</b> {arr_text}\n"
                    )
                    ships_now = self._last_known_ships(disp.coords)
                    flyable_ships = {k: v for k, v in ships_now.items() if k != "solar_satellite" and v > 0}
                    if flyable_ships:
                        msg += "• <b>Flota en origen:</b> " + ", ".join(f"{k}: {v}" for k, v in flyable_ships.items()) + "\n"
                    else:
                        msg += "• <b>Flota en origen:</b> Ninguna nave voladora en hangar.\n"

                    msg += "\n<i>Acción del bot: El bot intentará esquivar el ataque o realizar compras de pánico según tu configuración.</i>"

                    self.log.info("Disparando alerta de Telegram para ataque: %s", attack_key)
                    utils.send_telegram_message(
                        self.cfg.telegram_token,
                        self.cfg.telegram_chat_id,
                        msg,
                        logger=self.log
                    )
                    
        # Movimiento hostil visto: registrar la marca de actividad hostil (C7, fleetsave
        # condicionado). Se persiste con el _save_state del final de esta función.
        if under_attack:
            self.last_hostile_epoch = time.time()

        # 1b. Vigilancia de espionaje entrante (misión 6): solo avisa, no evade.
        self._watch_incoming_spy(mvs, our_by_coords)

        # 2. Iniciar evasión para planetas o lunas bajo ataque que no hayamos evadido todavía
        for dest_key, arrival_seconds in under_attack.items():
            try:
                parts = dest_key.split(":")
                g = int(parts[0])
                sys = int(parts[1])
                p = int(parts[2])
                t = parts[3]
                
                # Encontrar la ubicación exacta en esta coordenada y tipo
                loc = next((l for l in all_locations if l.coords.galaxy == g and l.coords.system == sys and l.coords.position == p and l.coords.type == t), None)
                if not loc:
                    continue
                
                # Leer estado primero para conocer las naves actuales
                self.client.read_planet_state(loc)
                # Satélites solares no cuentan para evasión
                flyable_ships = {k: v for k, v in loc.ships.items() if k != "solar_satellite" and v > 0}
                if not flyable_ships:
                    continue

                # Comprobar si ya evadimos esta ubicación específica (el tipo de coordenada coincide)
                already_escaped = any(e["origin"].tuple() == loc.coords.tuple() and e["origin"].type == loc.coords.type for e in self.escaped_fleets)
                if not already_escaped:
                    self._escape_attack_loc(loc, all_locations, under_attack, attacked_exact)
                
                # Comprobar pánico (menos de 5 minutos para el impacto) - solo en planeta por simplicidad
                if arrival_seconds < 300 and t == "planet":
                    planet = next((pl for pl in planets if pl.coords.galaxy == g and pl.coords.system == sys and pl.coords.position == p), None)
                    if planet:
                        total_res_approx = planet.resources.metal + planet.resources.crystal + planet.resources.deut
                        if total_res_approx == 0 or total_res_approx >= 5000:
                            self._panic_build_resources(planet, arrival_seconds)
            except Exception as e:
                self.log.error("Error al procesar evasión/pánico de ataque en %s: %s", dest_key, e)
                
        # 3. Comprobar si los ataques a planetas evadidos han finalizado o han sido retirados
        now = time.time()
        still_escaped = []
        for esc in self.escaped_fleets:
            origin_key = f"{esc['origin'].galaxy}:{esc['origin'].system}:{esc['origin'].position}:{esc['origin'].type}"
            
            if origin_key not in under_attack:
                if esc.get("is_sibling"):
                    # Es una evasión hermano, no se le hace recall (despliegue permanente)
                    self.log.info("Ataque finalizado en %s (evasión hermano). Removiendo del registro de evasión.", origin_key)
                else:
                    elapsed = now - esc["escaped_at"]
                    if elapsed >= 300: # 5 minutos
                        dest_str = f"{esc['destination'].galaxy}:{esc['destination'].system}:{esc['destination'].position}"
                        origin_coords_str = f"{esc['origin'].galaxy}:{esc['origin'].system}:{esc['origin'].position}"
                        self.log.info("Ataque retirado o finalizado en %s. Retornando flota de evasión %s -> %s...", origin_key, origin_coords_str, dest_str)
                        ok = self.client.recall_fleet(origin_coords_str, dest_str, mission="deploy")
                        if ok:
                            self.log.info("Flota de evasión retornada con éxito.")
                        else:
                            self.log.warning("No se pudo retornar la flota de evasión (puede haber llegado ya o haber sido cancelada manualmente).")
                    else:
                        still_escaped.append(esc)
            else:
                still_escaped.append(esc)

        self.escaped_fleets = still_escaped

        # 4. Purgar del dedup de Telegram los ataques que ya no están visibles: si vuelven
        # a atacar más tarde, la alerta se dispara de nuevo. Con mvs vacío no se purga
        # (puede ser un fallo de lectura y purgar duplicaría alertas de ataques vigentes).
        if mvs:
            stale_keys = [k for k in self.telegram_notified_attacks if k not in seen_attack_keys]
            for k in stale_keys:
                del self.telegram_notified_attacks[k]
        self._save_state()

    def _escape_attack_loc(self, origin_loc: Planet, all_locations: List[Planet], under_attack: dict,
                           attacked_exact: Optional[set] = None):
        has_any_ships = any(count > 0 for count in origin_loc.ships.values())
        if not has_any_ships:
            self.log.debug("Evasión de ataque en %s omitida: no hay naves.", origin_loc.coords)
            return

        # Intentar evasión al sibling (hermano) a 100%
        sibling_loc = None
        if origin_loc.coords.type == "planet":
            if origin_loc.has_moon and origin_loc.moon:
                sibling_loc = origin_loc.moon
        elif origin_loc.coords.type == "moon":
            # Buscar el planeta hermano en all_locations
            sibling_loc = next((loc for loc in all_locations if 
                                loc.coords.galaxy == origin_loc.coords.galaxy and
                                loc.coords.system == origin_loc.coords.system and
                                loc.coords.position == origin_loc.coords.position and
                                loc.coords.type == "planet"), None)

        sibling_under_attack = False
        if sibling_loc:
            sibling_key = f"{sibling_loc.coords.galaxy}:{sibling_loc.coords.system}:{sibling_loc.coords.position}:{sibling_loc.coords.type}"
            # under_attack marca planeta Y luna en las coords atacadas (para evacuar ante la
            # duda), así que el hermano SIEMPRE parecería atacado. Para decidir si el hermano
            # es refugio válido se usa attacked_exact, construido con el dest_type REAL.
            exact = attacked_exact if attacked_exact is not None else set(under_attack.keys())
            if sibling_key in exact:
                sibling_under_attack = True

        if sibling_loc and not sibling_under_attack:
            dest = sibling_loc.coords
            speed_percent = 1.0
            is_sibling_escape = True
            self.log.warning("¡ALERTA DE ATAQUE ENMIGO detectado en %s! El hermano %s NO está bajo ataque. Iniciando evasión al 100%% de velocidad hacia %s.", origin_loc.coords, sibling_loc.coords, dest)
        else:
            # Fallback a evasión remota al 10%
            candidates = [loc.coords for loc in all_locations if loc.coords.tuple() != origin_loc.coords.tuple()]
            if not candidates:
                self.log.warning("Evasión de ataque en %s omitida: no hay planetas/lunas de destino alternativos.", origin_loc.coords)
                return
            # Seleccionar el más lejano
            dest = max(candidates, key=lambda c: gd.distance(origin_loc.coords.tuple(), c.tuple()))
            speed_percent = 0.1
            is_sibling_escape = False
            self.log.warning("¡ALERTA DE ATAQUE ENMIGO detectado en %s! Iniciando evasión al 10%% de velocidad hacia %s.", origin_loc.coords, dest)

        # Llevar recursos SIEMPRE habilitado para evasión de ataques
        res = "all"
 
        ok = self.client.send_fleet(origin_loc.coords, dest, {}, mission="deploy", resources=res, speed_percent=speed_percent)
        if ok:
            self.log.info("¡Evasión de flota desde %s enviada con éxito!", origin_loc.coords)
            self.escaped_fleets.append({
                "origin": origin_loc.coords,
                "destination": dest,
                "escaped_at": time.time(),
                "is_sibling": is_sibling_escape
            })
            # Persistir ya: un reinicio no debe perder el recall pendiente de la evasión.
            self._save_state()
        else:
            self.log.error("Fallo al enviar la flota de evasión desde %s.", origin_loc.coords)

    def _append_daily_history(self):
        """Histórico diario para las gráficas de la GUI: la primera vez que cambia la fecha
        local se appendea UNA línea JSON compacta a stats_history.jsonl con los recursos
        totales del imperio y los acumulados de sesión de ogbot_stats.json."""
        import json
        import os
        today = time.strftime("%Y-%m-%d")
        if today == self._last_history_date:
            return
        m = c = d = 0
        for p in self.last_planets:
            for loc in [p] + ([p.moon] if getattr(p, "moon", None) else []):
                r = getattr(loc, "resources", None)
                if r is not None:
                    m += int(r.metal); c += int(r.crystal); d += int(r.deut)
        stats = {}
        if os.path.exists("ogbot_stats.json"):
            try:
                with open("ogbot_stats.json", "r", encoding="utf-8") as f:
                    stats = json.load(f)
            except Exception:
                stats = {}
        farm = stats.get("total_farming") or {}
        expe = stats.get("total_expeditions") or {}
        hostiles = (stats.get("session_actions") or {}).get("hostile_attacks") or {}
        entry = {
            "ts": int(time.time()),
            "date": today,
            "metal": m, "crystal": c, "deut": d,
            "farm_metal": int(farm.get("metal", 0) or 0),
            "farm_crystal": int(farm.get("crystal", 0) or 0),
            "farm_deut": int(farm.get("deut", 0) or 0),
            "expe_metal": int(expe.get("metal", 0) or 0),
            "expe_crystal": int(expe.get("crystal", 0) or 0),
            "expe_deut": int(expe.get("deut", 0) or 0),
            "expe_dark_matter": int(expe.get("dark_matter", 0) or 0),
            "hostile_attacks": sum(len(v or []) for v in hostiles.values()),
        }
        # Ranking del jugador (C5): puntos y posición vía la API del universo, 1 vez al día.
        pid = str(getattr(self.client, "player_id", "") or self.player_id or "")
        if pid and self.api:
            try:
                if hasattr(self.api, "player_score"):
                    score = self.api.player_score(pid) or {}
                else:
                    # Respaldo si player_score aún no existe: highscore total (cat 1, type 0).
                    s = self.api.highscore(category=1, type_=0).get(pid) or {}
                    score = {"points": s.get("score", 0), "rank": s.get("rank", 0)}
                points = int(score.get("points", 0) or 0)
                rank = int(score.get("rank", 0) or 0)
                if points or rank:
                    entry["points"] = points
                    entry["rank"] = rank
                    self.update_player_score(points, rank, self.player_name)
            except Exception as e:
                self.log.debug("No se pudo leer el ranking del jugador %s: %s", pid, e)
        with open("stats_history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._last_history_date = today
        self._save_state()

    def _write_hourly_snapshot(self):
        """Snapshot horario (contrato C2): 1 línea JSON compacta por hora en
        stats_hourly.jsonl, rolling 72h (se reescribe entero podando lo viejo, con
        tmp+os.replace porque atomic_write_json no sirve para jsonl multilinea).
        actions = entradas de session_actions (ogbot_stats.json) con timestamp en la
        última hora; sus entradas llevan timestamp '%Y-%m-%d %H:%M:%S'."""
        import json
        import os
        hour = time.strftime("%Y-%m-%d %H")
        if hour == self._last_hourly:
            return
        now = int(time.time())
        m = c = d = 0
        for p in self.last_planets:
            for loc in [p] + ([p.moon] if getattr(p, "moon", None) else []):
                r = getattr(loc, "resources", None)
                if r is not None:
                    m += int(r.metal); c += int(r.crystal); d += int(r.deut)
        actions = 0
        try:
            with open("ogbot_stats.json", "r", encoding="utf-8") as f:
                sa = json.load(f).get("session_actions") or {}
            for group in sa.values():
                entries = group if isinstance(group, list) else \
                    [e for v in (group or {}).values() for e in (v or [])]
                for e in entries:
                    try:
                        ts = time.mktime(time.strptime(e.get("timestamp", ""), "%Y-%m-%d %H:%M:%S"))
                        if now - ts <= 3600:
                            actions += 1
                    except Exception:
                        continue
        except Exception:
            pass
        rows = []
        path = "stats_hourly.jsonl"
        try:
            with open(path, "r", encoding="utf-8") as f:
                for ln in f:
                    try:
                        e = json.loads(ln)
                        if now - int(e.get("ts", 0)) <= 72 * 3600:
                            rows.append(e)
                    except Exception:
                        continue
        except Exception:
            pass
        rows.append({"ts": now, "metal": m, "crystal": c, "deut": d, "actions": actions})
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in rows:
                f.write(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp, path)
        self._last_hourly = hour
        self._save_state()

    def _publish_agenda(self):
        """Publica task_agenda.json (contrato C3): próximas rondas de módulos,
        construcciones/investigación en curso, retornos de vuelos propios, fleetsave
        nocturno y pausa nocturna. Tolerante: cualquier dato ausente se omite."""
        import json
        now = time.time()
        tasks = []

        def add(when, kind, title, detail="", status="programado", loc=None):
            try:
                tasks.append({"when": int(when), "kind": kind, "title": title,
                              "detail": detail, "status": status, "loc": loc})
            except Exception:
                pass

        # 1) Próxima ronda de cada módulo habilitado (intervalo efectivo; 0 = cada ciclo).
        next_cycle = self._next_cycle_eta if self._next_cycle_eta > now else now
        modules = [
            ("economia", "Ronda de economía", getattr(self.cfg, "enable_economy", True),
             self.last_economy_run_time, getattr(self.cfg, "economy_run_interval_mins", 0)),
            ("reciclaje", "Ronda de reciclaje", getattr(self.cfg, "enable_recycling", True),
             self.last_recycling_run_time, getattr(self.cfg, "recycling_run_interval_mins", 0)),
            ("expedicion", "Ronda de expediciones", getattr(self.cfg, "enable_expeditions", True),
             self.last_expeditions_run_time, getattr(self.cfg, "expeditions_run_interval_mins", 0)),
            ("farming", "Ronda de farmeo", getattr(self.cfg, "enable_farming", True),
             self.last_farming_run_time, getattr(self.cfg, "farming_run_interval_mins", 0)),
        ]
        for kind, title, enabled, last_run, interval in modules:
            if not enabled:
                continue
            try:
                due = float(last_run or 0.0) + max(0, int(interval or 0)) * 60
            except (TypeError, ValueError):
                continue
            add(max(due, next_cycle), kind, title,
                f"intervalo {int(interval)} min" if interval else "cada ciclo",
                "pendiente" if due <= now else "programado")

        # 2) Construcciones/investigación en curso (de la caché de estado).
        for key, entry in (self.state_cache.get("planets") or {}).items():
            finish = (entry or {}).get("build_finish_epoch", 0.0) or 0.0
            if finish > now:
                loc = ":".join(key.split(":")[:3])
                q = entry.get("build_queue") or []
                add(finish, "construir", f"Construcción en {loc}",
                    str(q[0]) if q else "", "en_curso", loc)
        r = self.state_cache.get("research") or {}
        if (r.get("finish_epoch") or 0.0) > now:
            add(r["finish_epoch"], "investigacion", "Investigación en curso",
                str(r.get("tech", "") or ""), "en_curso")

        # 3) Retornos de vuelos propios (fleet_flights.json del ciclo).
        kind_by_mission = {"15": "expedicion", "1": "farming", "2": "farming",
                           "8": "reciclaje", "6": "espionaje", "3": "transporte",
                           "4": "fleetsave"}
        try:
            with open("fleet_flights.json", "r", encoding="utf-8") as f:
                flights = json.load(f).get("flights", []) or []
        except Exception:
            flights = []
        for fl in flights:
            try:
                back = int(fl.get("return_arrival_epoch") or 0) or int(fl.get("arrival_epoch") or 0)
            except (TypeError, ValueError):
                continue
            if back <= now:
                continue
            add(back, kind_by_mission.get(str(fl.get("mission_code", "")), "transporte"),
                f"Retorno de {fl.get('mission', 'vuelo')}",
                f"{fl.get('origin', '?')} -> {fl.get('destination', '?')}",
                "en_curso", fl.get("origin") or None)

        # 4) Fleetsave nocturno estimado y pausa nocturna (según active_hours).
        try:
            if utils.within_active_hours(self.cfg.active_hours):
                rest_s = utils.seconds_until_inactive(self.cfg.active_hours)
                if 0 < rest_s < 86400:
                    if getattr(self.cfg, "enable_fleetsave", True):
                        add(now + rest_s, "fleetsave", "Fleetsave nocturno",
                            "al inicio del descanso", "programado")
                    add(now + rest_s, "sistema", "Pausa nocturna",
                        "fin de la franja activa", "programado")
            else:
                wake_h = utils.hours_until_active(self.cfg.active_hours)
                add(now + wake_h * 3600, "sistema", "Pausa nocturna",
                    "descansando hasta la franja activa", "en_curso")
        except Exception:
            pass

        tasks.sort(key=lambda t: t["when"])
        utils.atomic_write_json("task_agenda.json",
                                {"generated_at": int(now), "tasks": tasks})

    def _calculate_panic_build(self, planet: Planet) -> Dict[str, int]:
        """
        Calcula la combinación óptima de defensa y flota para gastar la mayor cantidad de recursos.
        Usa una ponderación dinámica según los recursos restantes.
        """
        from ogbot import gamedata as gd
        from ogbot.economy import check_defense_prereqs
        from ogbot.prereqs import resolve_prerequisites

        # Lista de candidatos a evaluar
        defenses_candidates = ["rocket_launcher", "light_laser", "heavy_laser", "gauss_cannon", "ion_cannon", "plasma_turret"]
        ships_candidates = ["small_cargo", "large_cargo", "light_fighter", "heavy_fighter", "cruiser", "battleship", "battlecruiser", "bomber", "destroyer", "recycler", "espionage_probe", "reaper", "pathfinder"]

        buildable_items = []  # Lista de tuples (nombre, tipo, Cost)

        # 1. Filtrar defensas buildables
        for d_name in defenses_candidates:
            if check_defense_prereqs(d_name, planet, self.research_levels):
                unit_data = gd.DEFENSES.get(d_name)
                if unit_data:
                    buildable_items.append((d_name, "defense", unit_data.cost))

        # 2. Filtrar naves buildables
        for s_name in ships_candidates:
            res = resolve_prerequisites("ship", s_name, 1, planet, self.research_levels)
            if res == ("ship", s_name, 1):
                unit_data = gd.SHIPS.get(s_name)
                if unit_data:
                    buildable_items.append((s_name, "ship", unit_data.cost))

        if not buildable_items:
            return {}

        # Recursos disponibles actuales
        r_metal = planet.resources.metal
        r_crystal = planet.resources.crystal
        r_deut = planet.resources.deut

        plan = {}

        # Algoritmo codicioso (greedy) con pesos dinámicos
        while True:
            best_item = None
            best_score = -1.0

            # Los pesos de cada recurso son proporcionales a lo que nos queda
            w_metal = max(0.0, r_metal)
            w_crystal = max(0.0, r_crystal)
            w_deut = max(0.0, r_deut)

            # Si nos quedan poquísimos recursos, parar
            if w_metal + w_crystal + w_deut < 1000:
                break

            for name, item_type, cost in buildable_items:
                # Verificar si nos lo podemos permitir
                if cost.metal <= r_metal and cost.crystal <= r_crystal and cost.deut <= r_deut:
                    # Puntuación basada en consumir los recursos que más abundan
                    score = (cost.metal * w_metal) + (cost.crystal * w_crystal) + (cost.deut * w_deut)
                    if score > best_score:
                        best_score = score
                        best_item = (name, item_type, cost)

            if not best_item:
                # No nos podemos permitir ninguna unidad más
                break

            name, item_type, cost = best_item
            plan[name] = plan.get(name, 0) + 1
            r_metal -= cost.metal
            r_crystal -= cost.crystal
            r_deut -= cost.deut

        return plan

    def _panic_build_resources(self, planet: Planet, seconds_remaining: float):
        """
        Navega, lee recursos actuales e intenta comprar naves/defensas
        para vaciar los almacenes ante un ataque inminente.
        """
        self.log.warning("[PANIC BUILD] ¡Impacto de ataque inminente en %s en %.1f min! Ejecutando compra de pánico...",
                             planet.coords, seconds_remaining / 60)
        from ogbot import gamedata as gd

        # 1. Asegurar que estamos en el planeta y recargar recursos reales
        try:
            self.client._goto("overview", planet)
            planet.resources = self.client.read_resources()
        except Exception as e:
            self.log.error("[PANIC BUILD] Error al ir a Overview y recargar recursos en %s: %s", planet.coords, e)
            return

        # 2. Calcular qué construir para consumir el máximo
        plan = self._calculate_panic_build(planet)
        if not plan:
            self.log.info("[PANIC BUILD] No hay ninguna nave o defensa asequible/desbloqueada para comprar en %s.", planet.coords)
            return

        self.log.warning("[PANIC BUILD] Plan de compra calculado para %s: %s", planet.coords, plan)

        # 3. Agrupar compras por tipo de página para optimizar navegación
        ships_to_build = {}
        defenses_to_build = {}
        for name, qty in plan.items():
            if qty <= 0:
                continue
            if name in gd.SHIPS:
                ships_to_build[name] = qty
            elif name in gd.DEFENSES:
                defenses_to_build[name] = qty

        # 4. Ejecutar compras de naves
        if ships_to_build:
            try:
                for name, qty in ships_to_build.items():
                    ok = self.client.build_ships(planet, name, qty)
                    if ok:
                        self.record_session_action("fleet", name, qty, str(planet.coords))
            except Exception as e:
                self.log.error("[PANIC BUILD] Error construyendo naves de pánico en %s: %s", planet.coords, e)

        # 5. Ejecutar compras de defensas
        if defenses_to_build:
            try:
                for name, qty in defenses_to_build.items():
                    ok = self.client.build_defense(planet, name, qty)
                    if ok:
                        self.record_session_action("defense", name, qty, str(planet.coords))
            except Exception as e:
                self.log.error("[PANIC BUILD] Error construyendo defensas de pánico en %s: %s", planet.coords, e)

        self.log.info("[PANIC BUILD] Proceso de compra de pánico completado en %s.", planet.coords)

def parse_time_to_seconds(time_str: str) -> Optional[int]:
    if not time_str:
        return None
    time_str = time_str.strip().lower()
    import re
    # Formato HH:MM:SS
    m_hms = re.match(r'^(\d+):(\d+):(\d+)$', time_str)
    if m_hms:
        h, m, s = map(int, m_hms.groups())
        return h * 3600 + m * 60 + s
    
    # Formato Xd Yh Zm Ws / Xh Ym Zs / Ym Zs / Zs / etc.
    parts = re.findall(r'(\d+)\s*([dhms])', time_str)
    if parts:
        total_seconds = 0
        for val, unit in parts:
            if unit == 'd':
                total_seconds += int(val) * 86400
            elif unit == 'h':
                total_seconds += int(val) * 3600
            elif unit == 'm':
                total_seconds += int(val) * 60
            elif unit == 's':
                total_seconds += int(val)
        return total_seconds
    
    # Formato de solo dígitos o "X s"
    m_s = re.match(r'^(\d+)\s*s?$', time_str)
    if m_s:
        return int(m_s.group(1))
    return None
