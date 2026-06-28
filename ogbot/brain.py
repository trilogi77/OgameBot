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
from . import combat
from .models import Coords, Resources, Planet
from . import utils

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


def _flight_sig(f):
    # Incluye un bucket de llegada (minuto) para no confundir dos flotas distintas en la
    # MISMA ruta/misión (p.ej. dos expediciones a la misma posición). El epoch apenas
    # deriva entre la escritura del ciclo y la del chequeo de ataques, así que el bucket
    # casa el mismo vuelo físico; si justo cruza un minuto, las naves se mostrarán vacías
    # (mejor que mostrar las de otra flota).
    return (str(f.get("mission_code", "")), f.get("origin", ""),
            f.get("destination", ""), bool(f.get("is_return")),
            int(f.get("arrival_epoch", 0)) // 60)


def build_flights(mvs, now, prev=None):
    """Convierte movimientos crudos de read_movements() en vuelos para la GUI.

    Excluye flotas hostiles entrantes (los ataques los gestiona el escape/Telegram): así
    las dos fuentes —la página de movimientos del ciclo y el event_list del chequeo de
    ataques— producen el MISMO conjunto (solo tus flotas) y no parpadean filas.

    prev: vuelos del fichero anterior. Si la fuente actual no trae naves/carga (el
    event_list no las trae), se conservan del vuelo previo equivalente (escrito por el
    ciclo con datos completos).
    """
    prev_by_sig = {}
    for pf in (prev or []):
        prev_by_sig.setdefault(_flight_sig(pf), pf)
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
            "ships": ships,
            "cargo": cargo,
        }
        if not ships and not any(cargo.values()):
            pf = prev_by_sig.get(_flight_sig(f))
            if pf:
                f["ships"] = pf.get("ships", {}) or {}
                f["cargo"] = pf.get("cargo", {}) or {"metal": 0, "crystal": 0, "deut": 0}
        flights.append(f)
    return flights


class Brain:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = utils.setup_logger("ogbot", cfg.log_file, cfg.log_level)
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
        self.total_expe_slots = 0
        self.escaped_fleets: List[dict] = []
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
        self.next_build_event = 0.0       # despertar para encolar la siguiente construcción
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
            with open(self.cfg.state_file, "w", encoding="utf-8") as f:
                json.dump({
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
                }, f, indent=2)
        except Exception as e:
            self.log.debug("No se pudo guardar state.json: %s", e)

    # ------------------------------------------------------------------ #
    #  Memoria de estado: caché de niveles (edificios/investigación/defensas)
    # ------------------------------------------------------------------ #
    STATE_CACHE_FILE = "game_state_cache.json"

    def _loc_key(self, coords) -> str:
        return f"{coords.galaxy}:{coords.system}:{coords.position}:{coords.type}"

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
        import json
        try:
            with open(self.STATE_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.state_cache, f, indent=2)
        except Exception as e:
            self.log.debug("No se pudo guardar game_state_cache.json: %s", e)

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
        if finished and time.time() >= finished and not loc.building_in_progress:
            self.log.info("Estado %s: construcción terminada -> refresco niveles de edificios.", loc.coords)
            self._refresh_buildings(loc)
            self._cache_store_location(loc)
        else:
            entry["build_queue"] = list(loc.building_queue)
            entry["build_finish_epoch"] = (time.time() + loc.building_remaining_seconds) if loc.building_in_progress else 0.0
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
            with open("build_status.json", "w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
        except Exception as e:
            self.log.debug("No se pudo guardar build_status.json: %s", e)

    def _get_planet_setting(self, planet, setting_name: str, default_val):
        coords_str = f"{planet.coords.galaxy}:{planet.coords.system}:{planet.coords.position}"
        planets_config = getattr(self.cfg, "planets_config", {}) or {}
        p_cfg = planets_config.get(coords_str, {})
        return p_cfg.get(setting_name, getattr(self.cfg, setting_name, default_val))

    # ------------------------------------------------------------------ #
    def run_forever(self):
        self.log.info("=== OGBot iniciado (dry_run=%s) ===", self.cfg.dry_run)
        self.client.start()
        if not self.client.login():
            self.log.error("Login fallido. Abortando.")
            self.client.stop()
            return
        self.initialize_session_stats()
        try:
            while self.running:
                # Comprobación de ataque prioritaria e incondicional (antes de evaluar franja horaria)
                if getattr(self.cfg, "enable_attack_escape", True):
                    try:
                        self.log.info("Comprobación prioritaria de ataques hostiles...")
                        self._check_and_escape_attacks()
                    except Exception as e:
                        self.log.debug("Error en comprobación prioritaria de ataques: %s", e)

                if utils.within_active_hours(self.cfg.active_hours):
                    try:
                        self.cycle()
                        self._continue_until_idle()
                    except Exception as e:
                        self.log.exception("Error en ciclo: %s", e)

                    sleep_s = utils.jitter(random.uniform(self.cfg.cycle_interval_min_s,
                                                           self.cfg.cycle_interval_max_s))
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
                                    
                        time.sleep(5)
                else:
                    hours_to_sleep = utils.hours_until_active(self.cfg.active_hours)
                    self.log.info("Fuera de franja horaria. Modo descanso activado por %.2f horas.", hours_to_sleep)
                    
                    # Realizar fleetsave una sola vez para cubrir todo el periodo de inactividad
                    self._fleetsave_all(offline_hours=hours_to_sleep)
                    
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
                        if night_sweep and (time.time() - last_sweep) >= sweep_interval_s:
                            last_sweep = time.time()
                            self.log.info("Barrido nocturno: despertando para vaciar planetas...")
                            try:
                                if self.client.login():
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

    def _has_free_expe_slots(self) -> bool:
        total = self.total_expe_slots if self.total_expe_slots > 0 else 1
        return self.active_expe_slots < total

    def _has_free_slots_for_mission(self) -> bool:
        total = self.total_fleet_slots if self.total_fleet_slots else (
            self.research_levels.get("computer_tech", 0) + 1 if self.research_levels else 1)
        free = total - self.active_slots
        return free >= 1

    def _has_free_slots_for_espionage(self) -> bool:
        total = self.total_fleet_slots if self.total_fleet_slots else (
            self.research_levels.get("computer_tech", 0) + 1 if self.research_levels else 1)
        free = total - self.active_slots
        return free >= 1

    def _has_ships(self, planets, ship_type: str, min_count: int = 1) -> bool:
        return sum(p.ships.get(ship_type, 0) for p in planets) >= min_count

    def cycle(self):
        self.log.info("--- Nuevo ciclo ---")
        try:
            # Recargar configuración del disco para capturar cambios desde la GUI sin reiniciar el bot
            path = getattr(self.cfg, "_path", "config.yaml")
            new_cfg = Config.load(path)
            for k, v in new_cfg.__dict__.items():
                setattr(self.cfg, k, v)
            self.log.info("Configuración recargada desde el disco.")
        except Exception as e:
            self.log.warning("No se pudo recargar la configuración desde el disco: %s", e)

        # Correcciones de nivel / re-lectura forzada pedidas desde la GUI.
        self._apply_pending_gui_requests()

        planets = self.client.read_planets()
        if not planets:
            self.log.warning("Sin planetas legibles; revisa selectores.")
            return

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
            self.total_expe_slots = slot_info.get("expe_total", 0)
            self.active_expe_slots = slot_info.get("expe_used", 0)
            self.log.info("Slots reales del juego: Flotas %d/%d, Expediciones %d/%d",
                          self.active_slots, self.total_fleet_slots,
                          self.active_expe_slots, self.total_expe_slots)
        else:
            self.active_slots = self._count_our_active_fleets(mvs, planets)
            self.active_expe_slots = self._count_our_active_expeditions(mvs)
            self.total_expe_slots = int(self.research_levels.get("astrophysics", 0) ** 0.5)
            self.log.info("Slots de flota (fallback movimientos): %d activos, Expediciones %d/%d",
                          self.active_slots, self.active_expe_slots, self.total_expe_slots)

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
            with open("planets_cache.json", "w", encoding="utf-8") as f:
                json.dump(planets_data, f, indent=2)
            with open("fleet_in_motion.json", "w", encoding="utf-8") as f:
                json.dump(ships_in_motion, f, indent=2)
            # Lista de vuelos para la pestaña "Vuelos" (datos completos: naves y carga).
            with open("fleet_flights.json", "w", encoding="utf-8") as f:
                json.dump({"flights": build_flights(mvs, time.time()),
                           "updated": time.time()}, f, ensure_ascii=False, indent=2)
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

        # 2. Reciclaje (timer independiente)
        if run_recycling and self.cfg.enable_recycling:
            if self._has_ships(all_locations, "recycler"):
                self._recycle(all_locations)
            else:
                self.log.debug("Reciclaje omitido: sin recicladores.")
            self.last_recycling_run_time = time.time()
            self._save_state()

        # 3. Expediciones (timer independiente)
        if run_expeditions and self.cfg.enable_expeditions:
            self._run_expeditions_round(planets, all_locations)
            self.last_expeditions_run_time = time.time()
            self._update_next_expedition_event()
            self._save_state()

        # 7-8. Farmeo (ataques a inactivos) + colonización + lunas (timer independiente)
        if run_farming:
            self.log.info("Iniciando ronda de farmeo (ataques a inactivos)...")
            if self.cfg.enable_farming and self.api:
                if self._has_ships(all_locations, "espionage_probe"):
                    self._farm(all_locations)
                else:
                    self.log.info("Farmeo omitido: sin sondas de espionaje.")

            # 9. Colonización
            if self.cfg.enable_colonization:
                self._colonize(planets)

            # 10. Lunas
            if self.cfg.enable_moon_creation:
                self._moonshot(planets)

            self.last_farming_run_time = time.time()
            self._save_state()

        # Ejecutar ronda de Economía/Construcción si corresponde
        if run_economy:
            self.log.info("Iniciando ronda de economía/construcción...")
            # 4-7. Economía / defensa / formas de vida / instalaciones por planeta (solo planetas principales)
            for p in planets:
                self._economy_step(p)
                self._defense_step(p)
                self._lifeforms_step(p)
                self._facilities_step(p)
            self._feed_step(planets, mvs)   # alimentar planetas-objetivo desde fuentes marcadas
            self._research_step(planets)
            self._fleet_step(planets)

            self.last_economy_run_time = time.time()
            self._save_state()

        # Mantener armado el despertar por vuelta de expedición aunque la ronda no se haya
        # ejecutado este ciclo (p.ej. bloqueada por su intervalo), para no perder reenvíos.
        if self.cfg.enable_expeditions:
            self._update_next_expedition_event()

        # Publicar tiempos restantes de construcciones/investigación para la GUI
        self._write_build_status(planets)

        # 11. Actualizar estadísticas imperiales
        self.update_imperial_stats()

        self.last_planets = planets

    # ------------------------------------------------------------------ #
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

    def _fleet_step(self, planets):
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

        # Si hay objetivos de flota definidos, los procesamos
        fleet_targets = getattr(self.cfg, "fleet_targets", {}) or {}
        if fleet_targets:
            for ship_name, target_qty in fleet_targets.items():
                if target_qty <= 0:
                    continue
                # Contamos las naves totales en todo el imperio
                current_qty = sum(p.ships.get(ship_name, 0) for p in planets)
                if current_qty < target_qty:
                    needed = target_qty - current_qty
                    # Fabricar en lotes de máximo 10 para no drenar recursos
                    batch = min(needed, 10)
                    if self._guard():
                        self.log.info("Fabricando %d %s (objetivo: %d, actual imperio: %d)", 
                                       batch, ship_name, target_qty, current_qty)
                        ok = self.client.build_ships(home, ship_name, batch)
                        if ok:
                            self.record_session_action("fleet", ship_name, batch, str(home.coords))
                        # Actualizar inventario local para no duplicar en el mismo ciclo
                        home.ships[ship_name] = home.ships.get(ship_name, 0) + batch
                        break # Un solo lote de construcción por ciclo para balancear recursos
        else:
            # Fallback al comportamiento original (cargueros grandes para farmear)
            if not self.cfg.enable_farming:
                return
            if home.lvl("metal_mine") < 4:
                return
            have_lc = home.ships.get("large_cargo", 0)
            if have_lc < 20 and self._guard():
                ok = self.client.build_ships(home, "large_cargo", 10)
                if ok:
                    self.record_session_action("fleet", "large_cargo", 10, str(home.coords))

    def _farm(self, locations):
        # Filtrar ubicaciones origen elegibles para farmeo (planetas y lunas)
        eligible_locations = [p for p in locations if self._get_planet_setting(p, "enable_farming", True)]
        if not eligible_locations:
            self.log.info("Farmeo omitido: no hay ningún planeta o luna con farmeo activado.")
            return

        # 1. Template de flota de ataque
        template = {k: v for k, v in
                    (self.cfg.attacker_fleet_template or {}).items() if v > 0}
        if not template:
            template = {"large_cargo": 5}

        # Comprobar si tenemos al menos una naves de estos tipos en algún origen elegible
        has_farming_fleet = any(self._has_ships(eligible_locations, ship_type, min_count=1) for ship_type in template.keys())
        if not has_farming_fleet:
            self.log.info("Farmeo omitido: sin flota de ataque/cargueros configurados en %s en ubicaciones con farmeo activo.", list(template.keys()))
            return

        self.log.info("Buscando objetivos (API)...")
        candidates = self.api.inactive_targets()

        # Filtrar candidatos por cooldown de ataques
        cooldown_s = getattr(self.cfg, "farming_attack_cooldown_hours", 2.0) * 3600
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
            filtered_candidates.append(cand)

        origins = [p.coords for p in eligible_locations]
        pre = tgt.select_targets(filtered_candidates, origins, self.cfg.max_target_distance_systems)
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
                          if m.get("mission") in ("9", "espionage", "Espionage")]
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

            # Calcular botín completo (sin limitar por la capacidad inicial del template)
            full_loot = tgt.estimate_loot(report.resources, 10**9, self.cfg.loot_percent)

            # Buscar el mejor origen para este objetivo
            best_atk_for_target = None
            reasons_by_planet = {}
            for p in eligible_locations:
                # 1. Dimensionar la flota específicamente para los cargueros disponibles en este origen
                atk_fleet = fleet_mod.size_attack_fleet_for_planet(p, full_loot, template)
                
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
                    alt_fleet = fleet_mod.size_attack_fleet_for_planet(p, full_loot, template)
                    if not can_afford_fleet(p, alt_fleet):
                        continue
                    if tgt.cargo_capacity(alt_fleet) == 0:
                        continue
                    
                    alt_target = tgt.evaluate(target.report, p.coords, alt_fleet, self.my_tech, self.cfg)
                    if not alt_target:
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
                # Actualizar el inventario local de naves del origen
                for ship, qty in fleet_to_send.items():
                    if ship in origin.ships:
                        origin.ships[ship] = max(0, origin.ships[ship] - qty)
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
                self._save_state()

        self.log.info("Farmeo completado: %d ataques en este ciclo.", attacked)

    def _recycle(self, locations):
        locations_with_recyclers = [p for p in locations 
                                    if p.ships.get("recycler", 0) >= 1 
                                    and self._get_planet_setting(p, "enable_recycling", True)]
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
                origin_loc.ships["recycler"] = max(0, origin_loc.ships["recycler"] - n)
                self.active_slots += 1

    def _expeditions(self, home: Coords, ships: Optional[dict] = None,
                     target_system: Optional[int] = None) -> bool:
        if not self._has_free_slots_for_mission():
            self.log.info("Expedición: deteniendo envío por falta de slots de flota libres.")
            return False
        if not self._has_free_expe_slots():
            self.log.info("Expedición: deteniendo envío por falta de slots de expedición libres (%d/%d).",
                          self.active_expe_slots, self.total_expe_slots)
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
                        self.expedition_flight_cal = ratio
                        self.log.info("Calibración de vuelo de expedición: real=%.0fs estimado=%.0fs -> factor %.2f",
                                      real_oneway, ftime, ratio)
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
                        if self._get_planet_setting(parent_of(loc), "enable_expeditions", True)]

        top1 = max_find = optimal = per_exp_auto = 0
        optimal_nopf = optimal_pf = 0
        spread_cap = None  # None = cada expedición a su óptimo; int = reparto por slot
        if auto:
            top1 = self._expedition_top1_points()
            safety = float(getattr(self.cfg, "expedition_find_safety", 1.0))
            discoverer = bool(getattr(self.cfg, "expedition_discoverer_class", False))
            # Dimensionado distinto con/sin pathfinder: solo se dobla el botín en las
            # expediciones que de verdad llevan un pathfinder (no en todas).
            base_find = gd.expedition_max_find_units(top1, self.cfg.universe_speed, discoverer, False)
            optimal_nopf = fleet_mod.optimal_expedition_cargo(base_find, cargo_ship, safety)
            if use_pf:
                pf_find = gd.expedition_max_find_units(top1, self.cfg.universe_speed, discoverer, True)
                optimal_pf = fleet_mod.optimal_expedition_cargo(pf_find, cargo_ship, safety)
            else:
                pf_find = base_find
                optimal_pf = optimal_nopf
            max_cargo = int(getattr(self.cfg, "expedition_max_cargo", 0) or 0)
            if max_cargo > 0:
                optimal_nopf = min(optimal_nopf, max_cargo)
                optimal_pf = min(optimal_pf, max_cargo)
            free_slots = max(1, (self.total_expe_slots or 1) - self.active_expe_slots)
            total_avail = sum(loc.ships.get(cargo_ship, 0) for loc in enabled_locs)
            # ¿Hay NGC de sobra para llenar todos los slots al óptimo? Si no, repartir
            # las disponibles entre todos los slots para maximizar el nº de expediciones.
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
                                                 spread_cap, use_pf, loc.ships, min_cargo)
                else:
                    ships = dict(manual_ships)
                    if not all(loc.ships.get(s, 0) >= q for s, q in ships.items()):
                        ships = None
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
                    for s, q in ships.items():
                        loc.ships[s] = max(0, loc.ships.get(s, 0) - q)
                else:
                    break

        self._write_expedition_status(top1, max_find, optimal, per_exp_auto, cargo_ship, auto)

    def _auto_exp_ships(self, cargo_ship, optimal_nopf, optimal_pf, spread_cap,
                        use_pf, avail, min_cargo):
        """
        Dict de naves para una expedición auto, limitado por el hangar. Decide PRIMERO
        si esta expedición lleva pathfinder (solo si lo hay) y dimensiona la carga al
        óptimo correspondiente (x2 con pathfinder, x1 sin él), sin pasar del reparto.
        """
        will_pf = use_pf and cargo_ship != "pathfinder" and avail.get("pathfinder", 0) >= 1
        loc_optimal = optimal_pf if will_pf else optimal_nopf
        target = loc_optimal if spread_cap is None else min(loc_optimal, spread_cap)
        n = min(target, avail.get(cargo_ship, 0))
        if n < min_cargo:
            return None
        ships = {cargo_ship: n}
        if will_pf:
            ships["pathfinder"] = 1
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
                "total_expe_slots": self.total_expe_slots,
                "next_event_epoch": self.next_expedition_event,
                "returns_epochs": returns,
                "updated_at": now,
            }
            with open("expedition_status.json", "w", encoding="utf-8") as f:
                json.dump(status, f, indent=2)
        except Exception as e:
            self.log.debug("No se pudo guardar expedition_status.json: %s", e)

    def _colonize(self, planets):
        occupied = {p.coords.tuple() for p in planets}
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
                planet.building_in_progress = True
                dur = gd.building_time(cost, planet.lvl("robotics_factory"),
                                       planet.lvl("nanite_factory"), self.cfg.universe_speed)
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
        marcan a mano en la pestaña 'Por Planeta'."""
        destinos = [p for p in planets if self._get_planet_setting(p, "feed_target", False)]
        fuentes = [p for p in planets if self._get_planet_setting(p, "feed_source", False)]
        if not destinos or not fuentes:
            return

        # Destinos que YA tienen un TRANSPORTE entrante (la alimentación siempre usa
        # mission=transport): no reenviamos hasta que llegue, para no mandar de más.
        # OJO: solo transporte, NO deploy — los fleetsave son deploy y darían falsos
        # positivos que dejarían al destino sin alimentar.
        inbound = {}
        for m in (movements or []):
            if m.get("is_hostile") or m.get("is_return"):
                continue
            mission = str(m.get("mission", "")).lower()
            if not (mission == "3" or "transport" in mission):
                continue
            d = m.get("destination", "").replace("[", "").replace("]", "").strip()
            o = m.get("origin", "").replace("[", "").replace("]", "").strip()
            if d and d != o:
                inbound.setdefault(d, m)

        for dst in destinos:
            # Si ya está construyendo algo, lo que pediría está en cola y pagado: no
            # mandar recursos para una subida en curso (evita mandar de más).
            if getattr(dst, "building_in_progress", False):
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
            # Fuente más cercana primero: menos deut de vuelo y llega antes.
            srcs = sorted(fuentes, key=lambda s: gd.distance(s.coords.tuple(), dst.coords.tuple()))
            for src in srcs:
                if src.coords.tuple() == dst.coords.tuple():
                    continue
                if not self._has_free_slots_for_mission():
                    self.log.info("Alimentación: sin slots de flota libres; sigo el próximo ciclo.")
                    return
                sent = self._feed_transport(src, dst, need)
                if sent:
                    self.active_slots += 1   # el transporte ocupa un slot hasta que vuelve
                    need = Resources(max(0.0, need.metal - sent.metal),
                                     max(0.0, need.crystal - sent.crystal),
                                     max(0.0, need.deut - sent.deut))
                if need.total() <= 0:
                    break

    def _target_next_build(self, planet):
        """(nombre, coste) de lo próximo que el planeta-destino quiere construir, o None.
        Prioriza los objetivos de instalaciones (lab, astillero...) y, si no hay,
        usa la siguiente construcción que pediría la economía (minas/energía)."""
        from .prereqs import resolve_prerequisites
        for facility in ("robotics_factory", "shipyard", "research_lab", "nanite_factory"):
            target_val = self._get_planet_setting(planet, f"target_{facility}", 0)
            if planet.lvl(facility) < target_val:
                res = resolve_prerequisites("building", facility, planet.lvl(facility) + 1,
                                            planet, self.research_levels)
                if res and res[0] == "building":
                    return res[1], gd.building_cost(res[1], res[2])
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
        self.log.info("Alimentación: %s quiere %s (coste M:%d C:%d D:%d); le faltan M:%d C:%d D:%d",
                      planet.coords, name, int(cost.metal), int(cost.crystal), int(cost.deut),
                      int(need.metal), int(need.crystal), int(need.deut))
        return need

    def _feed_transport(self, src, dst, need):
        """Envía el excedente de 'src' hacia 'dst' (lo que falte y quepa). Devuelve los
        Resources realmente enviados, o None."""
        buf = 1 - self.cfg.keep_resources_buffer
        avail = Resources(src.resources.metal * buf,
                          src.resources.crystal * buf,
                          src.resources.deut * buf)
        send = Resources(min(avail.metal, need.metal),
                         min(avail.crystal, need.crystal),
                         min(avail.deut, need.deut))
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

        # No sobrecargar: limitar lo enviado a la capacidad real de los cargueros elegidos.
        cap = tgt.cargo_capacity(ships)
        total = send.total()
        if total > cap and total > 0:
            f = cap / total
            send = Resources(send.metal * f, send.crystal * f, send.deut * f)

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
                return send
        return None

    def _fleetsave_all(self, offline_hours: float):
        if not self.cfg.enable_fleetsave:
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

            plan = fleet_mod.fleetsave_plan(loc.coords, coords, self.cfg, offline_hours)
            if not plan:
                continue
            # Fleetsave is safety-critical and must run even if hourly action rate limits are reached.
            self.rate.record()
            self.client._delay()
            self.log.info("Fleetsave %s -> %s (objetivo: %.2fh)", loc.coords, plan["destination"], target_dur / 3600)
            res = "all" if getattr(self.cfg, "fleetsave_carry_resources", True) else None
            self.client.send_fleet(loc.coords, plan["destination"], {},
                                   mission=plan["mission"],
                                   resources=res,
                                   speed_percent=plan.get("speed_percent", 1.0),
                                   target_duration_s=target_dur)

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
            self.client.read_planet_state(loc)
            flyable = {k: v for k, v in loc.ships.items() if k != "solar_satellite" and v > 0}
            if not flyable:
                continue
            plan = fleet_mod.fleetsave_plan(loc.coords, coords, self.cfg, remaining_h)
            if not plan:
                continue
            self.rate.record()
            self.client._delay()
            res = "all" if getattr(self.cfg, "fleetsave_carry_resources", True) else None
            self.log.info("Barrido nocturno: vaciando %s -> %s (vuelve en ~%.1fh)",
                          loc.coords, plan["destination"], remaining_h)
            self.client.send_fleet(loc.coords, plan["destination"], {},
                                   mission=plan["mission"], resources=res,
                                   speed_percent=plan.get("speed_percent", 1.0),
                                   target_duration_s=remaining_h * 3600)
            swept += 1
        self.log.info("Barrido nocturno completado: %d ubicación(es) vaciada(s).", swept)

    def _recall_sleep_fleetsaves(self):
        planets = self.client.read_planets()
        if not planets:
            return
        # Construir lista de todas las ubicaciones (planetas y lunas)
        all_locations = []
        for p in planets:
            all_locations.append(p)
            if p.has_moon and p.moon:
                all_locations.append(p.moon)

        mvs = self.client.read_movements()
        our_coords_str = {f"{loc.coords.galaxy}:{loc.coords.system}:{loc.coords.position}" for loc in all_locations}
        
        for mv in mvs:
            if (mv.get("mission") in ("4", "deploy")) and not mv.get("is_return", False):
                origin = mv.get("origin")
                dest = mv.get("destination")
                if origin in our_coords_str and dest in our_coords_str and origin != dest:
                    self.log.info("Retornando despliegue de fleetsave: %s -> %s", origin, dest)
                    self.client.recall_fleet(origin, dest, mission="deploy")

    def _last_known_ships(self, coords) -> dict:
        """Naves conocidas (del último ciclo) en una ubicación, para las alertas de
        ataque (sin navegación extra; cuando aún no hay datos devuelve {})."""
        for p in self.last_planets:
            locs = [p] + ([p.moon] if getattr(p, "moon", None) else [])
            for loc in locs:
                if loc.coords.tuple() == coords.tuple() and loc.coords.type == coords.type:
                    return getattr(loc, "ships", {}) or {}
        return {}

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
        if not getattr(self.cfg, "enable_spy_watch", True):
            return
        if not (getattr(self.cfg, "telegram_token", "") and getattr(self.cfg, "telegram_chat_id", "")):
            return
        now = time.time()
        cooldown = max(0, int(getattr(self.cfg, "spy_watch_cooldown_mins", 30))) * 60
        for mv in mvs:
            if str(mv.get("mission", "")) != "6" or mv.get("is_return", False):
                continue
            dest = mv.get("destination", "")
            if dest not in our_by_coords:
                continue
            origin = mv.get("origin", "Desconocido")
            key = f"{origin}->{dest}"
            if key in self._spy_seen and now - self._spy_seen[key] < cooldown:
                continue
            self._spy_seen[key] = now
            self._save_state()
            msg = (
                f"🔍 <b>¡Te están sondeando en OGame!</b>\n\n"
                f"• <b>Origen:</b> [{origin}]\n"
                f"• <b>Destino:</b> [{dest}]\n"
                f"• <b>Llegada de las sondas:</b> {mv.get('arrival_text', '')}\n\n"
                f"<i>Un sondeo suele preceder a un ataque. Revisa o saca la flota.</i>"
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
            with open("fleet_flights.json", "w", encoding="utf-8") as f:
                json.dump({"flights": build_flights(mvs, time.time(), prev=prev),
                           "updated": time.time()}, f, ensure_ascii=False, indent=2)
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

        # 1. Encontrar planetas/lunas bajo ataque con su tiempo de llegada mínimo
        under_attack = {}
        for mv in mvs:
            if not ((mv.get("is_hostile") or mv.get("mission") in ("1", "2", "9")) and not mv.get("is_return", False)):
                continue
            dest_coords = mv.get("destination", "")
            targets = our_by_coords.get(dest_coords, [])
            if not targets:
                continue

            arr_text = mv.get("arrival_text", "")
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
            mission_str = mission_labels.get(str(mv.get("mission", "")), "Ataque")
            self.record_session_action("hostile_attacks", f"{mission_str} desde {origin_coords_str}", arr_sec, dest_coords)

            # Enviar notificación de Telegram si está configurado
            if getattr(self.cfg, "telegram_token", "") and getattr(self.cfg, "telegram_chat_id", ""):
                now = time.time()
                arr_epoch = now + arr_sec
                arr_epoch_rounded = int(arr_epoch / 60) * 60  # redondear a 60s
                attack_key = f"{origin_coords_str}->{dest_coords}:{mission_str}:{arr_epoch_rounded}"

                if attack_key not in self.telegram_notified_attacks:
                    self.telegram_notified_attacks[attack_key] = arr_epoch
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
                    self._escape_attack_loc(loc, all_locations, under_attack)
                
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
 
    def _escape_attack_loc(self, origin_loc: Planet, all_locations: List[Planet], under_attack: dict):
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
            if sibling_key in under_attack:
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
        else:
            self.log.error("Fallo al enviar la flota de evasión desde %s.", origin_loc.coords)
    def initialize_session_stats(self):
        """Inicializa las estadísticas a 0 para la nueva sesión e ignora mensajes existentes."""
        self.log.info("Inicializando estadísticas de la sesión (ignorando mensajes previos)...")
        import json
        import os

        stats_file = "ogbot_stats.json"
        stats = {
            "total_farming": {"metal": 0, "crystal": 0, "deut": 0},
            "total_recycling": {"metal": 0, "crystal": 0, "deut": 0},
            "total_expeditions": {"metal": 0, "crystal": 0, "deut": 0, "dark_matter": 0, "ships_found": {}},
            "parsed_messages": [],
            "spy_seen_at_boot": [],
            "session_actions": {
                "buildings": {},
                "research": [],
                "fleet": {},
                "defense": {},
                "farming": {},
                "expeditions": {},
                "hostile_attacks": {},
                "espionage": {}
            }
        }

        # Combate/expedición/reciclaje: marcamos los previos como vistos (no contabilizamos
        # loot antiguo). El espionaje (tab 20) va aparte: guardamos los avisos YA presentes
        # en 'spy_seen_at_boot' para no soltar un alud al arrancar, pero SIN marcarlos
        # 'parsed', de modo que cualquier aviso NUEVO sí dispare el Telegram.
        tabs = [21, 22, 24]
        for tab_id in tabs:
            try:
                msgs = self.client.read_message_reports(tab_id)
                for m in msgs:
                    msg_id = f"{tab_id}-{m['id']}"
                    if msg_id not in stats["parsed_messages"]:
                        stats["parsed_messages"].append(msg_id)
            except Exception as e:
                self.log.warning("No se pudieron leer mensajes previos del tab %d durante la inicialización: %s", tab_id, e)
        try:
            for m in self.client.read_message_reports(20):
                sid = f"20-{m['id']}"
                if sid not in stats["spy_seen_at_boot"]:
                    stats["spy_seen_at_boot"].append(sid)
        except Exception as e:
            self.log.warning("No se pudieron leer avisos de espionaje previos al inicializar: %s", e)

        try:
            with open(stats_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2)
            self.log.info("Estadísticas de sesión inicializadas con éxito. %d mensajes previos marcados como leídos o ignorados.", len(stats["parsed_messages"]))
        except Exception as e:
            self.log.error("No se pudo escribir el archivo de estadísticas inicial: %s", e)

    def record_session_action(self, action_type: str, name: str, value, planet_coords: str = None):
        """
        Registra una acción de la sesión (edificio, investigación, naves, defensa, farmeo, expedición, ataque hostil o espionaje) en ogbot_stats.json.
        """
        import json
        import os
        import time

        stats_file = "ogbot_stats.json"
        stats = {
            "total_farming": {"metal": 0, "crystal": 0, "deut": 0},
            "total_recycling": {"metal": 0, "crystal": 0, "deut": 0},
            "total_expeditions": {"metal": 0, "crystal": 0, "deut": 0, "dark_matter": 0, "ships_found": {}},
            "parsed_messages": [],
            "session_actions": {
                "buildings": {},
                "research": [],
                "fleet": {},
                "defense": {},
                "farming": {},
                "expeditions": {},
                "hostile_attacks": {},
                "espionage": {}
            }
        }

        if os.path.exists(stats_file):
            try:
                with open(stats_file, "r", encoding="utf-8") as f:
                    stats = json.load(f)
            except Exception:
                pass

        if "session_actions" not in stats:
            stats["session_actions"] = {
                "buildings": {},
                "research": [],
                "fleet": {},
                "defense": {},
                "farming": {},
                "expeditions": {},
                "hostile_attacks": {},
                "espionage": {}
            }
        elif "espionage" not in stats["session_actions"]:
            stats["session_actions"]["espionage"] = {}

        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

        action_data = {
            "name": name,
            "value": value,
            "timestamp": timestamp_str
        }

        if action_type == "research":
            stats["session_actions"]["research"].append(action_data)
        else:
            if not planet_coords:
                planet_coords = "Empire"
            actions_dict = stats["session_actions"].setdefault(action_type, {})
            planet_list = actions_dict.setdefault(planet_coords, [])
            
            # Evitar duplicar alertas de ataque hostil o espionajes idénticos
            if action_type in ("hostile_attacks", "espionage"):
                for existing in planet_list:
                    if existing["name"] == name:
                        existing["value"] = value
                        existing["timestamp"] = timestamp_str
                        try:
                            with open(stats_file, "w", encoding="utf-8") as f:
                                json.dump(stats, f, indent=2)
                        except Exception:
                            pass
                        return
                        
            planet_list.append(action_data)

        try:
            with open(stats_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2)
        except Exception as e:
            self.log.debug("No se pudo escribir ogbot_stats.json en record_session_action: %s", e)

    def update_imperial_stats(self):
        """Lee los mensajes recientes de combates, expediciones y reciclaje para compilar estadísticas."""
        self.log.info("Actualizando estadísticas imperiales desde mensajes...")
        import json
        import os
        import re

        stats_file = "ogbot_stats.json"
        stats = {
            "total_farming": {"metal": 0, "crystal": 0, "deut": 0},
            "total_recycling": {"metal": 0, "crystal": 0, "deut": 0},
            "total_expeditions": {"metal": 0, "crystal": 0, "deut": 0, "dark_matter": 0, "ships_found": {}},
            "parsed_messages": []
        }

        if os.path.exists(stats_file):
            try:
                with open(stats_file, "r", encoding="utf-8") as f:
                    stats = json.load(f)
            except Exception:
                pass

        parsed_set = set(stats.get("parsed_messages", []))
        spy_seen_at_boot = set(stats.get("spy_seen_at_boot", []))
        changed = False

        # --- Visor de mensajes leídos (lo consume la GUI vía /api/messages) ---
        msg_log_file = "messages_read.json"
        msg_log = []
        if os.path.exists(msg_log_file):
            try:
                with open(msg_log_file, "r", encoding="utf-8") as f:
                    msg_log = json.load(f).get("messages", [])
            except Exception:
                msg_log = []
        log_index = {e.get("key"): e for e in msg_log}
        log_changed = [False]
        cat_names = {21: "Combate", 22: "Expedición", 24: "Reciclaje", 20: "Espionaje"}
        ship_es = {k: v[0] for k, v in EXPEDITION_SHIP_NAMES}

        def record_msg(tab_id, m):
            """Registra (una vez) el texto íntegro de un mensaje leído. Devuelve su entrada."""
            key = f"{tab_id}-{m['id']}"
            if key in log_index:
                return log_index[key]
            txt = (m.get("text", "") or "").strip()
            if len(txt) > 4000:
                txt = txt[:4000] + "…"
            entry = {
                "key": key, "tab": tab_id,
                "category": cat_names.get(tab_id, str(tab_id)),
                "ts": time.strftime("%Y-%m-%d %H:%M"),
                "text": txt, "summary": "",
            }
            msg_log.append(entry)
            log_index[key] = entry
            log_changed[0] = True
            return entry

        def fmt_extracted(metal, crystal, deut, dark_matter, ships):
            f = lambda n: f"{n:,}".replace(",", ".")
            parts = []
            if metal: parts.append(f"Metal {f(metal)}")
            if crystal: parts.append(f"Cristal {f(crystal)}")
            if deut: parts.append(f"Deuterio {f(deut)}")
            if dark_matter: parts.append(f"Materia oscura {f(dark_matter)}")
            for sk, q in (ships or {}).items():
                parts.append(f"+{f(q)} {ship_es.get(sk, sk)}")
            return " · ".join(parts) if parts else "Sin recursos ni naves"

        tabs = {
            21: "total_farming",
            22: "total_expeditions",
            24: "total_recycling"
        }

        def clean_num(s):
            """Convierte '1.234.567' / '1 234 567' / '1,234,567' -> 1234567."""
            if isinstance(s, (int, float)):
                return int(s)
            return int(re.sub(r'[^\d]', '', str(s))) if s and re.search(r'\d', str(s)) else 0

        def num_from_raw(raw, *keys):
            for k in keys:
                if k in raw:
                    n = clean_num(str(raw[k]))
                    if n:
                        return n
            return 0

        def loot_from_raw(raw):
            """El botín de combate suele venir como JSON en data-raw-loot/resources o data-raw-result."""
            # 1. Chequear data-raw-result (para reportes de combate)
            if "result" in raw:
                try:
                    res_data = json.loads(raw["result"])
                    resources_list = res_data.get("loot", {}).get("resources", [])
                    m, c, d = 0, 0, 0
                    for res in resources_list:
                        name = res.get("resource", "").lower()
                        amount = clean_num(res.get("amount"))
                        if "metal" in name:
                            m = amount
                        elif "crystal" in name:
                            c = amount
                        elif "deuterium" in name:
                            d = amount
                    if m or c or d:
                        return m, c, d
                except Exception:
                    pass

            # 2. Chequear data-raw-cargo (para transportes y expediciones)
            if "cargo" in raw:
                try:
                    cargo_data = json.loads(raw["cargo"])
                    m = clean_num(cargo_data.get("metal"))
                    c = clean_num(cargo_data.get("crystal"))
                    d = clean_num(cargo_data.get("deuterium") or cargo_data.get("deut"))
                    if m or c or d:
                        return m, c, d
                except Exception:
                    pass

            # 3. Formato clásico (data-raw-loot/resources)
            blob = raw.get("loot") or raw.get("resources")
            if not blob:
                return (0, 0, 0)
            try:
                d = json.loads(blob)
            except Exception:
                return (0, 0, 0)
            g = lambda *ks: next(
                (clean_num(str(d[k])) for k in ks if k in d), 0)
            return (g("metal", "901"), g("crystal", "902"),
                    g("deuterium", "deut", "903"))

        def num_from_text(text, labels):
            """Respaldo: extrae la cifra de una etiqueta. Prioriza 'etiqueta: número' y solo
            si no aparece usa 'número etiqueta' (número justo antes), para no robar la cifra
            del recurso anterior (p.ej. en 'Metal: 1.000 Cristal: 500' el cristal son 500)."""
            num = r'(\d[\d.,]*)'
            for lab in labels:   # 1) etiqueta seguida del número (orden real de OGame)
                m = re.search(r'(?:%s)\s*:?\s*%s' % (lab, num), text, re.IGNORECASE)
                if m:
                    return clean_num(m.group(1))
            for lab in labels:   # 2) número inmediatamente antes de la etiqueta
                m = re.search(r'%s\s*(?:de\s+)?(?:%s)' % (num, lab), text, re.IGNORECASE)
                if m:
                    return clean_num(m.group(1))
            return 0

        for tab_id, key in tabs.items():
            msgs = self.client.read_message_reports(tab_id)
            for m in msgs:
                msg_id = f"{tab_id}-{m['id']}"
                entry = record_msg(tab_id, m)  # registrar el texto aunque ya esté contabilizado
                if msg_id in parsed_set:
                    continue

                text = m.get("text", "") or ""
                raw = m.get("raw", {}) or {}
                dark_matter = 0

                text_lower = text.lower()
                # Filtrar mensajes de combate para registrar solo ataques exitosos propios
                if key == "total_farming":
                    is_win = ("has ganado" in text_lower or "you won" in text_lower or "gewonnen" in text_lower)
                    is_loss_to_attacker = ("el atacante ha ganado" in text_lower or "the attacker has won" in text_lower or "der angreifer hat gewonnen" in text_lower)
                    if not is_win or is_loss_to_attacker:
                        entry["summary"] = "Combate no ganado u hostil (no contabilizado)"
                        parsed_set.add(msg_id)
                        stats["parsed_messages"].append(msg_id)
                        changed = True
                        continue

                # Filtrar mensajes del tab "Otros" para procesar solo informes de reciclaje reales
                elif key == "total_recycling":
                    is_rec = ("reciclador" in text_lower or "recycler" in text_lower or
                              "escombros" in text_lower or "debris" in text_lower or
                              "recolecci" in text_lower or "trümmerfeld" in text_lower)
                    if not is_rec:
                        entry["summary"] = "No es informe de reciclaje (no contabilizado)"
                        parsed_set.add(msg_id)
                        stats["parsed_messages"].append(msg_id)
                        changed = True
                        continue

                # 1) Datos estructurados data-raw-* (fiables, sin depender del idioma)
                metal = num_from_raw(raw, "metal")
                crystal = num_from_raw(raw, "crystal")
                deut = num_from_raw(raw, "deuterium", "deut")
                if not (metal or crystal or deut):
                    metal, crystal, deut = loot_from_raw(raw)

                # 2) Respaldo: parsear el texto del informe
                if not (metal or crystal or deut):
                    metal = num_from_text(text, ["metal"])
                    crystal = num_from_text(text, ["cristal", "crystal"])
                    deut = num_from_text(text, ["deuterio", "deuterium"])

                if not (metal or crystal or deut) and key != "total_expeditions":
                    self.log.debug("Mensaje %s sin recursos extraídos. raw=%s txt=%.120s",
                                   msg_id, raw, text.replace("\n", " "))

                found_ships = {}
                if key == "total_expeditions":
                    dark_matter = (num_from_raw(raw, "darkmatter", "dark_matter")
                                   or num_from_text(text, ["materia oscura", "dark matter"]))

                    found_ships = parse_found_ships(text)
                    ships_found = stats["total_expeditions"].setdefault("ships_found", {})
                    for sk, qty in found_ships.items():
                        ships_found[sk] = ships_found.get(sk, 0) + qty

                entry["summary"] = fmt_extracted(metal, crystal, deut, dark_matter, found_ships)

                stats[key]["metal"] = stats[key].get("metal", 0) + metal
                stats[key]["crystal"] = stats[key].get("crystal", 0) + crystal
                stats[key]["deut"] = stats[key].get("deut", 0) + deut
                if key == "total_expeditions":
                    stats[key]["dark_matter"] = stats[key].get("dark_matter", 0) + dark_matter

                parsed_set.add(msg_id)
                stats["parsed_messages"].append(msg_id)
                changed = True

        # Avisos de contraespionaje (tab 20): "Se ha detectado una flota del planeta X
        # cerca de tu planeta Y". Rescata sondeos que el polling de movimientos no pilló.
        if getattr(self.cfg, "enable_spy_watch", True) and getattr(self.cfg, "spy_watch_messages", True):
            tg_ok = bool(getattr(self.cfg, "telegram_token", "") and getattr(self.cfg, "telegram_chat_id", ""))
            try:
                for m in self.client.read_message_reports(20):
                    msg_id = f"20-{m['id']}"
                    entry = record_msg(20, m)  # registrar el texto siempre (lo lea o no Telegram)
                    if msg_id in parsed_set:
                        continue
                    parsed_set.add(msg_id)
                    stats["parsed_messages"].append(msg_id)
                    changed = True
                    text = m.get("text", "") or ""
                    tl = text.lower()
                    # Solo las notificaciones de "me han espiado", no mis propios informes.
                    if ("se ha detectado una flota" not in tl and "cerca de tu planeta" not in tl
                            and "near your planet" not in tl):
                        entry["summary"] = "Otro mensaje (sin aviso de espionaje)"
                        continue
                    coords = re.findall(r'\[(\d+:\d+:\d+)\]', text)
                    origin = coords[0] if coords else "?"
                    mine = coords[-1] if len(coords) > 1 else "?"
                    ce = re.search(r'contra-?espionaje[^\d]*(\d+)\s*%', tl)
                    ce_txt = f"{ce.group(1)}%" if ce else "?"
                    # No avisar del backlog que ya estaba al arrancar (evita el alud); sí de
                    # cualquier aviso nuevo aparecido mientras el bot corre.
                    is_backlog = msg_id in spy_seen_at_boot
                    self.log.info("Vigilancia de espionaje (mensaje): %s -> %s%s",
                                  origin, mine, " [previo al arranque, no aviso]" if is_backlog else "")
                    if is_backlog:
                        entry["summary"] = f"🔍 Te han espiado desde [{origin}] (ya estaba al arrancar)"
                    elif tg_ok:
                        entry["summary"] = f"🔍 Te han espiado desde [{origin}] (avisado por Telegram)"
                        alert = (
                            f"🔍 <b>¡Te han espiado en OGame!</b> (detectado)\n\n"
                            f"• <b>Desde:</b> [{origin}]\n"
                            f"• <b>Tu ubicación:</b> [{mine}]\n"
                            f"• <b>Prob. contraespionaje:</b> {ce_txt}\n\n"
                            f"<i>Un sondeo suele preceder a un ataque.</i>"
                        )
                        utils.send_telegram_message(self.cfg.telegram_token,
                                                    self.cfg.telegram_chat_id, alert, logger=self.log)
                    else:
                        entry["summary"] = f"🔍 Te han espiado desde [{origin}] (Telegram no configurado)"
            except Exception as e:
                self.log.debug("Error leyendo avisos de espionaje (tab 20): %s", e)

        if changed:
            try:
                with open(stats_file, "w", encoding="utf-8") as f:
                    json.dump(stats, f, indent=2)
                self.log.info("Estadísticas imperiales actualizadas en ogbot_stats.json.")
            except Exception as e:
                self.log.debug("No se pudo escribir ogbot_stats.json: %s", e)

        if log_changed[0]:
            try:
                del msg_log[:-300]  # acotar el fichero a los 300 mensajes más recientes
                with open(msg_log_file, "w", encoding="utf-8") as f:
                    json.dump({"messages": msg_log}, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self.log.debug("No se pudo escribir messages_read.json: %s", e)

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
    
    # Formato Xh Ym Zs / Ym Zs / Zs / etc.
    parts = re.findall(r'(\d+)\s*([hms])', time_str)
    if parts:
        total_seconds = 0
        for val, unit in parts:
            if unit == 'h':
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
