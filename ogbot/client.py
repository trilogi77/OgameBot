"""
client.py — Única capa que toca el juego en vivo (Playwright).
"""
from __future__ import annotations
import os
import time
from pathlib import Path
from typing import Dict, List, Optional
from .config import Config
from .models import Planet, Coords, Resources, EspionageReport
from . import utils

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sync_playwright = None
    PWTimeout = Exception


# ---------------------------------------------------------------------------
SEL = {
    # :text-is (texto EXACTO), NO :has-text: el lobby actual de GameForge tiene un <li>
    # CONTENEDOR con el texto combinado "Iniciar sesiónRegistrarse"; :has-text lo casa
    # PRIMERO y clicar ese contenedor NO activa el login (el <li> real de la pestaña no
    # tiene clase distintiva). Resultado del bug: #loginForm nunca aparecía y el bot
    # terminaba en el formulario de REGISTRO. El texto exacto casa el <li> real por idioma.
    "lobby_login_tab": "li:text-is('Log in'), li:text-is('Iniciar sesión'), li:text-is('Login'), li:text-is('Anmelden'), li:text-is('Se connecter'), li:text-is('Zaloguj')",
    # Scoped a #loginForm: la portada tiene TAMBIÉN un form de registro con
    # input[name=email]/password; sin el scope, fill() podía rellenar el de registro.
    "lobby_email":     "#loginForm input[name='email']",
    "lobby_pass":      "#loginForm input[name='password']",
    "lobby_login_btn": "#loginForm button[type=submit]",
    "play_button":     "button.btn-primary, button.btn-success, button.button-default, button",
    "accounts_url":    "https://lobby.ogame.gameforge.com/en_GB/accounts",
    "resource_metal":  "#resources_metal",
    "resource_crystal":"#resources_crystal",
    "resource_deut":   "#resources_deuterium",
    "resource_energy": "#resources_energy",
    "planet_list":     "#planetList .smallplanet",
    "captcha":         "#captcha, .challenge, iframe[src*='challenge']",
}

# URL real del juego: index.php?page=ingame&component=X
PAGE = {
    "overview":   "index.php?page=ingame&component=overview",
    "supplies":   "index.php?page=ingame&component=supplies",
    "facilities": "index.php?page=ingame&component=facilities",
    "research":   "index.php?page=ingame&component=research",
    "shipyard":   "index.php?page=ingame&component=shipyard",
    "defenses":   "index.php?page=ingame&component=defenses",
    "lfbuildings": "index.php?page=ingame&component=lfbuildings",
    "lfresearch":  "index.php?page=ingame&component=lfresearch",
    "fleet":      "index.php?page=ingame&component=fleetdispatch",
    "galaxy":     "index.php?page=ingame&component=galaxy",
    "movement":   "index.php?page=ingame&component=movement",
    "messages":   "index.php?page=ingame&component=messages",
    "event_list": "index.php?page=componentOnly&component=eventList",
}

# Nombre interno → ID de tecnología de OGame
TECH_IDS: Dict[str, int] = {
    # Supplies
    "metal_mine": 1,       "crystal_mine": 2,     "deut_synth": 3,
    "solar_plant": 4,      "fusion_reactor": 12,  "metal_storage": 22,
    "crystal_storage": 23, "deut_tank": 24,
    # Facilities
    "robotics_factory": 14, "shipyard": 21,        "research_lab": 31,
    "nanite_factory": 15,
    # Research
    "energy_tech": 113,    "laser_tech": 120,     "ion_tech": 121,
    "hyperspace_tech": 114,"plasma_tech": 122,    "combustion_drive": 115,
    "impulse_drive": 117,  "hyperspace_drive": 118,"espionage_tech": 106,
    "computer_tech": 108,  "astrophysics": 124,   "weapons_tech": 109,
    "shielding_tech": 110, "armor_tech": 111,
    # Ships
    "small_cargo": 202,    "large_cargo": 203,    "light_fighter": 204,
    "heavy_fighter": 205,  "cruiser": 206,        "battleship": 207,
    "colony_ship": 208,    "recycler": 209,       "espionage_probe": 210,
    "solar_satellite": 212,"bomber": 211,         "destroyer": 213,
    "deathstar": 214,      "battlecruiser": 215,  "reaper": 218,
    "pathfinder": 219,
    # Defenses
    "rocket_launcher": 401, "light_laser": 402,   "heavy_laser": 403,
    "gauss_cannon": 404,    "ion_cannon": 405,    "plasma_turret": 406,
    "small_shield_dome": 407, "large_shield_dome": 408,
}
# Inverso: tech_id str → name
_ID_TO_NAME = {str(v): k for k, v in TECH_IDS.items()}

# Nombres de naves tal como salen en los tooltips de movimientos del juego → clave
# interna. Solo naves que vuelan (los recursos de la carga se descartan al no estar aquí).
# ponytail: tabla ES+EN; si tus tooltips salen en otro idioma, añade sus nombres aquí.
_SHIP_NAME_ALIASES = {
    # Español
    "nave pequena de carga": "small_cargo",
    "nave grande de carga": "large_cargo",
    "gran nave de carga": "large_cargo",
    "cazador ligero": "light_fighter",
    "cazador pesado": "heavy_fighter",
    "crucero": "cruiser",
    "nave de batalla": "battleship",
    "nave colonizadora": "colony_ship",
    "reciclador": "recycler",
    "sonda de espionaje": "espionage_probe",
    "bombardero": "bomber",
    "satelite solar": "solar_satellite",
    "destructor": "destroyer",
    "estrella de la muerte": "deathstar",
    "acorazado": "battlecruiser",
    "segador": "reaper",
    "explorador": "pathfinder",
    # English
    "small cargo": "small_cargo",
    "large cargo": "large_cargo",
    "light fighter": "light_fighter",
    "heavy fighter": "heavy_fighter",
    "cruiser": "cruiser",
    "battleship": "battleship",
    "colony ship": "colony_ship",
    "recycler": "recycler",
    "espionage probe": "espionage_probe",
    "bomber": "bomber",
    "solar satellite": "solar_satellite",
    "destroyer": "destroyer",
    "deathstar": "deathstar",
    "death star": "deathstar",
    "battlecruiser": "battlecruiser",
    "reaper": "reaper",
    "pathfinder": "pathfinder",
}

_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def _ship_name_to_key(name: str) -> Optional[str]:
    """Normaliza el nombre de una nave del juego (ES/EN) a su clave interna, o None."""
    if not name:
        return None
    norm = " ".join(name.strip().lower().translate(_ACCENTS).split())
    return _SHIP_NAME_ALIASES.get(norm)

# Misiones OGame: nombre interno → código numérico
MISSION_CODES: Dict[str, int] = {
    "attack": 1, "group_attack": 2, "transport": 3, "deploy": 4,
    "hold": 5, "espionage": 6, "colonize": 7, "harvest": 8,
    "destroy": 9, "expedition": 15,
}
# Tipo de destino: nombre → código
TYPE_CODES: Dict[str, int] = {"planet": 1, "debris": 2, "moon": 3}

# ---------------------------------------------------------------------------
# Los bloques JS grandes viven en ogbot/js/<nombre>.js (uno por page.evaluate);
# se cargan del disco una sola vez por proceso y se cachean.
_JS_DIR = Path(__file__).parent / "js"
_JS_CACHE: Dict[str, str] = {}


def _load_js(name: str) -> str:
    """Devuelve el contenido de ogbot/js/<name>.js (cacheado)."""
    js = _JS_CACHE.get(name)
    if js is None:
        js = (_JS_DIR / f"{name}.js").read_text(encoding="utf-8")
        _JS_CACHE[name] = js
    return js


class GameClient:
    def __init__(self, cfg: Config, logger):
        self.cfg = cfg
        self.log = logger
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.lobby_url = "https://lobby.ogame.gameforge.com/"
        self._planet_cache: List[Planet] = []
        self._canary_done = False          # canario de selectores: una vez por proceso
        self._last_captcha_alert = 0.0     # epoch de la última alerta de CAPTCHA por Telegram
        self.player_id: Optional[str] = None    # meta ogame-player-id (C5); brain lo persiste en state.json
        self.player_name: Optional[str] = None  # meta ogame-player-name (C5)

    # ------------------------------------------------------------------
    def start(self):
        if sync_playwright is None:
            raise RuntimeError("pip install playwright && playwright install chromium")
        self._pw = sync_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            f"--remote-debugging-port={getattr(self.cfg, 'cdp_port', 9222)}",
        ]
        # En Docker (Chromium como root) hacen falta estos flags
        if os.environ.get("OGBOT_CHROMIUM_NO_SANDBOX"):
            launch_args += ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        launch_kwargs = {"headless": self.cfg.headless, "args": launch_args}
        # Proxy opcional (p.ej. residencial) para evitar el bloqueo de login por IP de VPS
        proxy_server = getattr(self.cfg, "proxy_server", "") or os.environ.get("OGBOT_PROXY", "")
        if proxy_server:
            proxy = {"server": proxy_server}
            if getattr(self.cfg, "proxy_username", ""):
                proxy["username"] = self.cfg.proxy_username
                proxy["password"] = getattr(self.cfg, "proxy_password", "")
            launch_kwargs["proxy"] = proxy
            self.log.info("Usando proxy para el navegador: %s", proxy_server)
        self.browser = self._pw.chromium.launch(**launch_kwargs)
        state = "ogame_session.json"
        self.context = self.browser.new_context(
            storage_state=state if os.path.exists(state) else None,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"),
            viewport={"width": 1366, "height": 768},
        )
        self.page = self.context.new_page()

    def stop(self):
        try:
            if self.context:
                self.context.storage_state(path="ogame_session.json")
        except Exception:
            pass
        for obj in (self.browser, self._pw):
            try:
                obj.close() if hasattr(obj, "close") else obj.stop()
            except Exception:
                pass

    def save_screenshot(self, path: str) -> bool:
        """Guarda una captura PNG de la página actual en `path` (crea el directorio
        padre). Devuelve False si el navegador no está arrancado o falla la captura."""
        if self.page is None:
            return False
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self.page.screenshot(path=path, full_page=False)
            return True
        except Exception as e:
            self.log.warning("No pude guardar captura en %s: %s", path, e)
            return False

    def _delay(self):
        utils.human_delay(self.cfg.min_action_delay_s, self.cfg.max_action_delay_s)

    def _has_captcha(self) -> bool:
        try:
            return self.page.locator(SEL["captcha"]).count() > 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    #  Login
    # ------------------------------------------------------------------
    def _is_game_url(self, url: str) -> bool:
        return ("index.php" in url or "/game/" in url) and "lobby" not in url

    def _all_pages(self):
        """
        TODAS las páginas del navegador (de todos los contextos), no solo self.context.
        Al pulsar Jugar, OGame puede abrir el juego en otra ventana/contexto; si solo
        miramos self.context.pages no lo vemos y creemos que no entró.
        """
        pages = []
        try:
            for ctx in self.browser.contexts:
                try:
                    pages.extend(ctx.pages)
                except Exception:
                    continue
        except Exception:
            pass
        if not pages:
            try:
                pages = list(self.context.pages)
            except Exception:
                pages = []
        return pages

    def _accept_cookies(self):
        """Acepta el banner de cookies de GameForge si está presente. Sin sesión guardada
        el banner aparece siempre y puede interceptar los clics del login."""
        for sel in ("button:has-text('Aceptar cookies')", "button:has-text('Accept cookies')",
                    "button:has-text('ACEPTAR')", ".cookiebanner__button--accept"):
            try:
                b = self.page.locator(sel).first
                if b.count() > 0 and b.is_visible():
                    b.click()
                    self.log.info("Banner de cookies aceptado.")
                    self._delay()
                    return
            except Exception:
                continue

    def _dismiss_login_error(self) -> bool:
        """
        Cierra el diálogo de GameForge 'Ha ocurrido un error al iniciar sesión.
        Inténtalo de nuevo' si aparece. Devuelve True si detectó/cerró el error.
        """
        for pg in self._all_pages():
            try:
                if pg.is_closed():
                    continue
                err = pg.locator(
                    "text=/error al iniciar sesión|error occurred while logging|"
                    "Inténtalo de nuevo|try again/i"
                ).first
                if err.count() > 0 and err.is_visible():
                    for sel in ["button:has-text('OK')", "button:has-text('Aceptar')",
                                "a:has-text('OK')", "a:has-text('Aceptar')",
                                "button:has-text('Cerrar')", ".btn-primary", "button.close"]:
                        try:
                            b = pg.locator(sel).first
                            if b.count() > 0 and b.is_visible():
                                b.click()
                                self.log.info("Cerrado diálogo de error de login de GameForge.")
                                return True
                        except Exception:
                            continue
                    return True  # error detectado aunque no se pudiera pulsar OK
            except Exception:
                continue
        return False

    def _enter_game_via_play(self, play_locator) -> bool:
        """
        Pulsa Jugar y entra al juego (headless/servidor). Escucha todas las pestañas,
        tolera que la de /loading se cierre, y REINTENTA si GameForge devuelve el
        error 'Ha ocurrido un error al iniciar sesión. Inténtalo de nuevo'.
        """
        self.log.info("Entrando al juego (modo robusto v4)...")
        new_pages = []

        def _on_page(pg):
            try:
                new_pages.append(pg)
            except Exception:
                pass

        try:
            self.context.on("page", _on_page)
        except Exception:
            pass

        try:
            for attempt in range(1, 4):
                # Click normal y, si no dispara en headless, click por JS de respaldo
                try:
                    play_locator.first.click(timeout=8000)
                except Exception as e:
                    self.log.debug("Click en Jugar falló (%s); probando por JS.", e)
                    try:
                        play_locator.first.evaluate("el => el.click()")
                    except Exception:
                        pass

                # Esperar (hasta 45s) a que alguna pestaña llegue al juego, o detectar el error
                deadline = time.time() + 45
                target = None
                login_error = False
                while time.time() < deadline and target is None:
                    for pg in self._all_pages() + list(new_pages):
                        try:
                            if pg.is_closed():
                                continue
                            if self._is_game_url(pg.url):
                                target = pg
                                break
                        except Exception:
                            continue
                    if target is None:
                        if self._dismiss_login_error():
                            login_error = True
                            break  # GameForge falló: salir a reintentar
                        time.sleep(1.0)

                if target is not None:
                    try:
                        target.wait_for_load_state("domcontentloaded", timeout=20000)
                    except Exception:
                        pass
                    self.page = target
                    try:
                        self.context = target.context  # el juego puede estar en otro contexto
                    except Exception:
                        pass
                    self.log.info("Entrado al juego: %s", self.page.url)
                    for pg in self._all_pages():
                        try:
                            if pg is not self.page and not pg.is_closed():
                                pg.close()
                        except Exception:
                            pass
                    if "/game/" in self.page.url:
                        actual_base = self.page.url.split("/game/")[0] + "/"
                        if self.cfg.server_url.rstrip("/") != actual_base.rstrip("/"):
                            self.log.warning(
                                "Servidor real detectado en vivo: %s (configurado: %s). Actualizando server_url.",
                                actual_base, self.cfg.server_url)
                            self.cfg.server_url = actual_base
                    return True

                self.log.warning("Play intento %d/3: %s. Reintentando...", attempt,
                                 "GameForge devolvió error de login" if login_error else "no se entró al juego")
                time.sleep(3)
                fresh = self._find_play_button()
                if fresh is not None:
                    play_locator = fresh

            urls = []
            for pg in self._all_pages():
                try:
                    if not pg.is_closed():
                        urls.append(pg.url)
                except Exception:
                    pass
            self.log.warning(
                "Play: no se entró al juego tras 3 intentos (pestañas: %s). Si corres en un "
                "servidor/VPS, GameForge suele bloquear el login por IP; configura un proxy "
                "residencial (proxy_server / OGBOT_PROXY).", urls)
            return False
        except Exception as e:
            self.log.warning("Error en _enter_game_via_play: %s", e)
            return False
        finally:
            try:
                self.context.remove_listener("page", _on_page)
            except Exception:
                pass

    def _find_play_button(self):
        """
        Busca el botón de 'Play'/'Jugar' correcto.
        Primero intenta buscarlo específicamente para el universo configurado.
        Si no, busca por palabras clave comunes o cualquier botón de acción visible.
        """
        # 1. Por universo específico
        if self.cfg.universe:
            # Buscar en contenedores del lobby (cards, rows, celdas)
            for container in [".accountCard", "tr", ".account-row", ".account-item", "div.account-list-item", "div"]:
                try:
                    locator = self.page.locator(container, has_text=self.cfg.universe)
                    if locator.count() > 0:
                        for btn_sel in ["button.btn-primary", "button.btn-success", "button.button-default", "button", "a.btn"]:
                            btn = locator.locator(btn_sel).first
                            if btn.count() > 0 and btn.is_visible():
                                self.log.info("Encontrado botón Jugar para el universo '%s' (%s > %s)", self.cfg.universe, container, btn_sel)
                                return btn
                except Exception:
                    continue

        # 2. Por texto (Play, Jugar, Spielen, Jouer...)
        for text in ["Play", "Jugar", "Spielen", "Jouer", "Grać", "Gioca"]:
            for btn_sel in [f"button:has-text('{text}')", f"a:has-text('{text}')"]:
                try:
                    btn = self.page.locator(btn_sel).first
                    if btn.count() > 0 and btn.is_visible():
                        self.log.info("Encontrado botón de entrada general por texto '%s'", text)
                        return btn
                except Exception:
                    continue

        # 3. Fallback: cualquier botón primario o similar
        for btn_sel in [SEL["play_button"], "button.btn-primary", "button.btn-success", "button.button-default", "button"]:
            try:
                btn = self.page.locator(btn_sel).first
                if btn.count() > 0 and btn.is_visible():
                    self.log.info("Encontrado botón de entrada por fallback de selector '%s'", btn_sel)
                    return btn
            except Exception:
                continue

        return None

    def _wait_for_human_check(self, where: str) -> bool:
        """
        Verificación humana (CAPTCHA) en el login. En modo headless/Docker no hay
        navegador visible, PERO el visor 'Bot en Directo' del panel web sí permite
        verla y resolverla (clic/teclado vía CDP). Espera hasta
        login_human_check_timeout_s sondeando si ya se resolvió, y avisa al usuario.
        """
        timeout_s = float(getattr(self.cfg, "login_human_check_timeout_s", 300) or 300)
        self.log.warning(
            "Verificación humana (CAPTCHA) %s. Abre el panel web -> pestaña "
            "'Bot en Directo' y resuélvela. Esperando hasta %.0f min...",
            where, timeout_s / 60.0)
        # Alerta por Telegram con captura (máx. una cada 10 min para no inundar el chat).
        try:
            if (getattr(self.cfg, "telegram_token", "") and getattr(self.cfg, "telegram_chat_id", "")
                    and time.time() - self._last_captcha_alert > 600):
                self._last_captcha_alert = time.time()
                caption = (f"Verificación humana requerida ({where}). Entra al visor "
                           "del navegador en la GUI para resolverla.")
                shot = ""
                try:
                    os.makedirs("errors", exist_ok=True)
                    shot = f"errors/captcha_{int(time.time())}.png"
                    self.page.screenshot(path=shot)
                except Exception:
                    shot = ""
                sent = False
                if shot:
                    sent = bool(utils.send_telegram_photo(
                        self.cfg.telegram_token, self.cfg.telegram_chat_id, shot,
                        caption="🤖 OGBot: " + utils.tg_escape(caption),
                        logger=self.log, block=True))
                if not sent:
                    utils.send_telegram_message(
                        self.cfg.telegram_token, self.cfg.telegram_chat_id,
                        "🤖 OGBot: " + utils.tg_escape(caption), logger=self.log)
        except Exception:
            pass
        start = time.time()
        while time.time() - start < timeout_s:
            time.sleep(5)
            try:
                if not self._has_captcha():
                    self.log.info("Verificación humana resuelta; continúo con el login.")
                    return True
            except Exception:
                pass
        self.log.warning("Se agotó la espera de verificación humana (%s).", where)
        return False

    def selector_canary(self) -> list[str]:
        """Comprueba en la página de overview que los selectores base del bot siguen
        existiendo. Devuelve la lista de selectores que fallan (vacía si todo OK)."""
        self._goto("overview")
        broken: list[str] = []
        for sel in (
            "#resources",
            "#planetList .smallplanet",
            "#eventboxContent, #eventListWrap, #eventContent",
            "a[href*='component=fleetdispatch']",
            ".OGameClock",
        ):
            try:
                if self.page.locator(sel).count() == 0:
                    broken.append(sel)
            except Exception:
                broken.append(sel)
        return broken

    def _run_selector_canary_once(self) -> None:
        """Canario tras el primer login exitoso del proceso. Nunca rompe el login."""
        if self._canary_done or not getattr(self.cfg, "enable_selector_canary", False):
            return
        self._canary_done = True  # una sola vez por proceso, aunque falle
        try:
            broken = self.selector_canary()
            if not broken:
                self.log.info("Canario de selectores: OK (todos presentes).")
                return
            msg = ("GameForge puede haber cambiado la interfaz; el bot puede fallar en: "
                   + ", ".join(broken))
            self.log.warning("Canario de selectores: %s", msg)
            if getattr(self.cfg, "telegram_token", "") and getattr(self.cfg, "telegram_chat_id", ""):
                utils.send_telegram_message(
                    self.cfg.telegram_token, self.cfg.telegram_chat_id,
                    "⚠️ OGBot: " + utils.tg_escape(msg), logger=self.log)
        except Exception as e:
            self.log.debug("Canario de selectores falló (ignorado): %s", e)

    def _read_player_identity(self) -> None:
        """Lee los meta ogame-player-id / ogame-player-name del DOM del juego (C5).
        Repuebla self.player_id/self.player_name en cada login; nunca rompe el login."""
        try:
            pid, pname = self.page.evaluate("""() => {
                const g = n => { const m = document.querySelector('meta[name="' + n + '"]');
                                 return m ? m.content : null; };
                return [g('ogame-player-id'), g('ogame-player-name')];
            }""")
        except Exception:
            pid = pname = None
        # No machacar con None una identidad ya conocida por un fallo puntual de lectura
        self.player_id = pid or self.player_id
        self.player_name = pname or self.player_name
        if pid:
            self.log.info("Identidad del jugador: %s (id=%s)", self.player_name, self.player_id)

    def login(self) -> bool:
        ok = self._do_login()
        if ok:
            self._read_player_identity()
            self._run_selector_canary_once()
            # Persistir la sesión YA (no solo en stop()): un OOM/kill del contenedor perdía
            # las cookies y forzaba login completo con riesgo de CAPTCHA.
            try:
                self.context.storage_state(path="ogame_session.json")
            except Exception:
                pass
        return ok

    def _do_login(self) -> bool:
        # Determinar URL de cuentas localizada según país
        country = (self.cfg.country or "en").lower()
        locales = {
            "es": "es_ES", "de": "de_DE", "en": "en_GB", "fr": "fr_FR",
            "it": "it_IT", "pl": "pl_PL", "ru": "ru_RU", "tr": "tr_TR"
        }
        locale = locales.get(country, f"{country}_{country.upper()}")
        accounts_url = f"https://lobby.ogame.gameforge.com/{locale}/accounts"

        # 1) Intentar sesión guardada directamente en el servidor del juego
        if self.cfg.server_url:
            try:
                self.page.goto(self.cfg.server_url, wait_until="domcontentloaded", timeout=20000)
                self._delay()
                if self._is_game_url(self.page.url):
                    self.log.info("Sesión restaurada (sin login).")
                    return True
            except Exception:
                pass

        # 2) Ir a la página de cuentas del lobby
        self.log.info("Navegando a cuentas del lobby...")
        self.page.goto(accounts_url, wait_until="domcontentloaded", timeout=30000)
        self._delay()

        if self._is_game_url(self.page.url):
            self.log.info("Sesión restaurada vía lobby.")
            return True

        self._accept_cookies()

        # 3) Play/Jugar SOLO si seguimos en la página de cuentas (= hay sesión Gameforge).
        #    Sin sesión, /accounts redirige a la portada de login y el fallback genérico de
        #    _find_play_button acababa clicando el "Registrarse" verde: 3 intentos x 45 s
        #    perdidos y errores de validación en pantalla antes de llegar al login real.
        if "/accounts" in self.page.url:
            play = self._find_play_button()
            if play:
                self.log.info("Sesión activa. Entrando al universo...")
                if self._enter_game_via_play(play):
                    return True
                self.log.warning("Play no llevó al juego; intentando login completo.")

        # 4) Login completo con credenciales
        if self._has_captcha():
            self._wait_for_human_check("antes del login")

        try:
            # Portada LOCALIZADA (no lobby_url a secas): sin locale, GameForge elige idioma
            # por Accept-Language del navegador (inglés en headless) y la página "cambiaba
            # de idioma" a mitad de login. Así siempre aterrizamos en el idioma configurado.
            self.page.goto(f"https://lobby.ogame.gameforge.com/{locale}/",
                           wait_until="domcontentloaded", timeout=30000)
            self._accept_cookies()
            self.page.wait_for_selector(SEL["lobby_login_tab"], state="visible", timeout=15000)
            self.page.click(SEL["lobby_login_tab"])
            self.page.wait_for_selector("#loginForm", state="visible", timeout=10000)
            # strip(): un espacio colado en el YAML/env hace fallar el login con error genérico
            self.page.fill(SEL["lobby_email"], (self.cfg.username or "").strip())
            self.page.fill(SEL["lobby_pass"], self.cfg.password)
            self._delay()
            self.page.click(SEL["lobby_login_btn"])
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            self._delay()

            if self._has_captcha():
                self._wait_for_human_check("tras introducir credenciales")

            # Ir explícitamente a cuentas: si el login falló seguimos en la portada,
            # y buscar ahí el botón Jugar repetiría el falso positivo de "Registrarse".
            self.page.goto(accounts_url, wait_until="domcontentloaded", timeout=30000)
            self._delay()
            if "/accounts" in self.page.url:
                play2 = self._find_play_button()
                if play2 and self._enter_game_via_play(play2):
                    return True
            self.log.error("Login completo: no se alcanzó la URL del juego (URL: %s). "
                           "¿Credenciales incorrectas o CAPTCHA pendiente?", self.page.url)
            return False
        except PWTimeout:
            self.log.error("Timeout en login (URL: %s).", self.page.url)
            return False

    # ------------------------------------------------------------------
    #  Navegación interna
    # ------------------------------------------------------------------
    def _goto(self, page_key: str, planet: "Planet | None" = None):
        url = f"{self.cfg.server_url.rstrip('/')}/game/{PAGE[page_key]}"
        if planet is not None:
            pid = planet.id.replace("planet-", "")
            url += f"&cp={pid}"
        for attempt in range(3):
            try:
                self.page.goto(url, wait_until="domcontentloaded")
                # Si OGame nos ha sacado al lobby, reloginear automáticamente
                if not self._is_game_url(self.page.url):
                    self.log.warning("Sesión caducada (URL=%s); reloginando...", self.page.url)
                    if self.login():
                        self.page.goto(url, wait_until="domcontentloaded")
                    else:
                        raise RuntimeError("Re-login fallido tras sesión caducada.")
                break
            except Exception as e:
                err_str = str(e)
                self.log.warning("Error en navegación _goto (intento %d/3): %s", attempt + 1, err_str)
                if ("Target page" in err_str or "context or browser has been closed" in err_str
                        or "TargetClosedError" in type(e).__name__) and attempt < 2:
                    self.log.warning("Página cerrada (intento %d/2); recuperando sesión...", attempt + 1)
                    try:
                        self.page = self.context.new_page()
                    except Exception:
                        pass
                    if self.login():
                        continue  # reintenta el goto con la sesión restaurada
                    raise
                elif attempt < 2:
                    # Otros errores temporales (como navegación interrumpida, ERR_ABORTED, etc.): esperar y reintentar
                    time.sleep(2)
                    continue
                else:
                    raise
        self._delay()

    def _wait_tech(self, timeout: int = 8000):
        try:
            self.page.wait_for_selector("li[data-technology]", timeout=timeout)
        except Exception:
            pass

    def _is_error_page(self) -> bool:
        """Devuelve True si OGame muestra 'An error has occured' en la página actual."""
        try:
            text = self.page.locator("body").inner_text(timeout=2000)
            return "An error has occured" in text or "An error has occurred" in text
        except Exception:
            return False

    def _get_page_token(self) -> Optional[str]:
        """Extrae el token CSRF de la página actual (necesario para builds via URL)."""
        try:
            return self.page.evaluate("""() => {
                if (window.token) return String(window.token);
                const m = document.querySelector('meta[name="token"]');
                if (m) return m.getAttribute('content');
                const i = document.querySelector('input[name="token"]');
                if (i) return i.value;
                for (const s of document.querySelectorAll('script')) {
                    const txt = s.textContent || '';
                    const t = txt.match(/"token"\s*:\s*"(\w{16,})"|var token\s*=\s*"(\w{16,})"/);
                    if (t) return t[1] || t[2];
                }
                return null;
            }""")
        except Exception:
            return None

    def _is_build_queue_active(self) -> bool:
        try:
            return bool(self.page.evaluate(_load_js("build_queue_active")))
        except Exception:
            return False

    def _is_build_queue_active_from_overview(self) -> bool:
        """Comprueba la cola desde la página de overview (más fiable). Debe llamarse en overview."""
        try:
            return bool(self.page.evaluate(_load_js("build_queue_overview")))
        except Exception:
            return False

    def _get_build_queue_remaining_seconds(self) -> int:
        try:
            return int(self.page.evaluate(_load_js("build_queue_remaining")) or 0)
        except Exception:
            return 0

    def _get_build_queue(self) -> List[str]:
        try:
            tids = self.page.evaluate(_load_js("build_queue")) or []
            return [_ID_TO_NAME[str(tid)] for tid in tids if str(tid) in _ID_TO_NAME]
        except Exception:
            return []

    def _is_lf_queue_active(self) -> bool:
        try:
            return bool(self.page.evaluate(_load_js("lf_queue_active")))
        except Exception:
            return False

    def _click_upgrade(self, tech_id: int) -> bool:
        """Intenta hacer clic en el botón de upgrade/build de tech_id."""
        btn = self._find_upgrade_locator(tech_id)
        if btn is not None:
            try:
                btn.click()
                return True
            except Exception:
                pass
        # Fallback JS: click en cualquier botón no deshabilitado dentro del li
        try:
            js = f"""() => {{
                const li = document.querySelector("li[data-technology='{tech_id}']");
                if (!li) return null;
                const btn = li.querySelector(
                    'button:not([disabled]):not(.disabled):not(.tpl_busy),' +
                    'a.btn_blue:not(.disabled),.btnBuy:not([disabled])'
                );
                if (btn && btn.offsetParent !== null) {{ btn.click(); return btn.className; }}
                // último recurso: primer botón visible
                for (const el of li.querySelectorAll('button, a[href="#"]')) {{
                    if (el.offsetParent !== null && !el.disabled) {{ el.click(); return 'any:' + el.className; }}
                }}
                return null;
            }}"""
            result = self.page.evaluate(js)
            if result:
                self.log.debug("JS click tech_id=%d: %s", tech_id, result)
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    #  Lectura de estado
    # ------------------------------------------------------------------
    def read_resources(self) -> Resources:
        def num(sel):
            try:
                txt = (self.page.locator(sel).first.get_attribute("data-raw")
                       or self.page.locator(sel).first.inner_text())
                return utils.parse_localized_number(txt)
            except Exception:
                return 0.0
        return Resources(num(SEL["resource_metal"]),
                         num(SEL["resource_crystal"]),
                         num(SEL["resource_deut"]),
                         num(SEL["resource_energy"]))

    def read_planets(self) -> List[Planet]:
        self._goto("overview")
        planets: List[Planet] = []
        try:
            js = r"""() => {
                const results = [];
                document.querySelectorAll('#planetList .smallplanet').forEach(el => {
                    const id = el.getAttribute('id') || '';
                    const coordsEl = el.querySelector('.planet-koords');
                    const coordsTxt = coordsEl ? coordsEl.textContent.trim() : '';
                    const nameEl = el.querySelector('.planet-name');
                    const name = nameEl ? nameEl.textContent.trim() : '';
                    
                    const moonEl = el.querySelector('a.moonlink');
                    let moonId = null;
                    if (moonEl) {
                        const href = moonEl.getAttribute('href') || '';
                        const match = href.match(/cp=(\d+)/);
                        if (match) {
                            moonId = "planet-" + match[1];
                        }
                    }
                    results.push({ id, name, coordsTxt, moonId });
                });
                return results;
            }"""
            rows = self.page.evaluate(js) or []
            for row in rows:
                coords_txt = row["coordsTxt"].strip("[] ")
                if not coords_txt or ":" not in coords_txt:
                    continue
                g, s, p = [int(x) for x in coords_txt.split(":")]
                planet_obj = Planet(id=row["id"], name=row["name"], coords=Coords(g, s, p, type="planet"))
                if row["moonId"]:
                    planet_obj.has_moon = True
                    planet_obj.moon = Planet(
                        id=row["moonId"],
                        name=f"{row['name']} (Luna)",
                        coords=Coords(g, s, p, type="moon")
                    )
                planets.append(planet_obj)
        except Exception as e:
            self.log.warning("No pude leer planetas: %s", e)
        self._planet_cache = planets
        return planets

    def _planet_by_coords(self, coords: Coords) -> "Optional[Planet]":
        """Busca el planeta o luna en caché por coordenadas y tipo; devuelve el primero si no hay match."""
        for p in self._planet_cache:
            if p.coords.tuple() == coords.tuple():
                if getattr(coords, "type", "planet") == "moon":
                    if p.has_moon and p.moon:
                        return p.moon
                return p
        return self._planet_cache[0] if self._planet_cache else None

    def _click_first(self, selectors: list, timeout_ms: int = 3000) -> bool:
        """Hace clic en el primer selector visible de la lista."""
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0:
                    loc.wait_for(state="visible", timeout=timeout_ms)
                    loc.click()
                    return True
            except Exception:
                continue
        return False

    def _fill_first(self, selectors: list, value: str) -> bool:
        """Rellena el primer input visible de la lista y dispara eventos."""
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.fill(value)
                    loc.press("Tab")
                    return True
            except Exception:
                continue
        return False

    def _read_tech_page(self, component: str, planet: "Planet | None" = None) -> Dict[str, int]:
        """Lee {name: level} de una página de componente usando data-technology."""
        self._goto(component, planet)
        self._wait_tech()
        try:
            raw = self.page.evaluate(_load_js("read_tech"))  # {tech_id_str: level}
            return {_ID_TO_NAME[k]: v for k, v in raw.items() if k in _ID_TO_NAME}
        except Exception as e:
            self.log.warning("Error leyendo %s: %s", component, e)
            return {}

    def read_planet_state(self, planet: Planet) -> None:
        """Lee recursos, edificios, naves, defensas y cola de construcción."""
        self._goto("overview", planet)
        planet.resources = self.read_resources()
        # Cola de construcción: lo más fiable es leerla desde overview
        planet.building_in_progress = self._is_build_queue_active_from_overview()
        planet.building_remaining_seconds = self._get_build_queue_remaining_seconds() if planet.building_in_progress else 0
        planet.building_queue = self._get_build_queue() if planet.building_in_progress else []

        # Para lunas, saltar "supplies", solo leer "facilities"
        components = ["facilities"]
        if planet.coords.type == "planet":
            components.append("supplies")

        for comp in components:
            data = self._read_tech_page(comp, planet)
            planet.buildings.update(data)

        ships_data = self._read_tech_page("shipyard", planet)
        planet.ships.update(ships_data)

        defense_data = self._read_tech_page("defenses", planet)
        planet.defenses.update(defense_data)

        # Detectar cola de Formas de vida (solo en planetas)
        if planet.coords.type == "planet" and planet.lifeform_available:
            self._goto("lfbuildings", planet)
            if self._is_error_page():
                self.log.info("Formas de vida no disponibles en %s (universo sin LF o no desbloqueado).", planet.coords)
                planet.lifeform_available = False
                planet.lifeform_in_progress = False
            else:
                try:
                    self._wait_tech(timeout=5000)
                    planet.lifeform_in_progress = self._is_lf_queue_active()
                except Exception:
                    planet.lifeform_in_progress = False
        else:
            planet.lifeform_available = False
            planet.lifeform_in_progress = False

        self.log.info(
            "Ubicación %s: M=%.0f C=%.0f D=%.0f | minas M%d/C%d/D%d | "
            "def RL=%d | cola=%s lf=%s",
            planet.coords,
            planet.resources.metal, planet.resources.crystal, planet.resources.deut,
            planet.lvl("metal_mine"), planet.lvl("crystal_mine"), planet.lvl("deut_synth"),
            planet.defenses.get("rocket_launcher", 0),
            f"ocupada ({', '.join(planet.building_queue)} - {planet.building_remaining_seconds}s)" if planet.building_in_progress else "libre",
            "ocupada" if planet.lifeform_in_progress else "libre",
        )

    def read_planet_light(self, planet: Planet) -> None:
        """
        Lectura LIGERA: solo lo que cambia en vivo cada ciclo -> recursos, cola de
        construcción (con su tiempo restante) y naves. NO lee edificios/defensas:
        esos los aporta la caché de niveles del cerebro. Mucho menos navegación.
        """
        self._goto("overview", planet)
        planet.resources = self.read_resources()
        planet.building_in_progress = self._is_build_queue_active_from_overview()
        planet.building_remaining_seconds = self._get_build_queue_remaining_seconds() if planet.building_in_progress else 0
        planet.building_queue = self._get_build_queue() if planet.building_in_progress else []

        ships_data = self._read_tech_page("shipyard", planet)
        planet.ships.update(ships_data)

        self.log.info(
            "Ubicación %s (ligero): M=%.0f C=%.0f D=%.0f | cola=%s",
            planet.coords,
            planet.resources.metal, planet.resources.crystal, planet.resources.deut,
            f"ocupada ({', '.join(planet.building_queue)} - {planet.building_remaining_seconds}s)" if planet.building_in_progress else "libre",
        )

    def read_research(self) -> Dict[str, int]:
        """Lee niveles de investigación actuales."""
        return self._read_tech_page("research")

    # ------------------------------------------------------------------
    #  Acciones (respetan dry_run)
    # ------------------------------------------------------------------
    def _act(self, description: str) -> bool:
        if self.cfg.dry_run:
            self.log.info("[DRY-RUN] %s", description)
            return True
        self.log.info("[ACCION] %s", description)
        return False

    def claim_directive_rewards(self) -> int:
        """Recoge las recompensas de misiones/directivas completadas. Devuelve cuántas.

        Las directivas viven en un overlay (component=ipioverview) que se abre desde el
        menú; hay que operarlo dentro del juego para que sus handlers AJAX estén ligados.
        Gate barato: el menú solo muestra .ipiHintCollect cuando hay algo que recoger.

        ponytail: el elemento que recoge es .ipiTaskItemTrack (data-target=taskid); si el
        click no cambia el estado a 'collected' abortamos en vez de contar en falso. Techo:
        ese selector es la única incógnita; upgrade path = ajustarlo si el juego lo cambia.
        """
        if self._act("Recoger recompensas de directivas"):
            return 0
        self._goto("overview")
        try:
            pending = int(self.page.evaluate(
                "() => { const s = document.querySelector('#ipimenucomponent .ipiHintCollect');"
                " return s ? (parseInt(s.textContent, 10) || 0) : 0; }") or 0)
        except Exception:
            pending = 0
        if pending <= 0:
            self.log.info("Directivas: sin recompensas pendientes.")
            return 0
        self.log.info("Directivas: %d recompensa(s) pendiente(s); abriendo el panel...", pending)
        # Abrir el overlay via el propio handler del enlace (evita checks de visibilidad).
        try:
            opened = self.page.evaluate(
                "() => { const a = document.querySelector('#ipiInnerMenuContentHolder');"
                " if (!a) return false; a.click(); return true; }")
            if not opened:
                self.log.warning("Directivas: no encontré el enlace del menú (#ipiInnerMenuContentHolder).")
                return 0
            self.page.wait_for_selector("#ipiOverviewTasklist .ipiTaskItem", timeout=8000)
        except Exception as e:
            self.log.warning("Directivas: no se pudo abrir el panel (%s); se reintentará.", e)
            return 0

        claimed = 0
        for _ in range(30):  # tope duro (tareas * capítulos); evita bucles si algo no "seca"
            try:
                res = self.page.evaluate(_load_js("claim_directive_reward"))
            except Exception as e:
                self.log.debug("Directivas: error en el panel: %s", e)
                break
            if not res:
                break
            time.sleep(1.5)  # esperar el re-render AJAX del panel
            if res.get("action") == "chapter":
                continue
            tid = res.get("id") or ""
            # Verificar que se recogió de verdad (el estado deja de ser 'completed').
            try:
                state = self.page.evaluate(
                    "(id) => { const t = document.querySelector(`.ipiTaskItem[data-taskid='${id}']`);"
                    " return t ? t.getAttribute('data-state') : 'gone'; }", tid)
            except Exception:
                state = "gone"
            if state == "completed":
                self.log.warning("Directivas: la tarea %s no se recogió (revisar selector de "
                                 "recogida); abortando para no contar en falso.", tid)
                break
            claimed += 1
            self.log.info("Recompensa de directiva recogida (tarea %s).", tid)
        if claimed:
            self.log.info("Directivas: %d recompensa(s) recogida(s).", claimed)
        return claimed

    def rename_planet(self, planet: "Planet", new_name: str) -> bool:
        """Renombra un planeta/colonia. Devuelve True si el nombre quedó aplicado.

        Usa el form 'planetMaintenance' (a.openPlanetRenameGiveupBox -> #planetName ->
        submit 'Renombrar', que hace ajaxFormSubmit a page=planetRename). NUNCA toca el
        form de abandonar (#planetMaintenanceDelete, exige contraseña). Verificado contra
        el HTML vivo del diálogo (2026-07-08).
        """
        if self._act(f"Renombrar {planet.coords} -> {new_name}"):
            return True
        self._goto("overview", planet)
        try:
            opened = self.page.evaluate(
                "() => { const a = document.querySelector('a.openPlanetRenameGiveupBox');"
                " if (!a) return false; a.click(); return true; }")
            if not opened:
                self.log.warning("Renombrado: no encontré el enlace abandonar/renombrar.")
                return False
            self.page.wait_for_selector("#planetMaintenance #planetName", timeout=8000)
        except Exception as e:
            self.log.warning("Renombrado: no se pudo abrir el diálogo (%s).", e)
            return False
        # Rellenar SOLO el form de rename y disparar su onsubmit (copia a #newPlanetName
        # y hace ajaxFormSubmit a planetRename). Clic en el submit del propio form.
        try:
            fired = self.page.evaluate(
                "(name) => {"
                " const inp = document.querySelector('#planetMaintenance #planetName');"
                " const btn = document.querySelector('#planetMaintenance input[type=submit]');"
                " if (!inp || !btn) return false;"
                " inp.value = name; inp.dispatchEvent(new Event('input', {bubbles:true}));"
                " btn.click(); return true; }", new_name)
        except Exception as e:
            self.log.warning("Renombrado: fallo al enviar el form (%s).", e)
            return False
        if not fired:
            self.log.warning("Renombrado: form de rename no encontrado en el diálogo.")
            return False
        time.sleep(2.0)  # esperar el ajaxFormSubmit y el callback planetRenamed
        # Verificación POSITIVA: el nombre en el menú de planetas debe ser el nuevo.
        try:
            actual = self.page.evaluate(
                "(pid) => { const el = document.querySelector('#' + pid + ' .planet-name');"
                " return el ? el.textContent.trim() : null; }", planet.id)
        except Exception:
            actual = None
        if actual == new_name:
            planet.name = new_name
            return True
        self.log.warning("Renombrado: no se pudo confirmar el nuevo nombre de %s (leído: %r).",
                         planet.coords, actual)
        return False

    def _find_upgrade_locator(self, tech_id: int):
        """Devuelve el primer locator visible del botón de upgrade, o None."""
        for sel in [
            f"li[data-technology='{tech_id}'] button.upgrade",
            f"li[data-technology='{tech_id}'] button[data-action='build']",
            f"li[data-technology='{tech_id}'] .btn_action_row.upgrade",
            f"li[data-technology='{tech_id}'] .btnBuy",
            f"li[data-technology='{tech_id}'] a.btn_blue",
            f"li[data-technology='{tech_id}'] button:not([disabled])",
        ]:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    def build(self, planet: Planet, component: str, what: str) -> bool:
        if self._act(f"Construir {what} en {planet.coords}"):
            return True
        tech_id = TECH_IDS.get(what)
        if tech_id is None:
            self.log.warning("Sin tech_id para edificio: %s", what)
            return False
        self._goto(component, planet)
        self._wait_tech()
        if self._is_build_queue_active():
            self.log.info("Cola de construcción ocupada en %s; saltando.", planet.coords)
            planet.building_in_progress = True
            return False
        btn = self._find_upgrade_locator(tech_id)
        if btn is None:
            self.log.warning("No pude construir %s en %s: botón no encontrado.", what, planet.coords)
            return False
        btn.click()
        # Esperar a que el servidor procese la petición AJAX
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            time.sleep(2)
        # Verificar que OGame no devolvió una página de error
        if self._is_error_page():
            self.log.warning("OGame reportó error al construir %s en %s.", what, planet.coords)
            return False
        self.log.info("Construcción iniciada: %s lv%d en %s",
                      what, planet.lvl(what) + 1, planet.coords)
        planet.building_in_progress = True
        return True

    def research(self, tech: str, planet: "Planet | None" = None) -> bool:
        if self._act(f"Investigar {tech}" + (f" desde {planet.coords}" if planet else "")):
            return True
        tech_id = TECH_IDS.get(tech)
        if tech_id is None:
            self.log.warning("Sin tech_id para investigación: %s", tech)
            return False
        self._goto("research", planet)
        self._wait_tech()
        if self._click_upgrade(tech_id):
            self.log.info("Investigación iniciada: %s", tech)
            return True
        else:
            self.log.warning("No pude investigar %s: botón no encontrado.", tech)
            return False

    def _build_units_ui(self, tech_id: int, amount: int) -> bool:
        """
        Construye barcos o defensas usando la UI estándar de OGame (3 pasos):
        1. Clic en la tarjeta del item (li[data-technology=X]).
        2. Clic y rellenado en el input de cantidad del panel de detalle.
        3. Clic en el botón verde "Construir".
        """
        try:
            # 1. Clic en la tarjeta del item
            card = self.page.locator(f"li[data-technology='{tech_id}']").first
            if card.count() == 0:
                self.log.warning("No se encontró la tarjeta del elemento con ID=%d", tech_id)
                return False
            
            card.click()
            time.sleep(0.5)  # Esperar a que se abra la sección de detalles
            
            # 2. Localizar el input de cantidad
            input_selectors = [
                "#technologydetails input#number",
                "#technologydetails input[name='kolonne']",
                ".technology-details input#number",
                ".technology-details input[name='kolonne']",
                "input#number",
                "input[name='kolonne']",
                "input#build_amount",
                ".technology-details input.amount",
                "input#amount",
                ".detail-overlay input",
                "input[name='amount']",
                "input.amount"
            ]
            
            qty_input = None
            for sel in input_selectors:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    qty_input = loc
                    break
            
            if not qty_input:
                self.log.warning("No se encontró el campo de entrada para cantidad.")
                return False
            
            # Clicar en el input para enfocarlo
            qty_input.click()
            # Rellenar la cantidad (escribir)
            qty_input.fill(str(amount))
            time.sleep(0.3)
            
            # 3. Localizar y clicar el botón de construir
            btn_selectors = [
                ".technology-details button.upgrade",
                "button.upgrade",
                "button.build",
                "#build_button",
                ".build_button",
                "button:has-text('Construir')",
                "button.btnBuy"
            ]
            
            build_btn = None
            for sel in btn_selectors:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible() and not loc.is_disabled():
                    build_btn = loc
                    break
            
            if not build_btn:
                self.log.warning("No se encontró el botón de construir activo.")
                return False
                
            build_btn.click()
            
            # Esperar a que se procese
            try:
                self.page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                time.sleep(1)
                
            return not self._is_error_page()
            
        except Exception as e:
            self.log.warning("Excepción durante la fabricación en interfaz: %s", e)
            return False

    def build_defense(self, planet: Planet, what: str, amount: int) -> bool:
        if self._act(f"Construir defensa {amount}x {what} en {planet.coords}"):
            return True
        tech_id = TECH_IDS.get(what)
        if tech_id is None:
            self.log.warning("Sin tech_id para defensa: %s", what)
            return False
        self._goto("defenses", planet)
        self._wait_tech()

        # Usar nuevo método UI unificado
        if self._build_units_ui(tech_id, amount):
            self.log.info("Defensa iniciada: %dx %s en %s", amount, what, planet.coords)
            return True
        else:
            self.log.warning("No pude construir defensa %s en %s.", what, planet.coords)
            return False

    def build_lifeform(self, planet: Planet) -> bool:
        """
        Navega a la página de Formas de vida e intenta construir el primer edificio
        disponible (asequible y sin cola activa). Devuelve True si inició algo.
        """
        if self._act(f"Forma de vida en {planet.coords}"):
            return True
        if not planet.lifeform_available:
            return False
        self._goto("lfbuildings", planet)
        if self._is_error_page():
            self.log.info("Formas de vida no disponibles en %s; desactivando.", planet.coords)
            planet.lifeform_available = False
            return False
        try:
            self._wait_tech(timeout=6000)
        except Exception:
            self.log.debug("Página de Forma de vida no disponible en %s.", planet.coords)
            return False
        if self._is_lf_queue_active():
            self.log.debug("Cola Forma de vida ocupada en %s.", planet.coords)
            planet.lifeform_in_progress = True
            return False
        # Buscar el primer botón de upgrade activo (no deshabilitado)
        js = """() => {
            for (const li of document.querySelectorAll('li[data-technology]')) {
                const btn = li.querySelector('button.upgrade:not([disabled]):not(.disabled)');
                if (btn) return li.getAttribute('data-technology');
            }
            return null;
        }"""
        try:
            tid = self.page.evaluate(js)
        except Exception:
            return False
        if not tid:
            self.log.debug("Sin edificios de Forma de vida disponibles en %s.", planet.coords)
            return False
        if self._click_upgrade(int(tid)):
            try:
                self.page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                time.sleep(2)
            if self._is_error_page():
                self.log.warning("OGame reportó error al construir forma de vida en %s.", planet.coords)
                return False
            self.log.info("Forma de vida: construcción iniciada (tech=%s) en %s", tid, planet.coords)
            planet.lifeform_in_progress = True
            return True
        return False

    def build_ships(self, planet: Planet, ship: str, amount: int) -> bool:
        if self._act(f"Fabricar {amount}x {ship} en {planet.coords}"):
            return True
        tech_id = TECH_IDS.get(ship)
        if tech_id is None:
            self.log.warning("Sin tech_id para nave: %s", ship)
            return False
        self._goto("shipyard", planet)
        self._wait_tech()
        
        # Usar nuevo método UI unificado
        if self._build_units_ui(tech_id, amount):
            self.log.info("Fabricación iniciada: %dx %s en %s", amount, ship, planet.coords)
            return True
        else:
            self.log.warning("No pude fabricar %s en %s.", ship, planet.coords)
            return False

    # ------------------------------------------------------------------
    #  Lectura de informes de espionaje
    # ------------------------------------------------------------------
    def read_all_spy_reports(self) -> Dict[str, EspionageReport]:
        """
        Navega a mensajes y extrae todos los informes de espionaje visibles.
        Devuelve {coords_str: EspionageReport} donde coords_str = "G:S:P".
        """
        for url_suffix in ["&tab=20", ""]:
            try:
                self.page.goto(
                    f"{self.cfg.server_url.rstrip('/')}/game/"
                    f"index.php?page=ingame&component=messages{url_suffix}",
                    wait_until="domcontentloaded", timeout=15000,
                )
                self._delay()
                break
            except Exception:
                continue
        # Intentar hacer clic en el tab de espionaje si existe
        self._click_first([
            "a[href*='tab=20']", "li[data-tab='20'] a",
            ".tabLabel:has-text('Espionaje') a",
            ".tabLabel:has-text('Espionage') a",
        ], timeout_ms=2000)
        time.sleep(1)
        try:
            raw_list = self.page.evaluate(_load_js("all_spy_reports")) or []
        except Exception as e:
            self.log.debug("Error leyendo informes espionaje: %s", e)
            return {}
        reports: Dict[str, EspionageReport] = {}
        for raw in raw_list:
            try:
                g, s, p = raw["galaxy"], raw["system"], raw["position"]
                key = f"{g}:{s}:{p}"
                if key in reports:
                    continue  # el primero en la lista es el más reciente
                res = raw.get("resources", {})
                reports[key] = EspionageReport(
                    coords=Coords(g, s, p),
                    player_name=raw.get("player_name", ""),
                    is_inactive=raw.get("is_inactive", False),
                    resources=Resources(
                        metal=float(res.get("metal", 0)),
                        crystal=float(res.get("crystal", 0)),
                        deut=float(res.get("deut", 0)),
                    ),
                    fleet={k: int(v) for k, v in raw.get("fleet", {}).items()},
                    defense={k: int(v) for k, v in raw.get("defense", {}).items()},
                    timestamp=time.time(),
                    activity_mins=raw.get("activity"),
                )
            except Exception as e:
                self.log.debug("Error parseando informe espionaje: %s", e)
        self.log.info("Informes de espionaje leídos: %d", len(reports))
        return reports

    def read_spy_report(self, target: Coords) -> Optional[EspionageReport]:
        """Lee el informe de espionaje más reciente para las coordenadas dadas."""
        reports = self.read_all_spy_reports()
        return reports.get(f"{target.galaxy}:{target.system}:{target.position}")

    # ------------------------------------------------------------------
    #  Espionaje
    # ------------------------------------------------------------------
    def espionage(self, target: Coords, probes: int = 4) -> Optional[EspionageReport]:
        """Envía sondas, espera retorno y devuelve el informe parseado."""
        if self.cfg.dry_run:
            self.log.info("[DRY-RUN] Espiar %s con %d sondas", target, probes)
            return None
        # Reutilizar informe reciente (< 30 min)
        report = self.read_spy_report(target)
        if report and (time.time() - report.timestamp) < 1800:
            self.log.info("Informe reciente para %s (reutilizando).", target)
            return report
        # Enviar sondas
        origin = self._planet_cache[0].coords if self._planet_cache else None
        if not origin:
            self.log.warning("espionage: sin planeta origen en caché.")
            return None
        if not self.send_fleet(origin, target, {"espionage_probe": probes},
                               mission="espionage"):
            self.log.warning("espionage: no se pudieron enviar sondas a %s.", target)
            return None
        # Esperar retorno (máx 3 min)
        self.log.info("Sondas enviadas -> %s. Esperando retorno (máx 3 min)...", target)
        coord_str = f"{target.galaxy}:{target.system}:{target.position}"
        deadline = time.time() + 180
        while time.time() < deadline:
            time.sleep(10)
            mvs = self.read_movements()
            # Igualdad exacta: 'in' casaba '1:2:3' con '11:2:34'
            in_flight = any(
                m.get("mission") in ("6", "espionage", "Espionage") and
                coord_str == m.get("destination", "").strip()
                for m in mvs
            )
            if not in_flight:
                time.sleep(3)
                break
        return self.read_spy_report(target)

    # ------------------------------------------------------------------
    #  Envío de flota (OGame Redesign: 2 pasos — naves → destino+misión)
    # ------------------------------------------------------------------
    def _set_speed(self, sp_val: int) -> bool:
        """
        Establece la velocidad de la flota (1 a 10, que corresponde a 10% a 100%).
        Utiliza clics reales de Playwright para asegurar la compatibilidad con los frameworks SPA de OGame.
        """
        if sp_val > 10 and sp_val <= 100:
            sp_val = int(sp_val / 10)
            
        selectors = [
            f".steps .step[data-step='{sp_val}']",
            f".steps [data-step='{sp_val}']",
            f"[data-step='{sp_val}'].step",
            f"ul.speed li.step{sp_val * 10}",
            f"ul.speed li[data-value='{sp_val * 10}']",
            f".speedLinks a.step{sp_val * 10}",
            f"a[onclick*='selectSpeed({sp_val * 10})']",
            f"a[onclick*='selectSpeed({sp_val})']",
        ]
        
        for sel in selectors:
            try:
                locator = self.page.locator(sel).first
                if locator.count() > 0:
                    locator.click(timeout=500)
                    self.log.debug("Velocidad %d%% establecida con éxito usando: %s", sp_val * 10, sel)
                    return True
            except Exception:
                pass
                
        try:
            res = self.page.evaluate(_load_js("set_speed"), str(sp_val))
            if res:
                self.log.debug("Velocidad %d%% establecida con JS: %s", sp_val * 10, res)
                return True
        except Exception:
            pass
            
        try:
            slider = self.page.locator("#speedPercent, input[name='speed'], input[type='range']").first
            if slider.count() > 0:
                slider.fill(str(sp_val))
                slider.dispatch_event("input")
                slider.dispatch_event("change")
                self.log.debug("Velocidad %d%% establecida vía input range fill", sp_val * 10)
                return True
        except Exception:
            pass
            
        return False

    def send_fleet(self, origin: Coords, destination: Coords, ships: Dict[str, int],
                   mission: str, resources = None,
                   speed_percent: float = 1.0, hold_hours: float = 0.0,
                   target_duration_s: Optional[float] = None,
                   max_round_trip_s: Optional[float] = None) -> bool:
        desc = (f"Flota {ships} {origin}->{destination} mision={mission} "
                f"vel={int(speed_percent * 100)}%")
        if self._act(desc):
            return True

        self.last_flight_seconds = None  # tiempo de vuelo real (un sentido) leído del juego
        origin_planet = self._planet_by_coords(origin)
        self._goto("fleet", origin_planet)
        try:
            self.page.wait_for_selector(
                "li[data-technology], #fleetdispatchscreen, .fleet-dispatch-ships",
                timeout=10000,
            )
        except Exception:
            pass
        self._delay()

        # Si resources es "all", leer los recursos del planeta desde la página actual
        resources_to_carry = None
        if resources == "all":
            resources_to_carry = self.read_resources()
            self.log.info("send_fleet: leídos recursos para fleetsave: %s", resources_to_carry)
        elif resources is not None:
            resources_to_carry = resources

        # ── Paso 1: seleccionar naves ──────────────────────────────────
        any_selected = False
        if not ships:
            # Si ships está vacío, intentar seleccionar todas las naves del planeta (ej: para fleetsave)
            self.log.info("send_fleet: seleccionando todas las naves del planeta.")
            clicked_all = self._click_first([
                "#sendall", "a#sendall", "a.allShips", "#sendallBtn", "a#sendallBtn",
                "a:has-text('Todas las naves')", "a:has-text('All ships')",
                "a:has-text('Enviar todas')", "a.allShips:not(.disabled)",
                "a#sendallships"
            ])
            if clicked_all:
                any_selected = True
                self.log.debug("send_fleet: clicado botón 'seleccionar todas las naves'.")
            else:
                self.log.warning("send_fleet: no encontré botón de 'seleccionar todas', intentando por JS...")
                try:
                    any_selected = self.page.evaluate(_load_js("select_all_ships"))
                except Exception as e:
                    self.log.warning("send_fleet: error al seleccionar todas las naves via JS: %s", e)
        else:
            # Usamos el setter nativo de HTMLInputElement para compatibilidad React/SPA
            for ship_name, amount in ships.items():
                if amount <= 0:
                    continue
                tech_id = TECH_IDS.get(ship_name)
                if not tech_id:
                    self.log.warning("send_fleet: tech_id desconocido para %s.", ship_name)
                    continue
                # Intento 1: Playwright fill (requiere is_visible)
                filled = self._fill_first([
                    f"li[data-technology='{tech_id}'] input.amount",
                    f"li[data-technology='{tech_id}'] input[name='amount']",
                    f"#ship_{tech_id}",
                    f"input[data-ship='{tech_id}']",
                    f"li[data-technology='{tech_id}'] input",
                ], str(amount))
                # Intento 2: JS con setter nativo React-compatible
                if not filled:
                    try:
                        js = f"""() => {{
                            const tid = {tech_id};
                            const val = '{amount}';
                            const nativeSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value'
                            ).set;
                            const setVal = (el) => {{
                                nativeSetter.call(el, val);
                                el.dispatchEvent(new Event('input', {{bubbles:true}}));
                                el.dispatchEvent(new Event('change', {{bubbles:true}}));
                                el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles:true}}));
                                el.blur();
                            }};
                            for (const id of ['ship_' + tid, 'ship' + tid]) {{
                                const el = document.getElementById(id);
                                if (el) {{ setVal(el); return 'id:' + id; }}
                            }}
                            const li = document.querySelector('li[data-technology="' + tid + '"]');
                            if (!li) return null;
                            const inp = li.querySelector('input');
                            if (inp) {{ setVal(inp); return 'li'; }}
                            return null;
                        }}"""
                        result = self.page.evaluate(js)
                        filled = result is not None
                        if filled:
                            self.log.debug("send_fleet: nave %s seleccionada via JS (%s).", ship_name, result)
                    except Exception:
                        pass
                if filled:
                    try:
                        self.page.locator(f"li[data-technology='{tech_id}'] input").first.press("Tab")
                    except Exception:
                        pass
                    any_selected = True
                else:
                    self.log.warning(
                        "send_fleet: no encontré input para %s (id=%d). "
                        "¿Tienes esa nave en el planeta?", ship_name, tech_id
                    )

        if not any_selected:
            try:
                techs = self.page.evaluate(
                    "() => [...document.querySelectorAll('li[data-technology]')]"
                    ".map(l => l.getAttribute('data-technology'))"
                )
                self.log.warning("send_fleet: sin naves; techs en página=%s", techs)
            except Exception:
                pass
            return False

        # Botón paso 1→2
        # Esperar a que el botón de continuar esté listo (no tenga la clase 'off')
        try:
            self.page.wait_for_selector("#continueToFleet2:not(.off)", timeout=4000)
        except Exception:
            if self.page.locator("#continueToFleet2").count() > 0:
                self.log.warning("send_fleet: el botón continuar #continueToFleet2 está desactivado (clase 'off').")
                return False

        if not self._click_first([
            "#continueToFleet2", "a#continueToFleet2", "input#continueToFleet2",
            "a:has-text('Siguiente')", "button:has-text('Continue')",
            ".btn_blue_middle", "a.btn_blue_small",
        ]):
            self.log.warning("send_fleet: no encontré botón continuar (paso 1→2).")
            return False

        # ── Paso 2: destino + misión + envío ──────────────────────────
        # En OGame Redesign el paso 2 es la pantalla final (no hay paso 3 separado).
        # Esperamos cualquier indicador de la pantalla de destino o de envío.
        try:
            self.page.wait_for_selector(
                "input#galaxy, input[name='galaxy'], #sendFleet, a:has-text('Enviar')",
                timeout=8000,
            )
        except Exception:
            time.sleep(2)
        self._delay()

        # Destino
        try:
            ok = self.page.evaluate(f"""() => {{
                const setVal = (id, val) => {{
                    const el = document.getElementById(id) || document.querySelector('input[name="' + id + '"]');
                    if (el) {{
                        el.focus();
                        const nativeSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        nativeSetter.call(el, String(val));
                        el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                        el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles: true, keyCode: 13, which: 13}}));
                        el.blur();
                        return true;
                    }}
                    return false;
                }};
                const g = setVal("galaxy", "{destination.galaxy}");
                const s = setVal("system", "{destination.system}");
                const p = setVal("position", "{destination.position}");
                return g && s && p;
            }}""")
            if not ok:
                raise RuntimeError("No se encontraron todos los campos de coordenadas en el DOM")
            self.log.info("Coordenadas establecidas vía JS: %s", destination)
        except Exception as e:
            self.log.warning("Fallo al rellenar coordenadas con JS: %s. Reintentando con Playwright...", e)
            self._fill_first(["input#galaxy", "input[name='galaxy']"], str(destination.galaxy))
            self._fill_first(["input#system", "input[name='system']"], str(destination.system))
            self._fill_first(["input#position", "input[name='position']"], str(destination.position))

        # Esperar a que los inputs de coordenadas se procesen y OGame termine su validación AJAX
        time.sleep(1.5)

        dest_type = TYPE_CODES.get(destination.type, 1)
        btn_id = ""
        if dest_type == 1:
            btn_id = "#pbutton"
        elif dest_type == 2:
            btn_id = "#dbutton"
        elif dest_type == 3:
            btn_id = "#mbutton"

        selectors = []
        if btn_id:
            selectors.extend([btn_id, f"a{btn_id}", f"div{btn_id}", f"button{btn_id}"])
        selectors.extend([
            f"input[name='type'][value='{dest_type}']",
            f"a[data-type='{dest_type}']",
            f"label[for='type{dest_type}']",
        ])

        clicked = False
        for attempt in range(3):
            if self._click_first(selectors):
                # Verificar si el botón tiene la clase 'selected' (e.g. debris_selected)
                time.sleep(0.5)
                if btn_id:
                    try:
                        classes = self.page.locator(btn_id).first.get_attribute("class") or ""
                        if "selected" in classes:
                            clicked = True
                            self.log.info("Tipo de destino %s seleccionado con éxito.", destination.type)
                            break
                    except Exception:
                        pass
                else:
                    clicked = True
                    break
            self.log.warning("Intento %d: Fallo al seleccionar tipo de destino %s. Reintentando...", attempt + 1, destination.type)
            time.sleep(1.0)

        if not clicked:
            self.log.warning("Fallo definitivo al seleccionar tipo de destino %s", destination.type)

        # Misión (radio button o enlace) - Seleccionada PRIMERO para habilitar el cálculo de velocidad y duración
        mission_code = MISSION_CODES.get(mission, 1)
        self._click_first([
            f"#missionButton{mission_code}",
            f"a#missionButton{mission_code}",
            f"[data-mission='{mission_code}']",
            f"label[for='mission{mission_code}']",
            f"input[name='mission'][value='{mission_code}']",
            f"#mission{mission_code}",
            f"a.mission{mission_code}",
            f"a[onclick*='mission={mission_code}']",
        ])
        time.sleep(1.0)  # Espera corta para que OGame cargue la duración inicial tras seleccionar misión

        # Velocidad
        if target_duration_s is not None and target_duration_s > 0:
            best_sp = 1  # 10% por defecto
            durations = {}
            
            # Esperar a que la duración del vuelo se calcule (sea distinta de 0:00:00)
            start_wait_dur = time.time()
            initial_dur = "0:00:00 h"
            while time.time() - start_wait_dur < 4.0:
                val = self.page.evaluate("""() => {
                    const el = document.getElementById('duration') || document.querySelector('.duration') || document.querySelector('#duration .value') || document.querySelector('.flightTime');
                    return el ? el.textContent.trim() : null;
                }""")
                if val and val not in ("0:00:00", "0:00:00 h", "00:00:00", "00:00:00 h", "0:00:00h", "00:00:00h"):
                    initial_dur = val
                    break
                time.sleep(0.2)
            
            last_duration = initial_dur
            
            for sp in range(1, 11):
                try:
                    self._set_speed(sp)
                    
                    # Esperar hasta 600ms a que la duración cambie respecto a last_duration
                    start_w = time.time()
                    current_dur = None
                    while time.time() - start_w < 0.6:
                        dur_str = self.page.evaluate("""() => {
                            const el = document.getElementById('duration') || document.querySelector('.duration') || document.querySelector('#duration .value') || document.querySelector('.flightTime');
                            return el ? el.textContent.trim() : null;
                        }""")
                        if dur_str and dur_str != last_duration:
                            current_dur = dur_str
                            break
                        time.sleep(0.05)
                    
                    if not current_dur:
                        # Fallback a leer lo que haya
                        current_dur = dur_str
                        
                    if current_dur:
                        last_duration = current_dur
                        seconds = self._parse_duration_to_seconds(current_dur)
                        if seconds:
                            durations[sp] = seconds
                except Exception as e:
                    self.log.debug("Error leyendo duración para velocidad %d: %s", sp, e)
            
            self.log.info("Duraciones leídas en pantalla por velocidad: %s (objetivo: %s s)", 
                           {f"{k*10}%": v for k, v in durations.items()}, int(target_duration_s))
            
            # Buscar el porcentaje más alto donde la duración sea >= target_duration_s
            valid_speeds = [sp for sp, sec in durations.items() if sec >= target_duration_s]
            if valid_speeds:
                best_sp = max(valid_speeds)
            elif durations:
                best_sp = min(durations.keys())
                
            self.log.info("Seleccionando velocidad dinámica optimizada: %d%%", best_sp * 10)
            self._set_speed(best_sp)
        else:
            sp_val = max(1, min(10, round(speed_percent * 10)))
            self._set_speed(sp_val)

        time.sleep(0.5)

        # Leer la duración REAL de vuelo (un sentido) directamente de la página de envío
        try:
            dur_txt = self.page.evaluate("""() => {
                const el = document.getElementById('duration') || document.querySelector('#duration .value') || document.querySelector('.duration') || document.querySelector('.flightTime');
                return el ? el.textContent.trim() : null;
            }""")
            secs = self._parse_duration_to_seconds(dur_txt) if dur_txt else None
            if secs and secs > 0:
                self.last_flight_seconds = secs
        except Exception:
            pass

        # Decidir con el tiempo REAL (no estimado): si no vuelve antes del límite
        # (p.ej. el descanso nocturno), cancelar el envío en vez de adivinar.
        if max_round_trip_s is not None and self.last_flight_seconds:
            hold_s = hold_hours * 3600 if mission == "expedition" else 0
            real_round = 2 * self.last_flight_seconds + hold_s
            if real_round > max_round_trip_s:
                self.log.info("Envío %s->%s cancelado: vuelo real %.1f min (ida+vuelta+permanencia) > %.1f min disponibles antes del descanso.",
                              origin, destination, real_round / 60.0, max_round_trip_s / 60.0)
                return False

        # Recursos (transporte/deploy)
        if resources_to_carry and mission in ("transport", "deploy"):
            self._fill_first(["#deuterium", "input[name='deuterium']"],
                             str(int(resources_to_carry.deut)))
            self._fill_first(["#crystal", "input[name='crystal']"],
                             str(int(resources_to_carry.crystal)))
            self._fill_first(["#metal", "input[name='metal']"],
                             str(int(resources_to_carry.metal)))

        # Tiempo de estancia (expedición)
        if hold_hours > 0 and mission == "expedition":
            hold_min = max(1, min(32767, int(hold_hours * 60)))
            self._fill_first(["#holdingtime", "input[name='holdingtime']"], str(hold_min))

        time.sleep(0.5)

        # Enviar flota
        try:
            self.page.wait_for_selector("#sendFleet:not(.off)", timeout=5000)
        except Exception:
            if self.page.locator("#sendFleet").count() > 0:
                self.log.warning("send_fleet: el botón de envío #sendFleet está visible pero desactivado (clase 'off').")
                try:
                    os.makedirs("errors", exist_ok=True)
                    self.page.screenshot(path=f"errors/fleet_send_disabled_{int(time.time())}.png")
                except Exception:
                    pass
                return False

        if not self._click_first([
            "#sendFleet", "input#sendFleet", "button#sendFleet",
            "a:has-text('Enviar Flota')", "a:has-text('Enviar')",
            "button:has-text('Send Fleet')", "button:has-text('Send')",
            ".btn_blue_large", "input[value='Send Fleet']",
        ]):
            self.log.warning("send_fleet: no encontré botón de envío.")
            try:
                os.makedirs("errors", exist_ok=True)
                self.page.screenshot(path=f"errors/fleet_send_{int(time.time())}.png")
                btns = self.page.evaluate(
                    "() => [...document.querySelectorAll('a,button,input[type=submit]')]"
                    ".filter(e=>e.offsetParent!==null)"
                    ".map(e=>e.id+':'+e.className+':'+e.textContent.trim().slice(0,20))"
                )
                self.log.warning("send_fleet: botones visibles=%s", btns[:15])
            except Exception:
                pass
            return False

        # Verificación POSITIVA: un envío correcto navega a component=movement.
        try:
            self.page.wait_for_url("**component=movement**", timeout=8000)
            self.log.info("Flota enviada (confirmado por navegación a movement): %s -> %s (%s)",
                          origin, destination, mission)
            return True
        except Exception:
            pass

        if self._is_error_page():
            self.log.warning("OGame reportó error enviando flota %s->%s.", origin, destination)
            return False

        # Confirmar que la flota SALIÓ de verdad. Un envío correcto abandona la pantalla
        # de despacho (OGame navega a 'movement'); si seguimos viendo #sendFleet visible,
        # no salió (slot lleno, combustible, recálculo o error inline) aunque el clic
        # "funcionara". Evita el falso "Flota enviada".
        time.sleep(1.0)
        try:
            still_on_dispatch = self.page.evaluate("""() => {
                const btn = document.querySelector('#sendFleet');
                return !!(btn && btn.offsetParent !== null);
            }""")
        except Exception as e:
            # Fail-closed: si no podemos verificar el estado, NO damos el envío por bueno.
            self.log.warning("send_fleet: no pude verificar si la flota %s->%s salió (%s); "
                             "la doy por NO enviada.", origin, destination, e)
            try:
                os.makedirs("errors", exist_ok=True)
                self.page.screenshot(path=f"errors/fleet_verify_{int(time.time())}.png")
            except Exception:
                pass
            return False

        if still_on_dispatch:
            err_txt = ""
            try:
                err_txt = self.page.evaluate("""() => {
                    const sel = '#fleetStatusBar, .fleetStatusBar, .error_text, .fleetError, ' +
                                '.error_box, #errorBoxDecision, .alert_box, .noships, .fleet_error';
                    for (const e of document.querySelectorAll(sel)) {
                        const t = (e.textContent || '').trim();
                        if (t) return t.slice(0, 200);
                    }
                    return '';
                }""") or ""
            except Exception:
                pass
            self.log.warning("send_fleet: se hizo clic en enviar pero la flota %s->%s NO salió "
                             "(seguimos en la pantalla de despacho). %s",
                             origin, destination,
                             ("Motivo: " + err_txt) if err_txt else "(sin mensaje inline visible)")
            try:
                os.makedirs("errors", exist_ok=True)
                self.page.screenshot(path=f"errors/fleet_not_sent_{int(time.time())}.png")
            except Exception:
                pass
            return False

        self.log.info("Flota enviada: %s -> %s (%s)", origin, destination, mission)
        return True

    def read_message_reports(self, tab_id: int) -> List[dict]:
        """
        Navega a la pestaña indicada de mensajes y devuelve, por cada mensaje:
            { "id": str, "text": str, "raw": dict }
        donde `raw` contiene todos los atributos data-raw-* hallados en el mensaje
        (cifras fiables, independientes del idioma). `text` es el cuerpo completo
        como respaldo.
        tab_id: 21 (Combates), 22 (Expediciones), 24 (Otros/Reciclaje).
        """
        try:
            self.page.goto(
                f"{self.cfg.server_url.rstrip('/')}/game/"
                f"index.php?page=ingame&component=messages&tab={tab_id}",
                wait_until="domcontentloaded", timeout=15000,
            )
            self._delay()
        except Exception:
            return []

        # Comprobar si la pestaña ya está activa en el DOM
        already_active = self.page.evaluate(f"""() => {{
            const tab = document.querySelector("li[data-tab='{tab_id}'], [data-subtab-id='{tab_id}'], .tabLabel[data-tab='{tab_id}']");
            return tab ? (tab.classList.contains('active') || tab.classList.contains('selected')) : false;
        }}""")

        # Si no está activa, registramos el primer mensaje visible actual para detectar el refresco
        first_id_before = ""
        if not already_active:
            first_id_before = self.page.evaluate("""() => {
                const first = document.querySelector('.msg, li.msg, .message_element, tr.message');
                return first ? (first.getAttribute('data-msg-id') || first.id || '') : '';
            }""")

            # OGame es una SPA: la pestaña no siempre cambia solo con la URL. Hacemos
            # clic explícito en el tab.
            self._click_first([
                f"a[href*='tab={tab_id}']",
                f"li[data-tab='{tab_id}'] a",
                f"li[data-tab='{tab_id}']",
                f"[data-subtab-id='{tab_id}']",
                f".tabLabel[data-tab='{tab_id}']",
            ], timeout_ms=3000)

            # Esperar hasta que el primer mensaje cambie o la lista se vacíe (máximo 4.5 segundos)
            start_time = time.time()
            while time.time() - start_time < 4.5:
                first_id_now = self.page.evaluate("""() => {
                    const first = document.querySelector('.msg, li.msg, .message_element, tr.message');
                    return first ? (first.getAttribute('data-msg-id') || first.id || '') : '';
                }""")
                if first_id_now != first_id_before:
                    break
                time.sleep(0.3)
        
        try:
            self.page.wait_for_selector(
                ".msg, li.msg, .message_element, tr.message", timeout=4000)
        except Exception:
            pass
        time.sleep(1)

        js = """() => {
            const results = [];
            document.querySelectorAll('.msg, li.msg, .message_element, tr.message').forEach(el => {
                const id = el.getAttribute('data-msg-id') || el.id || '';
                if (!id) return;
                const text = el.innerText || '';
                // Recoge todos los atributos data-raw-* del nodo y sus descendientes.
                const raw = {};
                const collect = node => {
                    if (!node.attributes) return;
                    for (const a of node.attributes) {
                        if (a.name.indexOf('data-raw-') === 0) {
                            raw[a.name.slice(9)] = a.value;
                        }
                    }
                };
                collect(el);
                el.querySelectorAll('*').forEach(collect);
                results.push({ id, text, raw });
            });
            return results;
        }"""
        try:
            return self.page.evaluate(js) or []
        except Exception as e:
            self.log.debug("Error leyendo mensajes tab=%d: %s", tab_id, e)
            return []

    def read_message_full(self, raw_id: str) -> str:
        """Abre (expande) un mensaje ya visible en la pestaña de mensajes actual y devuelve su
        texto completo. La fila de la lista solo trae un resumen; el cuerpo (coords del origen,
        % de contra-espionaje, etc.) se carga al hacer clic. Devuelve '' si no se puede abrir.
        Debe llamarse con la pestaña de mensajes ya cargada (p.ej. tras read_message_reports)."""
        sel = f"[data-msg-id='{raw_id}'], [id='{raw_id}']"
        try:
            el = self.page.query_selector(sel)
            if not el:
                return ""
            before = el.inner_text() or ""
            el.click()
            start = time.time()
            while time.time() - start < 3:
                time.sleep(0.3)
                cur = self.page.query_selector(sel)
                txt = (cur.inner_text() if cur else "") or ""
                if len(txt) > len(before) + 15:  # el cuerpo ya cargó
                    return txt
            cur = self.page.query_selector(sel)
            return (cur.inner_text() if cur else before) or before
        except Exception as e:
            self.log.debug("No se pudo abrir el mensaje %s: %s", raw_id, e)
            return ""

    # ------------------------------------------------------------------
    #  Movimientos y escombros
    # ------------------------------------------------------------------
    def read_movements(self, detailed: bool = False) -> List[dict]:
        """Lee movimientos de flota activos.

        detailed=True usa la página de movimientos (component=movement), que SÍ trae el
        desglose por nave de cada flota propia en su tooltip — imprescindible para sumar las
        naves en vuelo (sobre todo expediciones, miles de cargueros). event_list es más
        ligero y muestra los ataques entrantes (lo usamos para el escape), pero no incluye
        esa composición, así que con él las expediciones cuentan 0 naves.
        """
        self._goto("movement" if detailed else "event_list")
        try:
            self.page.wait_for_selector(
                ".eventFleet, tr.flightEventRow, .fleetDetails, .fleet_row",
                timeout=5000,
            )
        except Exception:
            pass
        try:
            data = self.page.evaluate(_load_js("read_movements")) or []
        except Exception as e:
            self.log.debug("Error leyendo movimientos: %s", e)
            return []
        # Diagnóstico opcional: vuelca el HTML crudo de las filas de flota para depurar el
        # parseo (tipo luna/planeta, reversal/hora de vuelta). Se activa con la variable de
        # entorno OGBOT_DUMP_MOVEMENTS=1 o creando un fichero 'dump_movements.txt' junto al bot
        # (cómodo en Docker); el fichero se borra tras volcar una vez.
        flag_file = "dump_movements.txt"
        if os.environ.get("OGBOT_DUMP_MOVEMENTS") or os.path.exists(flag_file):
            try:
                html = self.page.evaluate(
                    "() => Array.from(document.querySelectorAll("
                    "'.eventFleet, .fleetDetails, .fleet_row, tr.flightEventRow'))"
                    ".map(r => r.outerHTML).join('\\n\\n<!-- ===== fila ===== -->\\n\\n')"
                )
                with open("movement_debug.html", "w", encoding="utf-8") as f:
                    f.write(html or "")
                rows_dumped = (html.count("===== fila =====") + 1) if html else 0
                self.log.info("Volcado de movimientos en movement_debug.html (%d filas, %d parseadas).",
                              rows_dumped, len(data))
                try:
                    if os.path.exists(flag_file):
                        os.remove(flag_file)
                except OSError:
                    pass
            except Exception as e:
                self.log.debug("No se pudo volcar movement_debug.html: %s", e)
        return data

    def read_fleet_slots(self) -> Optional[Dict[str, int]]:
        """Lee el indicador real Flotas: X/Y y Expediciones: X/Y del juego."""
        self._goto("fleet")
        try:
            self.page.wait_for_selector("#fleetdispatchcomponent", timeout=5000)
        except Exception:
            pass
        try:
            data = self.page.evaluate("""() => {
                const r = {};
                const slotsEl = document.querySelector('#slots .fleetSlots .value, #slotValue, .fleetStatus .slot_count');
                if (slotsEl) {
                    const m = slotsEl.textContent.match(/(\\d+)\\s*\\/\\s*(\\d+)/);
                    if (m) { r.fleet_used = parseInt(m[1]); r.fleet_total = parseInt(m[2]); }
                }
                const expeEl = document.querySelector('#slots .expSlots .value, #expeValue, .fleetStatus .expe_count');
                if (expeEl) {
                    const m = expeEl.textContent.match(/(\\d+)\\s*\\/\\s*(\\d+)/);
                    if (m) { r.expe_used = parseInt(m[1]); r.expe_total = parseInt(m[2]); }
                }
                if (!r.fleet_used && r.fleet_used !== 0) {
                    const text = document.body.innerText;
                    const fm = text.match(/Flotas\\s*:?\\s*(\\d+)\\s*\\/\\s*(\\d+)/i) ||
                               text.match(/Fleets\\s*:?\\s*(\\d+)\\s*\\/\\s*(\\d+)/i);
                    if (fm) { r.fleet_used = parseInt(fm[1]); r.fleet_total = parseInt(fm[2]); }
                    const em = text.match(/Expediciones\\s*:?\\s*(\\d+)\\s*\\/\\s*(\\d+)/i) ||
                               text.match(/Expeditions\\s*:?\\s*(\\d+)\\s*\\/\\s*(\\d+)/i);
                    if (em) { r.expe_used = parseInt(em[1]); r.expe_total = parseInt(em[2]); }
                }
                return (r.fleet_used !== undefined || r.expe_used !== undefined) ? r : null;
            }""")
            if data:
                self.log.info("Fleet slots del juego: Flotas %d/%d, Expediciones %d/%d",
                              data.get("fleet_used", 0), data.get("fleet_total", 0),
                              data.get("expe_used", 0), data.get("expe_total", 0))
            return data
        except Exception as e:
            self.log.debug("Error leyendo fleet slots: %s", e)
            return None

    def read_hourly_production(self, planet) -> Optional[Dict[str, int]]:
        """Lee la producción REAL por hora (metal/cristal/deut) de la página de ajustes de
        recursos del planeta. Incluye oficiales, clase, formas de vida e items — más fiable
        que la estimación por niveles de mina. Devuelve None si no se pudo leer."""
        pid = str(getattr(planet, "id", "")).replace("planet-", "").replace("moon-", "")
        if not pid:
            return None
        url = (f"{self.cfg.server_url.rstrip('/')}/game/"
               f"index.php?page=ingame&component=resourcesettings&cp={pid}")
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=12000)
            self._delay()
        except Exception:
            return None
        js = r"""() => {
            const clean = s => parseInt(String(s||'').replace(/[^0-9-]/g, '')) || 0;
            for (const tr of document.querySelectorAll('tr')) {
                const label = (tr.innerText || '').toLowerCase();
                if (label.includes('total por hora') || label.includes('total per hour')
                        || label.includes('gesamt pro stunde')) {
                    const nums = [];
                    tr.querySelectorAll('td, th').forEach(c => {
                        const v = (c.textContent || '').trim();
                        // celdas puramente numéricas (excluye la celda de la etiqueta)
                        if (/[0-9]/.test(v) && !/[a-z]/i.test(v)) nums.push(clean(v));
                    });
                    if (nums.length >= 3) return {metal: nums[0], crystal: nums[1], deut: nums[2]};
                }
            }
            return null;
        }"""
        try:
            data = self.page.evaluate(js)
            if data:
                self.log.info("Producción real %s: M/h=%d C/h=%d D/h=%d",
                              planet.coords, data.get("metal", 0), data.get("crystal", 0), data.get("deut", 0))
            return data
        except Exception as e:
            self.log.debug("Error leyendo producción de %s: %s", getattr(planet, "coords", "?"), e)
            return None

    def read_debris_fields(self, planets_with_recyclers: List[Planet] = None) -> List[dict]:
        """Busca campos de escombros en los sistemas de nuestros planetas y alrededores."""
        targets = planets_with_recyclers if planets_with_recyclers is not None else self._planet_cache
        if not targets:
            return []
        debris_list = []
        seen_systems: set = set()
        
        search_range = getattr(self.cfg, "recycling_system_range", 0)
        min_debris = getattr(self.cfg, "recycling_min_debris", 10000)
        
        for planet in targets:
            c = planet.coords
            min_sys = max(1, c.system - search_range)
            max_sys = min(499, c.system + search_range)
            
            for sys in range(min_sys, max_sys + 1):
                key = (c.galaxy, sys)
                if key in seen_systems:
                    continue
                seen_systems.add(key)
                pid = planet.id.replace("planet-", "")
                url = (f"{self.cfg.server_url.rstrip('/')}/game/"
                       f"index.php?page=ingame&component=galaxy"
                       f"&galaxy={c.galaxy}&system={sys}&cp={pid}")
                self.log.info("Reciclaje: escaneando sistema [%d:%d]...", c.galaxy, sys)
                try:
                    self.page.goto(url, wait_until="domcontentloaded", timeout=10000)
                    self._delay()
                    try:
                        self.page.wait_for_selector(
                            "#galaxytable tr.row, #galaxytable tr.galaxyRow, .galaxyRow, #galaxytable tr", 
                            timeout=5000
                        )
                    except Exception:
                        pass
                    
                    rows = self.page.evaluate(_load_js("galaxy_debris")) or []
                    for row in rows:
                        tot = row.get("metal", 0) + row.get("crystal", 0)
                        if tot >= min_debris:
                            self.log.info("Campo de escombros detectado en %d:%d:%d con %d metal y %d cristal (Total: %d >= min: %d)",
                                          c.galaxy, sys, row['pos'], row['metal'], row['crystal'], tot, min_debris)
                            debris_list.append({
                                "coords": f"{c.galaxy}:{sys}:{row['pos']}",
                                "debris": {
                                    "metal": row["metal"], "crystal": row["crystal"], "deut": 0
                                },
                            })
                        elif tot > 0:
                            self.log.info("Campo de escombros en %d:%d:%d omitido por tamaño insuficiente (%d < min: %d)",
                                          c.galaxy, sys, row['pos'], tot, min_debris)
                except Exception as e:
                    self.log.debug("Error buscando escombros en %d:%d: %s",
                                   c.galaxy, sys, e)
        if debris_list:
            self.log.info("Escombros encontrados: %d campos.", len(debris_list))
        return debris_list

    def _confirm_recall_dialog(self) -> bool:
        """Tras clicar el enlace de regreso, OGame abre un diálogo de confirmación
        ('Retirar flota', botones Sí/No). Pulsa 'Sí'. Reintenta unos segundos por si tarda
        en aparecer. Devuelve True si lo pulsó."""
        js = r"""() => {
            const norm = t => (t || '').trim().toLowerCase().replace(/\s+/g, ' ');
            const YES = ['sí','si','yes','ja','oui'];
            const NO  = ['no','não','nein','non'];
            const vis = el => el && el.offsetParent !== null;
            // El diálogo de confirmación REAL tiene a la vez un botón "Sí" y uno "No" en el
            // mismo contenedor. Exigir ambos evita pulsar cualquier "sí" suelto de la página
            // (lo que pasaba antes, dejando el diálogo abierto).
            const scan = root => {
                let yesEl = null, hasNo = false;
                for (const el of root.querySelectorAll('a,button,input,span,div')) {
                    if (!vis(el)) continue;
                    const t = norm(el.textContent || el.value);
                    if (!yesEl && YES.includes(t)) yesEl = el;
                    else if (NO.includes(t)) hasNo = true;
                }
                return (yesEl && hasNo) ? yesEl : null;
            };
            const click = y => { (y.closest('a,button,[onclick],[role="button"]') || y).click(); };
            const conts = document.querySelectorAll(
                '[class*="confirm"],[id*="confirm"],.ui-dialog,[class*="dialog"],' +
                '[class*="overlay"],[class*="popup"],#box,.modal');
            for (const c of conts) { const y = scan(c); if (y) { click(y); return 'cont'; } }
            const y = scan(document.body);   // último recurso
            if (y) { click(y); return 'body'; }
            return false;
        }"""
        # El diálogo tarda "un par de segundos" en aparecer tras clicar 'devolver'.
        time.sleep(2.0)
        for _ in range(10):
            try:
                r = self.page.evaluate(js)
                if r:
                    self.log.info("Regreso: confirmado 'Sí' en el diálogo (%s).", r)
                    return True
            except Exception as e:
                self.log.debug("Regreso: error buscando el 'Sí': %s", e)
            time.sleep(0.6)
        # No encontrado: volcar los botones visibles para ajustar el selector si hiciera falta.
        try:
            import json as _json
            diag = self.page.evaluate(r"""() => {
                const out = [];
                for (const el of document.querySelectorAll('a,button,input')) {
                    if (el.offsetParent === null) continue;
                    const t = (el.textContent || el.value || '').trim();
                    if (t) out.push({t: t.slice(0,20), c: (el.className||'').slice(0,40), tag: el.tagName});
                }
                return out.slice(0, 30);
            }""")
            self.log.warning("Regreso: NO encontré el 'Sí' de confirmación. Botones visibles: %s",
                             _json.dumps(diag, ensure_ascii=False))
        except Exception:
            pass
        return False

    def _verify_recalled(self, origin: str, destination: str, fid: str = "") -> bool:
        """Confirma que la flota quedó EN RETORNO tras el recall. OGame marca la fila con
        data-return-flight="true"/"1" (y un '(R)' en la misión); el enlace de reversal
        desaparece y la llegada baja. Relee la página de movimiento y empareja por ruta
        (origen+destino), que se conserva aunque el botón de reversal ya no esté."""
        try:
            self._goto("movement")
            time.sleep(2)
            # Coords por celda e igualdad exacta: indexOf sobre el texto de la fila
            # casaba '1:2:3' con '11:2:34'.
            return bool(self.page.evaluate(
                """(args) => {
                    const origin = args[0], destination = args[1];
                    const coord = el => el ? ((el.textContent.match(/\\d+:\\d+:\\d+/) || [''])[0]) : '';
                    const rows = document.querySelectorAll(
                        '.eventFleet, .fleetDetails, .fleet_row, tr.flightEventRow');
                    for (const row of rows) {
                        const oEl = row.querySelector(
                            '.originCoords a, .originCoords .coords, .coordsOrigin a, .coordsOrigin, ' +
                            '.originFleet a, [class*="origin"] a, [class*="orig"] a');
                        const dEl = row.querySelector(
                            '.destinationCoords a, .destinationCoords .coords, .destCoords a, .destCoords .coords, ' +
                            '.coordsDest a, .coordsDest, .destFleet a, [class*="destination"] a, [class*="dest"] a');
                        if (coord(oEl) !== origin || coord(dEl) !== destination) continue;
                        const rrf = row.getAttribute('data-return-flight');
                        if (row.classList.contains('is_return') || rrf === 'true' || rrf === '1')
                            return true;
                    }
                    return false;
                }""", (origin.strip(), destination.strip())))
        except Exception as e:
            self.log.debug("verify_recalled error: %s", e)
            return False

    def recall_fleet(self, origin: str, destination: str, mission: str = "deploy",
                     arrival: int = 0) -> bool:
        desc = f"Retornar flota de {origin} -> {destination} ({mission})"
        if self._act(desc):
            return True

        # Marcador de versión: si NO ves "recall v8" en el log al pedir un regreso, el contenedor
        # corre una imagen vieja -> reconstruye con `docker compose up -d --build`.
        self.log.info("recall v8 (navega al href return=, verifica retorno): buscando %s -> %s", origin, destination)
        self._goto("movement")
        try:
            self.page.wait_for_selector(
                ".eventFleet, .fleetDetails, .fleet_row, #eventContent",
                timeout=5000,
            )
        except Exception:
            pass

        # Empareja por origen+destino+misión y, si hay varias flotas iguales, por la hora de
        # llegada (data-arrival-time) más cercana a la pedida, para recuperar la correcta.
        js_recall = """(args) => {
            // Playwright pasa UN solo argumento: aquí llega el array [origin, destination,
            // mission, arrival]. Antes la función declaraba 4 params y solo el 1º recibía el
            // array entero, así que 'origin' era el array y NINGUNA fila casaba (la causa real
            // de que el regreso nunca funcionara). Hay que destructurar.
            const origin = args[0], destination = args[1], mission = args[2], arrival = args[3] || 0;
            const norm = m => { m = String(m || '').toLowerCase();
                                return (m === 'deploy' || m === '4') ? '4' : m; };
            const want = norm(mission);
            const candidates = [];
            const rows = document.querySelectorAll(
                '.eventFleet, .fleetDetails, .fleet_row, tr.flightEventRow'
            );
            for (const row of rows) {
                try {
                    const origEl = row.querySelector(
                        '.originCoords a, .originCoords .coords, .coordsOrigin a, .coordsOrigin, ' +
                        '.originFleet a, [class*="origin"] a, [class*="orig"] a'
                    );
                    const rowOrigin = origEl ? (origEl.textContent.match(/\\d+:\\d+:\\d+/) || [''])[0] : '';
                    if (rowOrigin !== origin) continue;

                    const destEl = row.querySelector(
                        '.destinationCoords a, .destinationCoords .coords, .destCoords a, .destCoords .coords, ' +
                        '.coordsDest a, .coordsDest, .destFleet a, [class*="destination"] a, [class*="dest"] a'
                    );
                    const rowDest = destEl ? (destEl.textContent.match(/\\d+:\\d+:\\d+/) || [''])[0] : '';
                    if (rowDest !== destination) continue;

                    const rrf = row.getAttribute('data-return-flight');
                    // OJO: NO usar querySelector('.return_flight,...') aquí. En muchos servidores
                    // las filas RECUPERABLES (de ida) muestran la "hora de vuelta si la regresas"
                    // en un elemento con esa clase, lo que marcaba toda flota de ida como retorno
                    // y la saltaba. Una flota que YA regresa no trae botón recallFleet, así que el
                    // propio botón (más abajo) ya la excluye.
                    const is_return = row.classList.contains('is_return') ||
                                   rrf === 'true' || rrf === '1';
                    if (is_return) continue;

                    let rowMission = row.getAttribute('data-mission-type') || '';
                    if (!rowMission) {
                        const mEl = row.querySelector('[data-mission], [data-mission-type], .missionIcon, .icon_movement');
                        if (mEl) {
                            rowMission = mEl.getAttribute('data-mission') ||
                                         mEl.getAttribute('data-mission-type') ||
                                         (mEl.className.match(/mission(\\d+)/) || [])[1] || '';
                        }
                    }
                    // Filtra SIEMPRE por misión cuando se indica (no solo deploy), para no
                    // recuperar otra flota distinta en la misma ruta.
                    if (want && norm(rowMission) && norm(rowMission) !== want) continue;

                    // El regreso REAL de OGame es un GET a
                    // '?...&component=movement&return=FLEETID&token=...'. La fila de detalle
                    // (.fleetDetails) trae ese <a href>; la del event_list a veces SOLO trae
                    // <a class="recallFleet" data-fleet-id> SIN href (se dispara por JS). El código
                    // viejo clicaba esa ancla-sin-href + confirmaba un 'Sí' y la flota NO revertía
                    // (éxito en falso). Preferimos SIEMPRE el href (determinista). Guardamos el
                    // fleet-id para verificar el retorno después.
                    let href = '', fid = '';
                    const aHref = row.querySelector('a[href*="return="]');
                    if (aHref) {
                        href = aHref.href || aHref.getAttribute('href') || '';
                        const mm = href.match(/return=(\\d+)/); if (mm) fid = mm[1];
                    }
                    const aRecall = row.querySelector(
                        'a.recallFleet, a[onclick*="sendRecall"], a[class*="recall"], [class*="reversal"] a');
                    if (!fid && aRecall) fid = aRecall.getAttribute('data-fleet-id') || '';
                    if (!href && !aRecall) continue;   // sin control de regreso en esta fila

                    const rowArr = parseInt(row.getAttribute('data-arrival-time') || '0') || 0;
                    candidates.push({ href: href, fid: fid, arr: rowArr });
                } catch(e) {}
            }
            if (!candidates.length) return { found: false };
            // Preferir candidatos con href navegable (determinista). Entre varios, la llegada
            // más cercana a la pedida desambigua dos flotas en la misma ruta.
            const pool = candidates.some(c => c.href) ? candidates.filter(c => c.href) : candidates;
            let chosen = pool[0];
            if (arrival) {
                let bestDiff = 1e15;
                for (const c of pool) {
                    if (c.arr) { const d = Math.abs(c.arr - arrival); if (d < bestDiff) { bestDiff = d; chosen = c; } }
                }
            }
            return { found: true, href: chosen.href || '', fid: chosen.fid || '' };
        }"""
        try:
            result = self.page.evaluate(js_recall, (origin, destination, mission, int(arrival or 0)))
        except Exception as e:
            self.log.error("Excepción al buscar la flota a retornar: %s", e)
            result = None

        if result and result.get("found"):
            href = (result.get("href") or "").strip()
            fid = (result.get("fid") or "").strip()
            if href:
                # Regreso DETERMINISTA: navegar al enlace real de OGame revierte la flota
                # server-side (sin diálogo). Es lo que hace el juego al pulsar la flecha de vuelta.
                self.log.info("Regreso: navegando al enlace de reversal (fleet %s) %s -> %s",
                              fid or "?", origin, destination)
                try:
                    self.page.goto(href, wait_until="domcontentloaded", timeout=15000)
                except Exception as e:
                    self.log.warning("Regreso %s -> %s: fallo al navegar al enlace: %s",
                                     origin, destination, e)
                    return False
                if self._verify_recalled(origin, destination, fid):
                    self.log.info("Recall CONFIRMADO: la flota %s -> %s ya está de vuelta.",
                                  origin, destination)
                    return True
                self.log.warning("Regreso %s -> %s: navegué al enlace pero la flota NO figura en "
                                 "retorno (data-return-flight).", origin, destination)
                return False
            # Servidores sin href: clic en el ancla recallFleet + confirmar 'Sí', y VERIFICAR.
            self.log.info("Regreso: sin href, clic en recallFleet (fleet %s) y confirmar diálogo.",
                          fid or "?")
            clicked = False
            try:
                clicked = bool(self.page.evaluate(
                    """(fid) => {
                        let el = null;
                        if (fid) el = document.querySelector('[data-fleet-id="' + fid + '"]');
                        el = el || document.querySelector('a.recallFleet, a[onclick*="sendRecall"]');
                        if (!el) return false;
                        (el.closest('a,button,[onclick],[role="button"]') || el).click();
                        return true;
                    }""", fid))
            except Exception as e:
                self.log.debug("Regreso: error al clicar recallFleet: %s", e)
            if clicked and self._confirm_recall_dialog() and self._verify_recalled(origin, destination, fid):
                self.log.info("Recall CONFIRMADO (vía clic) para flota %s -> %s.", origin, destination)
                return True
            self.log.warning("Regreso %s -> %s: el clic+confirmación no dejó la flota en retorno.",
                             origin, destination)
            return False
        # No casó ninguna fila: volcar lo que ve el DOM (origen/destino/misión/retorno y las
        # clases de los enlaces) para ajustar los selectores. Si la lista sale vacía, es que no
        # se encontraron filas de movimiento (selector de fila incorrecto en este servidor).
        try:
            import json as _json
            diag = self.page.evaluate(_load_js("recall_diag"))
            # Solo las filas relevantes (mismo origen o destino), ENTERAS y sin truncar, para
            # ver exactamente por qué no casa la fila buscada (misión / is_return / botón).
            rel = [r for r in diag if r.get("o") == origin or r.get("d") == destination]
            self.log.warning("Recall DIAG (%s -> %s mision=%s): %d filas; coincidentes=%s",
                             origin, destination, mission, len(diag),
                             _json.dumps(rel, ensure_ascii=False))
        except Exception as e:
            self.log.debug("Recall DIAG falló: %s", e)
        return False

    def _parse_duration_to_seconds(self, time_str: str) -> Optional[int]:
        if not time_str:
            return None
        time_str = time_str.strip().lower()
        import re
        if ":" in time_str:
            time_str = re.sub(r'\s*[hms]$', '', time_str)
        m_hms = re.match(r'^(\d+):(\d+):(\d+)$', time_str)
        if m_hms:
            h, m, s = map(int, m_hms.groups())
            return h * 3600 + m * 60 + s
        
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
        
        m_s = re.match(r'^(\d+)\s*s?$', time_str)
        if m_s:
            return int(m_s.group(1))
        return None
