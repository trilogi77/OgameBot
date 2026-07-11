"""
stats.py
========
Estadísticas de sesión e imperiales del bot, extraídas de brain.py como mixin.

StatsMixin se mezcla en Brain (misma instancia): accede a self.cfg, self.log,
self.client y self.last_planets igual que antes. EXPEDITION_SHIP_NAMES y
parse_found_ships siguen viviendo en brain.py (los usan otras partes); aquí se
importan de forma tardía dentro de los métodos para evitar el ciclo brain<->stats.
"""
from __future__ import annotations
import re
import time
from typing import Dict
from . import utils

_COORDS_BRACKET_RE = re.compile(r'\[(\d{1,2}):(\d{1,3}):(\d{1,2})\]')


def combat_target_coords(raw: dict, text: str) -> str:
    """Coordenadas del planeta atacado en un mensaje de combate, como 'G:S:P'.
    Prioriza el atributo data-raw-coordinates; respaldo: primer [G:S:P] del texto."""
    c = str(raw.get("coordinates", "") or "").strip()
    m = re.match(r'^(\d{1,2}):(\d{1,3}):(\d{1,2})$', c) or _COORDS_BRACKET_RE.search(text or "")
    return f"{m.group(1)}:{m.group(2)}:{m.group(3)}" if m else ""


class StatsMixin:

    def initialize_session_stats(self):
        """Crea la estructura de estadísticas la PRIMERA vez (ignorando el backlog de
        mensajes). Si el fichero ya existe NO se resetea: los acumulados de farmeo,
        expediciones y reciclaje sobreviven a los reinicios, y los combates que
        aterrizaron con el bot parado se contabilizan al volver."""
        import json
        import os

        stats_file = "ogbot_stats.json"
        if os.path.exists(stats_file):
            try:
                with open(stats_file, "r", encoding="utf-8") as f:
                    json.load(f)
                self.log.info("Estadísticas existentes conservadas (acumulados entre sesiones).")
                return
            except Exception:
                self.log.warning("ogbot_stats.json corrupto: se reinicializa.")

        self.log.info("Inicializando estadísticas (primera vez: ignorando mensajes previos)...")
        stats = {
            "total_farming": {"metal": 0, "crystal": 0, "deut": 0},
            "total_recycling": {"metal": 0, "crystal": 0, "deut": 0},
            "total_expeditions": {"metal": 0, "crystal": 0, "deut": 0, "dark_matter": 0, "ships_found": {}},
            "expe_outcomes": {"resources": 0, "ships": 0, "items": 0, "dark_matter": 0, "nothing": 0},
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

        # Combate/expedición/reciclaje: marcamos los previos como vistos (no contabilizamos
        # loot antiguo). El espionaje (tab 20) NO se toca aquí: su dedup/notificación vive en
        # spy_notifications.csv (libro mayor persistente entre reinicios), que siembra su
        # propio backlog la primera vez y avisa de TODO mensaje nuevo a partir de entonces.
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
            utils.atomic_write_json(stats_file, stats)
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
                            utils.atomic_write_json(stats_file, stats)
                        except Exception:
                            pass
                        return

            planet_list.append(action_data)
            del planet_list[:-200]   # acumulado entre sesiones: acotar cada lista

        try:
            utils.atomic_write_json(stats_file, stats)
        except Exception as e:
            self.log.debug("No se pudo escribir ogbot_stats.json en record_session_action: %s", e)

    def update_player_score(self, points, rank, name=""):
        """Guarda el ranking del jugador (contrato C5) en ogbot_stats.json."""
        import json
        import os
        stats_file = "ogbot_stats.json"
        stats = {}
        if os.path.exists(stats_file):
            try:
                with open(stats_file, "r", encoding="utf-8") as f:
                    stats = json.load(f)
            except Exception:
                stats = {}
        stats["player_points"] = int(points or 0)
        stats["player_rank"] = int(rank or 0)
        if name:
            stats["player_name"] = name
        try:
            utils.atomic_write_json(stats_file, stats)
        except Exception as e:
            self.log.debug("No se pudo escribir el ranking en ogbot_stats.json: %s", e)

    def update_imperial_stats(self):
        """Lee los mensajes recientes de combates, expediciones y reciclaje para compilar estadísticas."""
        self.log.info("Actualizando estadísticas imperiales desde mensajes...")
        import json
        import os
        import re
        from .brain import EXPEDITION_SHIP_NAMES, parse_found_ships  # tardío: evita ciclo brain<->stats

        # --- Ahorro de acciones (y comportamiento más humano) ---
        # El sobre de la barra ya dice cuántos mensajes hay sin leer: si marca 0 y el
        # libro mayor de espionaje ya está sembrado, no hay nada nuevo que parsear y NI
        # SIQUIERA entramos en la página de mensajes. Con el indicador ausente (None) se
        # lee todo como siempre (fail-open). Los tests usan clientes falsos sin estos
        # métodos: getattr los detecta y también cae al comportamiento clásico.
        spy_watch = getattr(self.cfg, "enable_spy_watch", True) and \
            getattr(self.cfg, "spy_watch_messages", True)
        spy_seed_pending = spy_watch and not os.path.exists(self.SPY_LEDGER_FILE)
        # Avisos ya detectados pero PENDIENTES de reenviar por Telegram (notified=0): el
        # reintento prometido vive en _process_spy_messages, así que debe correr aunque no
        # haya nada sin leer (el mensaje quedó leído en el juego la primera vez). Solo
        # cuenta con Telegram configurado: sin token no hay reenvío posible y bloquearía
        # el ahorro de acciones para siempre.
        spy_retry_pending = False
        if spy_watch and not spy_seed_pending and \
                getattr(self.cfg, "telegram_token", "") and getattr(self.cfg, "telegram_chat_id", ""):
            try:
                import csv
                with open(self.SPY_LEDGER_FILE, "r", encoding="utf-8", newline="") as f:
                    spy_retry_pending = any((r.get("notified") or "").strip() != "1"
                                            for r in csv.DictReader(f))
            except Exception:
                pass
        unread = None
        probe = getattr(self.client, "unread_messages_count", None)
        if callable(probe):
            unread = probe()
        if unread == 0 and not spy_seed_pending and not spy_retry_pending:
            self.log.info("Mensajes: sobre a 0 (nada sin leer); fase de lectura omitida.")
            return

        # Contadores por pestaña: solo se abren las pestañas que marcan mensajes nuevos.
        # fleet_counts=None significa "no se pudo saber" -> se leen todas (fail-open).
        # Se pasa el valor del sobre (leído ANTES de navegar) para que el overview detecte
        # los mensajes que el propio aterrizaje marca como leídos (subpestaña visible).
        cat_counts, fleet_counts = {}, None
        overview = getattr(self.client, "read_messages_overview", None)
        if unread is not None and callable(overview):
            try:
                counts = overview(unread) or {}
                cat_counts = counts.get("categories") or {}
                fleet_counts = counts.get("fleet_tabs")
            except Exception as e:
                self.log.debug("Sin contadores de mensajes (%s); se leerán todas las pestañas.", e)

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
        cat_names = {21: "Combate", 22: "Expedición", 24: "Reciclaje", 20: "Espionaje",
                     23: "Uniones/transporte", 5: "Universo", 4: "OGame"}
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
            if fleet_counts is not None and not fleet_counts.get(tab_id, 0):
                continue   # la subpestaña no marca mensajes nuevos: nos ahorramos el clic
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

                    # Desglose de resultados (C8): clasificar el mensaje por lo extraído y,
                    # para objetos, por palabras clave del cliente español/inglés
                    # ("objeto", "artefacto", "inventario", "item").
                    outcomes = stats.setdefault("expe_outcomes", {
                        "resources": 0, "ships": 0, "items": 0, "dark_matter": 0, "nothing": 0})
                    if dark_matter:
                        outcome = "dark_matter"
                    elif found_ships:
                        outcome = "ships"
                    elif metal or crystal or deut:
                        outcome = "resources"
                    elif any(w in text_lower for w in ("objeto", "artefacto", "inventario", "item")):
                        outcome = "items"
                    else:
                        outcome = "nothing"
                    outcomes[outcome] = outcomes.get(outcome, 0) + 1

                entry["summary"] = fmt_extracted(metal, crystal, deut, dark_matter, found_ships)

                # Aprendizaje de farmeo: atribuir el botín REAL al objetivo del combate
                # (también con 0 botín: los raids vacíos cuentan para la blacklist).
                if key == "total_farming":
                    tcoords = combat_target_coords(raw, text)
                    if tcoords:
                        self._record_target_loot(tcoords, metal, crystal, deut)

                stats[key]["metal"] = stats[key].get("metal", 0) + metal
                stats[key]["crystal"] = stats[key].get("crystal", 0) + crystal
                stats[key]["deut"] = stats[key].get("deut", 0) + deut
                if key == "total_expeditions":
                    stats[key]["dark_matter"] = stats[key].get("dark_matter", 0) + dark_matter

                parsed_set.add(msg_id)
                stats["parsed_messages"].append(msg_id)
                changed = True

        # Uniones/transporte (subpestaña 23): el bot no saca estadísticas de ahí, pero si
        # marca mensajes nuevos los abrimos (los deja leídos y el sobre puede volver a 0)
        # y los registramos para el visor de la GUI. Solo con contadores fiables.
        if fleet_counts is not None and fleet_counts.get(23, 0):
            try:
                for m in self.client.read_message_reports(23):
                    record_msg(23, m)
            except Exception as e:
                self.log.debug("No se pudieron leer los mensajes de Uniones/transporte: %s", e)

        # Avisos de "te han espiado" (tab 20). Su dedup/notificación vive en un libro mayor
        # CSV persistente (spy_notifications.csv), no en parsed_messages, para garantizar UNA
        # notificación por mensaje nuevo y reintentar si Telegram falla. Ver _process_spy_messages.
        if spy_watch:
            if fleet_counts is None or fleet_counts.get(20, 0) or spy_seed_pending or spy_retry_pending:
                self._process_spy_messages(record_msg)
            else:
                self.log.debug("Espionaje: la subpestaña 20 no marca mensajes nuevos; omitida.")

        # Grupos Universo (5) y OGame (4): noticias del servidor/juego. Solo se abren si su
        # contador marca mensajes nuevos (abrirlos los deja leídos); se registran para la GUI.
        reader = getattr(self.client, "read_message_category", None)
        if callable(reader):
            for cid in (5, 4):
                if not cat_counts.get(cid, 0):
                    continue
                try:
                    n = 0
                    for m in reader(cid):
                        record_msg(cid, m)
                        n += 1
                    self.log.info("Mensajes de %s leídos: %d.", cat_names.get(cid, cid), n)
                except Exception as e:
                    self.log.debug("No se pudieron leer los mensajes del grupo %s: %s", cid, e)

        if changed:
            try:
                # Sin reset por sesión el índice de vistos crece sin límite: conservar
                # los últimos 3000 (los tabs de mensajes de OGame muestran muchos menos).
                del stats["parsed_messages"][:-3000]
                utils.atomic_write_json(stats_file, stats)
                self.log.info("Estadísticas imperiales actualizadas en ogbot_stats.json.")
            except Exception as e:
                self.log.debug("No se pudo escribir ogbot_stats.json: %s", e)
            # El historial por objetivo (target_stats) vive en state.json: persistirlo ya.
            try:
                self._save_state()
            except Exception:
                pass

        if log_changed[0]:
            try:
                del msg_log[:-300]  # acotar el fichero a los 300 mensajes más recientes
                utils.atomic_write_json(msg_log_file, {"messages": msg_log})
            except Exception as e:
                self.log.debug("No se pudo escribir messages_read.json: %s", e)

    def _own_location_summary(self, coords_str: str) -> str:
        """Recursos + flota (planeta y, si tiene, luna) de un planeta PROPIO, para enriquecer
        el aviso de espionaje con lo que hay en riesgo. Lee last_planets (datos del ciclo).
        Devuelve '' si no se encuentra la ubicación."""
        from .brain import EXPEDITION_SHIP_NAMES  # tardío: evita ciclo brain<->stats
        planet = None
        for p in (getattr(self, "last_planets", None) or []):
            c = getattr(p, "coords", None)
            if c and f"{c.galaxy}:{c.system}:{c.position}" == coords_str:
                planet = p
                break
        if planet is None:
            return ""
        f = lambda n: f"{int(n):,}".replace(",", ".")
        ship_es = {k: v[0] for k, v in EXPEDITION_SHIP_NAMES}
        def fleet_line(loc):
            ships = getattr(loc, "ships", {}) or {}
            items = [f"{ship_es.get(k, k)}: {f(q)}" for k, q in ships.items() if q]
            return ", ".join(items) if items else "sin flota"
        lines = []
        r = getattr(planet, "resources", None)
        if r is not None:
            lines.append(f"• <b>Recursos:</b> M {f(r.metal)} · C {f(r.crystal)} · D {f(r.deut)}")
        lines.append(f"• <b>Flota (planeta):</b> {fleet_line(planet)}")
        moon = getattr(planet, "moon", None)
        if moon is not None and (getattr(moon, "ships", {}) or {}):
            lines.append(f"• <b>Flota (luna):</b> {fleet_line(moon)}")
        return "\n".join(lines)

    SPY_LEDGER_FILE = "spy_notifications.csv"

    def _process_spy_messages(self, record_msg):
        """Garantiza UNA notificación de Telegram por cada aviso de "te han espiado" (tab 20).

        Usa spy_notifications.csv como libro mayor persistente y fuente de verdad:
            msg_id, from, to, detected_at, notified, notified_at
        - notified="1" -> ya avisado, no se repite.
        - notified="0" -> detectado pero NO avisado (Telegram falló o no estaba configurado);
          se reintenta el envío en cada ciclo hasta que llega.
        El CSV persiste entre reinicios, así que un sondeo recibido con el bot caído se avisa
        al volver. Solo la PRIMERA vez (sin CSV) se siembra el backlog como ya notificado para
        no soltar un alud de avisos viejos.
        """
        import csv
        import os
        import re

        ledger_file = self.SPY_LEDGER_FILE
        fields = ["msg_id", "from", "to", "detected_at", "notified", "notified_at"]
        ledger: Dict[str, dict] = {}
        ledger_existed = os.path.exists(ledger_file)
        if ledger_existed:
            try:
                with open(ledger_file, "r", encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        ledger[row["msg_id"]] = row
            except Exception as e:
                self.log.debug("No se pudo leer %s: %s", ledger_file, e)

        tg_ok = bool(getattr(self.cfg, "telegram_token", "") and getattr(self.cfg, "telegram_chat_id", ""))
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        changed = False

        try:
            msgs = self.client.read_message_reports(20)
        except Exception as e:
            self.log.debug("Error leyendo avisos de espionaje (tab 20): %s", e)
            return
        # ¿Es fiable una lectura VACÍA? read_message_reports devuelve [] también en fallos
        # silenciosos (goto fallido, evaluate roto). Solo damos el vacío por bueno si de
        # verdad estamos mirando la subpestaña 20; si no, una primera siembra "vacía" por
        # error daría el backlog por procesado y el siguiente ciclo notificaría avisos viejos.
        verify = getattr(self.client, "message_tab_active", None)
        read_verified = bool(msgs) or (callable(verify) and verify(20))

        # Coords propias (planeta + luna comparten g:s:p) para distinguir "me han espiado"
        # de mis propios informes. El OGame nuevo no usa frase: el aviso es una fila compacta
        # (hace cuánto / nombre del espía / - / - / [tus coords]). La señal fiable e
        # independiente del idioma es que las coords del mensaje son de un planeta MÍO.
        own_coords = set()
        for p in (getattr(self, "last_planets", None) or []):
            try:
                c = p.coords
                own_coords.add(f"{c.galaxy}:{c.system}:{c.position}")
            except Exception:
                pass
        # Respaldo robusto: el caché persistente de planetas (siempre disponible, sin depender
        # de que el ciclo ya haya leído ubicaciones cuando corre esta función).
        try:
            import json
            with open("planets_cache.json", "r", encoding="utf-8") as f:
                for p in json.load(f):
                    if p.get("coords"):
                        own_coords.add(p["coords"])
                    moon = p.get("moon") or {}
                    if moon.get("coords"):
                        own_coords.add(moon["coords"])
        except Exception:
            pass

        for m in msgs:
            msg_id = f"20-{m['id']}"
            entry = record_msg(20, m)  # registrar el texto siempre (para el visor de la GUI)
            text = m.get("text", "") or ""
            tl = text.lower()
            coords = re.findall(r'\d+:\d+:\d+', text)
            spied = next((c for c in coords if c in own_coords), None)
            # Respaldo: algunos idiomas/servidores aún usan la frase larga.
            phrase = ("se ha detectado una flota" in tl or "cerca de tu planeta" in tl
                      or "near your planet" in tl)
            if not spied and not phrase:
                entry["summary"] = "Otro mensaje (sin aviso de espionaje)"
                continue

            # Parseo barato (sin abrir): planeta espiado. El origen, el % de contraespionaje y
            # el nombre REAL del espía solo aparecen al ABRIR el mensaje (la fila compacta trae
            # NUESTRO propio nombre, no el del espía), así que se rellenan más abajo y solo para
            # los avisos nuevos (coste mínimo).
            mine = spied or (coords[-1] if coords else "?")
            origin = next((c for c in coords if c != mine), "?")
            ce = re.search(r'contra-?espionaje[^\d]*(\d+)\s*%', tl)
            ce_txt = f"{ce.group(1)}%" if ce else "?"
            who = f"[{origin}]" if origin != "?" else "?"

            row = ledger.get(msg_id)
            if row and row.get("notified") == "1":
                entry["summary"] = f"🔍 Te han espiado en [{mine}] (ya notificado)"
                continue

            # Primer arranque (sin libro mayor): sembrar el backlog como ya notificado para no
            # soltar un alud de avisos viejos. A partir de ahí, todo mensaje nuevo se avisa.
            if row is None and not ledger_existed:
                ledger[msg_id] = {"msg_id": msg_id, "from": who, "to": mine,
                                  "detected_at": now_str, "notified": "1", "notified_at": now_str}
                entry["summary"] = f"🔍 Te han espiado en [{mine}] (backlog inicial, sin avisar)"
                changed = True
                continue

            # Mensaje nuevo, o detectado antes pero pendiente de aviso (reintento).
            if row is None:
                row = {"msg_id": msg_id, "from": who, "to": mine,
                       "detected_at": now_str, "notified": "0", "notified_at": ""}
                ledger[msg_id] = row
                changed = True
                self.log.info("Vigilancia de espionaje (mensaje nuevo): %s en [%s]", msg_id, mine)

            if not tg_ok:
                entry["summary"] = f"🔍 Te han espiado en [{mine}] (Telegram no configurado, pendiente)"
                continue

            # Abrir el mensaje (solo avisos nuevos, justo antes de notificar). La fila compacta
            # NO trae: coords de origen, % de contraespionaje, el nombre REAL del espía (va entre
            # paréntesis tras las coords de origen; el de la fila es NUESTRO propio nombre) ni si
            # nos espiaron la LUNA o el PLANETA.
            spy_name = ""
            mine_is_moon = None
            try:
                full = self.client.read_message_full(m["id"])
            except Exception:
                full = ""
            if full:
                if origin == "?":
                    o = next((c for c in re.findall(r'\d+:\d+:\d+', full)
                              if c not in own_coords), None)
                    if o:
                        origin = o
                if ce_txt == "?":
                    fce = re.search(r'contra-?espionaje[^\d]*(\d+)\s*%', full.lower())
                    if fce:
                        ce_txt = f"{fce.group(1)}%"
                sm = re.search(r'\(([^)]{2,40})\)', full)   # nombre del espía, entre paréntesis
                if sm:
                    spy_name = sm.group(1).strip()
                # ¿Mi LUNA o mi PLANETA? Lo que precede a mis coords en "cerca de tu planeta".
                bm = re.search(r'(?:cerca de tu planeta|near your planet)\s+(.*?)\[' +
                               re.escape(mine), full, re.I)
                if bm:
                    mine_is_moon = bool(re.search(r'luna|moon', bm.group(1), re.I))

            who = spy_name or (f"[{origin}]" if origin != "?" else "desconocido")
            mi_loc = "luna" if mine_is_moon else ("planeta" if mine_is_moon is False else "ubicación")
            parts = [f"• <b>Tu {mi_loc}:</b> [{mine}]"]
            if origin != "?":
                parts.append(f"• <b>Origen:</b> [{origin}]")
            if spy_name:
                # Escapar: un nombre con < > & rompía el parse_mode=HTML y el ledger reintentaba en bucle.
                parts.append(f"• <b>Espía:</b> {utils.tg_escape(spy_name)}")
            parts.append(f"• <b>Prob. contraespionaje:</b> {ce_txt}")
            loc_summary = self._own_location_summary(mine)
            if loc_summary:
                parts.append(loc_summary)
            alert = (
                "🔍 <b>¡Te han espiado en OGame!</b>\n\n" + "\n".join(parts) +
                "\n\n<i>Un sondeo suele preceder a un ataque. Revisa o saca la flota.</i>"
            )
            ok = utils.send_telegram_message(self.cfg.telegram_token, self.cfg.telegram_chat_id,
                                             alert, logger=self.log, block=True)
            if ok:
                row["notified"] = "1"
                row["notified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                row["from"] = spy_name or (origin if origin != "?" else "?")
                entry["summary"] = f"🔍 Te han espiado en [{mine}] (espía {who}, avisado)"
                self.log.info("Aviso de espionaje notificado por Telegram: %s en [%s] (origen %s)", msg_id, mine, who)
            else:
                entry["summary"] = f"🔍 Te han espiado en [{mine}] (fallo de Telegram, se reintentará)"
                self.log.warning("Fallo al notificar espionaje %s; se reintentará el próximo ciclo.", msg_id)
            changed = True

        # Filas pendientes (notified=0) cuyo mensaje ya no está en la pestaña: imposibles de
        # reenviar (el cuerpo se perdió; también si cayó a otra página de la lista, donde su
        # valor ya es nulo). Se cierran para que no bloqueen eternamente el ahorro de acciones
        # (spy_retry_pending). Solo con lectura verificada: un [] por fallo no borra nada.
        if tg_ok and read_verified:
            visible = {f"20-{m['id']}" for m in msgs}
            for mid, row in ledger.items():
                if (row.get("notified") or "").strip() != "1" and mid not in visible:
                    row["notified"] = "1"
                    row["notified_at"] = now_str
                    changed = True
                    self.log.info("Aviso de espionaje %s ya no existe en la pestaña; se descarta el reenvío.", mid)

        # La primera siembra se persiste AUNQUE no hubiera avisos (CSV solo con cabecera):
        # sin esto, una cuenta sin espionajes nunca "terminaba" de sembrar y la fase de
        # mensajes no podía saltarse aunque el sobre marcara 0 sin leer. Pero solo si la
        # lectura vacía está verificada (read_verified): sembrar en falso convierte el
        # backlog en "mensajes nuevos" y suelta un alud de notificaciones viejas.
        if changed or (not ledger_existed and read_verified):
            # ponytail: reescribe el CSV entero; trivial para la decena de avisos de espionaje,
            # pasar a append solo si algún día crece de verdad.
            try:
                with open(ledger_file, "w", encoding="utf-8", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=fields)
                    w.writeheader()
                    for r in ledger.values():
                        w.writerow(r)
            except Exception as e:
                self.log.debug("No se pudo escribir %s: %s", ledger_file, e)
