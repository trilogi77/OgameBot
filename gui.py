import os
import sys
import re
import json
import yaml
import shutil
import subprocess
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import socket
import threading
import queue
import time

try:
    from http.server import ThreadingHTTPServer as HTTPServerClass
except ImportError:
    from http.server import HTTPServer as HTTPServerClass
    from socketserver import ThreadingMixIn
    class ThreadingHTTPServer(ThreadingMixIn, HTTPServerClass):
        pass
    HTTPServerClass = ThreadingHTTPServer

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

# ==========================================================================
# Multicuenta: cada cuenta vive en accounts/<id>/ con su propio config.yaml y
# todos sus ficheros de estado. El bot se lanza con cwd = ese directorio, así
# que todos los ficheros relativos (state.json, *_cache.json, ogbot.log, etc.)
# se aíslan solos. Cada cuenta usa un puerto CDP distinto para su navegador.
# ==========================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_DIR = os.path.join(BASE_DIR, "accounts")
BASE_CDP_PORT = 9222

PORT = 5000
bot_processes = {}  # account_id -> Popen

# --- Visor en vivo (de la cuenta seleccionada) ---
live_queue = queue.Queue()
latest_screenshot = None
live_status = {"available": False}
live_target = {"port": None}  # puerto CDP de la cuenta que se está viendo ahora


# ---------------------------------------------------------------- cuentas ---
def safe_account_id(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", (raw or "").strip())[:40]

def account_dir(account: str) -> str:
    return os.path.join(ACCOUNTS_DIR, account)

def acc_path(account: str, name: str) -> str:
    return os.path.join(account_dir(account), name)

def list_account_ids():
    if not os.path.isdir(ACCOUNTS_DIR):
        return []
    return sorted([d for d in os.listdir(ACCOUNTS_DIR)
                   if os.path.isfile(os.path.join(ACCOUNTS_DIR, d, "config.yaml"))])

def read_account_config(account: str) -> dict:
    p = acc_path(account, "config.yaml")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}
    return {}

def write_account_config(account: str, cfg: dict):
    os.makedirs(account_dir(account), exist_ok=True)
    with open(acc_path(account, "config.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, default_flow_style=False, allow_unicode=True)

def ensure_account_cdp_port(account: str) -> int:
    """Asigna (y persiste) un puerto CDP único a la cuenta si no tiene."""
    cfg = read_account_config(account)
    if cfg.get("cdp_port"):
        return int(cfg["cdp_port"])
    used = set()
    for a in list_account_ids():
        c = read_account_config(a)
        if c.get("cdp_port"):
            used.add(int(c["cdp_port"]))
    port = BASE_CDP_PORT
    while port in used:
        port += 1
    cfg["cdp_port"] = port
    write_account_config(account, cfg)
    return port

def account_cdp_port(account: str) -> int:
    cfg = read_account_config(account)
    return int(cfg["cdp_port"]) if cfg.get("cdp_port") else ensure_account_cdp_port(account)

def default_config() -> dict:
    """Plantilla de config para una cuenta nueva (desde el ejemplo o los defaults)."""
    example = os.path.join(BASE_DIR, "config.example.yaml")
    if os.path.exists(example):
        try:
            with open(example, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            data["username"] = ""
            data["password"] = ""
            return data
        except Exception:
            pass
    try:
        from ogbot.config import Config
        import dataclasses
        return dataclasses.asdict(Config())
    except Exception:
        return {}

def ensure_accounts_dir():
    """Crea accounts/ y migra el config.yaml de la raíz a una cuenta 'principal'."""
    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
    if not list_account_ids():
        root_cfg = os.path.join(BASE_DIR, "config.yaml")
        if os.path.exists(root_cfg):
            d = account_dir("principal")
            os.makedirs(d, exist_ok=True)
            shutil.copy(root_cfg, os.path.join(d, "config.yaml"))
            ensure_account_cdp_port("principal")
            print("Migrado config.yaml de la raíz a accounts/principal/.")


# ------------------------------------------------------------------ pids ---
def is_pid_running(pid: int) -> bool:
    try:
        out = subprocess.check_output(f'tasklist /FI "PID eq {pid}"', shell=True, text=True, stderr=subprocess.DEVNULL)
        return str(pid) in out
    except Exception:
        return False

def kill_pid(pid: int):
    try:
        subprocess.run(f'taskkill /F /PID {pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def get_saved_pid(account: str):
    pid_file = acc_path(account, "bot.pid")
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                return int(f.read().strip())
        except Exception:
            pass
    return None

def account_running(account: str) -> bool:
    proc = bot_processes.get(account)
    if proc is not None and proc.poll() is None:
        return True
    pid = get_saved_pid(account)
    return bool(pid and is_pid_running(pid))


# ------------------------------------------------------------- visor vivo ---
def get_game_page(browser):
    if not browser or not browser.contexts:
        return None
    all_pages = []
    for ctx in browser.contexts:
        try:
            all_pages.extend(ctx.pages)
        except Exception:
            continue
    for page in all_pages:
        try:
            if "index.php" in page.url or "/game/" in page.url:
                return page
        except Exception:
            continue
    return all_pages[-1] if all_pages else None

def _drain_queue_with_error(msg):
    try:
        while not live_queue.empty():
            task = live_queue.get_nowait()
            if "resp_queue" in task:
                task["resp_queue"].put({"error": msg})
    except queue.Empty:
        pass

def live_worker():
    """Conecta al navegador de la cuenta seleccionada (live_target) y la transmite.
    Se reconecta automáticamente cuando se cambia de cuenta o se cae la conexión."""
    global latest_screenshot
    if sync_playwright is None:
        print("Playwright no está instalado, deshabilitando transmisión en vivo.")
        return
    while True:
        target = live_target["port"]
        if not target:
            live_status["available"] = False
            _drain_queue_with_error("Selecciona una cuenta para ver el directo")
            time.sleep(0.5)
            continue
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.connect_over_cdp(f"http://localhost:{target}")
                    live_status["available"] = True
                except Exception:
                    live_status["available"] = False
                    _drain_queue_with_error("El navegador del bot no está activo")
                    time.sleep(1.0)
                    continue
                # Bucle mientras no cambie la cuenta objetivo
                while live_target["port"] == target:
                    try:
                        try:
                            task = live_queue.get(timeout=1.0)
                        except queue.Empty:
                            page = get_game_page(browser)
                            if page:
                                try:
                                    latest_screenshot = page.screenshot(type="jpeg", quality=60)
                                except Exception:
                                    break
                            continue
                        action = task.get("action")
                        page = get_game_page(browser)
                        if not page:
                            if "resp_queue" in task:
                                task["resp_queue"].put({"error": "No hay páginas de juego abiertas"})
                            continue
                        try:
                            if action == "click":
                                page.mouse.click(task["x"], task["y"])
                            elif action == "type":
                                page.keyboard.type(task["text"])
                            elif action == "press":
                                page.keyboard.press(task["key"])
                            elif action == "drag":
                                # Arrastre (para captchas "soy humano" de arrastrar)
                                page.mouse.move(task["x"], task["y"])
                                page.mouse.down()
                                page.mouse.move(task["x2"], task["y2"], steps=20)
                                page.mouse.up()
                            latest_screenshot = page.screenshot(type="jpeg", quality=60)
                            if "resp_queue" in task:
                                task["resp_queue"].put({"success": True})
                        except Exception as e:
                            if "resp_queue" in task:
                                task["resp_queue"].put({"error": str(e)})
                    except Exception:
                        break
                live_status["available"] = False
                try:
                    browser.close()
                except Exception:
                    pass
        except Exception:
            live_status["available"] = False
            time.sleep(1.0)

threading.Thread(target=live_worker, daemon=True).start()


class GUIRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    # ----------------------------------------------------------- routing ---
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        account = safe_account_id(parse_qs(parsed.query).get("account", [None])[0]) or None

        if path in ("/", "/index.html"):
            self.serve_static("gui_web/index.html", "text/html")
        elif path == "/index.css":
            self.serve_static("gui_web/index.css", "text/css")
        elif path == "/index.js":
            self.serve_static("gui_web/index.js", "application/javascript")
        elif path == "/api/accounts":
            self.get_accounts()
        elif path == "/api/config":
            self.get_config(account)
        elif path == "/api/status":
            self.get_status(account)
        elif path == "/api/logs":
            self.get_logs(account)
        elif path == "/api/planets":
            self.get_planets(account)
        elif path == "/api/fleet_motion":
            self._send_json_file(account, "fleet_in_motion.json", {})
        elif path == "/api/flights":
            self._send_json_file(account, "fleet_flights.json", {"flights": []})
        elif path == "/api/stats":
            self.get_stats(account)
        elif path == "/api/messages":
            self._send_json_file(account, "messages_read.json", {"messages": []})
        elif path == "/api/expedition":
            self.get_expedition_status(account)
        elif path == "/api/buildstatus":
            self.get_build_status(account)
        elif path == "/api/live/status":
            self._set_live_target(account)
            self.send_json(200, {"available": live_status["available"]})
        elif path == "/api/live/screenshot":
            self.serve_live_screenshot(account)
        else:
            self.send_error(404, "File not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        account = safe_account_id(parse_qs(parsed.query).get("account", [None])[0]) or None

        if path == "/api/accounts/create":
            self.create_account()
        elif path == "/api/accounts/delete":
            self.delete_account()
        elif path == "/api/config":
            self.save_config(account)
        elif path == "/api/start":
            self.start_bot(account)
        elif path == "/api/stop":
            self.stop_bot(account)
        elif path == "/api/locator":
            self.run_locator(account)
        elif path == "/api/telegram/test":
            self.test_telegram(account)
        elif path == "/api/resync":
            self.force_resync(account)
        elif path == "/api/state_override":
            self.add_state_override(account)
        elif path == "/api/build_queue":
            self.save_build_queue(account)
        elif path == "/api/live/click":
            self.handle_live_click(account)
        elif path == "/api/live/type":
            self.handle_live_type(account)
        elif path == "/api/live/press":
            self.handle_live_press(account)
        elif path == "/api/live/drag":
            self.handle_live_drag(account)
        else:
            self.send_error(404, "Endpoint not found")

    def serve_static(self, rel_path, content_type):
        abs_path = os.path.join(BASE_DIR, rel_path)
        if os.path.exists(abs_path):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.end_headers()
            with open(abs_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, f"File {rel_path} not found")

    # ---------------------------------------------------------- cuentas ----
    def get_accounts(self):
        out = []
        for a in list_account_ids():
            out.append({
                "id": a,
                "running": account_running(a),
                "cdp_port": account_cdp_port(a),
            })
        self.send_json(200, {"accounts": out})

    def create_account(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b"{}")
        except Exception:
            body = {}
        acc = safe_account_id(body.get("id", ""))
        if not acc:
            return self.send_json(400, {"error": "Nombre de cuenta inválido (usa letras, números, - o _)"})
        if acc in list_account_ids():
            return self.send_json(400, {"error": "Ya existe una cuenta con ese nombre"})
        cfg = default_config()
        write_account_config(acc, cfg)
        ensure_account_cdp_port(acc)
        self.send_json(200, {"status": "created", "id": acc})

    def delete_account(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b"{}")
        except Exception:
            body = {}
        acc = safe_account_id(body.get("id", ""))
        if acc not in list_account_ids():
            return self.send_json(400, {"error": "La cuenta no existe"})
        if account_running(acc):
            self._stop_account(acc)
        try:
            shutil.rmtree(account_dir(acc))
            self.send_json(200, {"status": "deleted"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ----------------------------------------------------------- config ----
    def get_config(self, account):
        if not account:
            return self.send_json(400, {"error": "Falta la cuenta"})
        p = acc_path(account, "config.yaml")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    self.send_json(200, yaml.safe_load(f) or {})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(200, default_config())

    def save_config(self, account):
        if not account:
            return self.send_json(400, {"error": "Falta la cuenta"})
        try:
            new_settings = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            current = read_account_config(account)
            for k, v in new_settings.items():
                current[k] = v
            # Nunca perder el puerto CDP asignado a la cuenta
            if not current.get("cdp_port"):
                current["cdp_port"] = account_cdp_port(account)
            write_account_config(account, current)
            self.send_json(200, {"status": "success"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def reset_stats_file(self, account):
        empty_stats = {
            "total_farming": {"metal": 0, "crystal": 0, "deut": 0},
            "total_recycling": {"metal": 0, "crystal": 0, "deut": 0},
            "total_expeditions": {"metal": 0, "crystal": 0, "deut": 0, "dark_matter": 0, "ships_found": {}},
            "parsed_messages": [],
            "session_actions": {
                "buildings": {}, "research": [], "fleet": {}, "defense": {},
                "farming": {}, "expeditions": {}, "hostile_attacks": {}, "espionage": {}
            }
        }
        try:
            with open(acc_path(account, "ogbot_stats.json"), "w", encoding="utf-8") as f:
                json.dump(empty_stats, f, indent=2)
        except Exception:
            pass

    # --------------------------------------------------------- bot proc ----
    def get_status(self, account):
        if not account:
            return self.send_json(200, {"running": False})
        running = account_running(account)
        if not running:
            # limpiar referencias muertas
            if account in bot_processes:
                bot_processes[account] = None
        self.send_json(200, {"running": running})

    def start_bot(self, account):
        if not account:
            return self.send_json(400, {"error": "Falta la cuenta"})
        cfg_path = acc_path(account, "config.yaml")
        if not os.path.exists(cfg_path):
            return self.send_json(400, {"error": "Configura y guarda la cuenta antes de iniciarla"})
        if account_running(account):
            return self.send_json(400, {"error": "Esta cuenta ya está en ejecución"})
        try:
            self.reset_stats_file(account)
            ensure_account_cdp_port(account)
            adir = account_dir(account)
            main_path = os.path.join(BASE_DIR, "main.py")
            # Las credenciales vienen del config.yaml de la cuenta; quitamos las env
            # globales para que no contaminen entre cuentas.
            env = dict(os.environ)
            env.pop("OGBOT_USER", None)
            env.pop("OGBOT_PASS", None)
            env["OGBOT_CDP_PORT"] = str(account_cdp_port(account))
            err_file = open(acc_path(account, "bot_stderr.log"), "w", encoding="utf-8")
            proc = subprocess.Popen(
                [sys.executable, main_path, "--config", "config.yaml"],
                stdout=subprocess.DEVNULL,
                stderr=err_file,
                cwd=adir,
                env=env,
            )
            bot_processes[account] = proc
            with open(acc_path(account, "bot.pid"), "w") as f:
                f.write(str(proc.pid))
            self.send_json(200, {"status": "started", "pid": proc.pid})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def _stop_account(self, account):
        proc = bot_processes.get(account)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        bot_processes[account] = None
        pid = get_saved_pid(account)
        if pid:
            kill_pid(pid)
        pid_file = acc_path(account, "bot.pid")
        if os.path.exists(pid_file):
            try:
                os.remove(pid_file)
            except Exception:
                pass

    def stop_bot(self, account):
        if not account:
            return self.send_json(400, {"error": "Falta la cuenta"})
        if not account_running(account):
            return self.send_json(400, {"error": "Esta cuenta no está en ejecución"})
        try:
            self._stop_account(account)
            self.send_json(200, {"status": "stopped"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # ------------------------------------------------------ datos por API --
    def _send_json_file(self, account, name, default):
        if not account:
            return self.send_json(200, default)
        p = acc_path(account, name)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    self.send_json(200, json.load(f))
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(200, default)

    def get_logs(self, account):
        if not account:
            return self.send_json(200, {"logs": ["Selecciona una cuenta."]})
        p = acc_path(account, "ogbot.log")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    self.send_json(200, {"logs": f.readlines()[-150:]})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(200, {"logs": ["El log de esta cuenta no existe todavía. Inicia el bot."]})

    def get_planets(self, account):
        if not account:
            return self.send_json(200, {"planets": []})
        p = acc_path(account, "planets_cache.json")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    self.send_json(200, {"planets": json.load(f)})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(200, {"planets": []})

    def get_stats(self, account):
        self._send_json_file(account, "ogbot_stats.json", {
            "total_farming": {"metal": 0, "crystal": 0, "deut": 0},
            "total_recycling": {"metal": 0, "crystal": 0, "deut": 0},
            "total_expeditions": {"metal": 0, "crystal": 0, "deut": 0, "dark_matter": 0, "ships_found": {}}
        })

    def get_expedition_status(self, account):
        self._send_json_file(account, "expedition_status.json", {})

    def get_build_status(self, account):
        self._send_json_file(account, "build_status.json", {})

    def run_locator(self, account):
        try:
            params = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            coordinate = params.get("coordinate", "").strip()
            server = params.get("server", "").strip()
            if not coordinate:
                return self.send_json(400, {"error": "Coordenada vacía"})
            if not server and account:
                cfg = read_account_config(account)
                server_url = cfg.get("server_url", "")
                if server_url:
                    parsed = urlparse(server_url)
                    server = parsed.netloc or parsed.path
            if not server:
                server = "s273-es.ogame.gameforge.com"
            script_path = os.path.join(BASE_DIR, "Localizador de colonias.py")
            p = subprocess.Popen(
                [sys.executable, script_path, coordinate, server],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="ignore", cwd=BASE_DIR
            )
            stdout, stderr = p.communicate()
            self.send_json(200, {"output": stdout, "error": stderr})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # -------------------------------------------------------- test telegram --
    def test_telegram(self, account):
        """Envía un mensaje de prueba a Telegram y devuelve el resultado (síncrono)."""
        import urllib.request
        import urllib.error
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b"{}")
        except Exception:
            body = {}
        token = (body.get("token") or "").strip()
        chat_id = (body.get("chat_id") or "").strip()
        # Si no vienen en el cuerpo, usar lo guardado en el config de la cuenta.
        if (not token or not chat_id) and account:
            cfg = read_account_config(account)
            token = token or str(cfg.get("telegram_token", "")).strip()
            chat_id = chat_id or str(cfg.get("telegram_chat_id", "")).strip()
        if not token or not chat_id:
            return self.send_json(400, {"error": "Falta el token o el ID de chat de Telegram"})
        text = "✅ Mensaje de prueba de OGBot. Si lees esto, las notificaciones de Telegram funcionan correctamente."
        data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            self.send_json(200, {"status": "success"})
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8", "ignore")).get("description", "")
            except Exception:
                detail = ""
            self.send_json(200, {"error": f"Telegram rechazó el envío ({e.code}): {detail or e.reason}"})
        except Exception as e:
            self.send_json(200, {"error": f"No se pudo contactar con Telegram: {e}"})

    # ------------------------------------------------- corrección de niveles --
    def force_resync(self, account):
        """Marca para que el bot relea niveles en el próximo ciclo: una ubicación
        concreta ({coords,is_moon}) o toda la cuenta ({all:true})."""
        if not account:
            return self.send_json(400, {"error": "Falta la cuenta"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b"{}")
        except Exception:
            body = {}
        path = acc_path(account, "force_resync.json")
        try:
            req = {"all": False, "targets": []}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        req = loaded
            req.setdefault("targets", [])
            if body.get("all"):
                req["all"] = True
            else:
                coords = (body.get("coords") or "").strip()
                if not coords:
                    return self.send_json(400, {"error": "Falta el planeta a releer"})
                key = f"{coords}:{'moon' if body.get('is_moon') else 'planet'}"
                if key not in req["targets"]:
                    req["targets"].append(key)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(req, f)
            self.send_json(200, {"status": "ok"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def add_state_override(self, account):
        """Apila una corrección manual de nivel que el bot aplicará en el próximo ciclo."""
        if not account:
            return self.send_json(400, {"error": "Falta la cuenta"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b"{}")
        except Exception:
            body = {}
        name = (body.get("name") or "").strip()
        if not name:
            return self.send_json(400, {"error": "Falta el edificio/tecnología"})
        try:
            level = int(body.get("level"))
        except (TypeError, ValueError):
            return self.send_json(400, {"error": "Nivel inválido"})
        if level < 0:
            return self.send_json(400, {"error": "El nivel no puede ser negativo"})
        ov = {
            "kind": body.get("kind", "building"),
            "coords": (body.get("coords") or "").strip(),
            "is_moon": bool(body.get("is_moon")),
            "name": name,
            "level": level,
        }
        path = acc_path(account, "state_overrides.json")
        try:
            data = []
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or []
            # Sustituir una corrección previa del mismo objetivo si la hubiera.
            data = [o for o in data if not (o.get("kind") == ov["kind"]
                                            and o.get("coords") == ov["coords"]
                                            and bool(o.get("is_moon")) == ov["is_moon"]
                                            and o.get("name") == ov["name"])]
            data.append(ov)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.send_json(200, {"status": "ok"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def save_build_queue(self, account):
        """Guarda la cola de construcción de un planeta en planets_config[coords].build_queue."""
        if not account:
            return self.send_json(400, {"error": "Falta la cuenta"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b"{}")
        except Exception:
            body = {}
        coords = (body.get("coords") or "").strip()
        queue = body.get("queue")
        if not coords or not isinstance(queue, list):
            return self.send_json(400, {"error": "Faltan coords o queue"})
        try:
            from ogbot import gamedata as gd
            valid_buildings = set(gd.BUILDING_COST.keys())
        except Exception:
            valid_buildings = None
        clean = []
        for e in queue:
            if not isinstance(e, dict):
                continue
            name = str(e.get("building", "")).strip()
            try:
                lvl = int(e.get("target_level"))
            except (TypeError, ValueError):
                continue
            if not name or lvl <= 0:
                continue
            if valid_buildings is not None and name not in valid_buildings:
                return self.send_json(400, {"error": f"Edificio desconocido: {name}"})
            clean.append({"building": name, "target_level": lvl})
        try:
            cfg = read_account_config(account)
            pc = cfg.get("planets_config") or {}
            if not isinstance(pc, dict):
                pc = {}
            p = pc.get(coords) or {}
            if not isinstance(p, dict):
                p = {}
            p["build_queue"] = clean
            pc[coords] = p
            cfg["planets_config"] = pc
            write_account_config(account, cfg)
            self.send_json(200, {"status": "ok"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    # --------------------------------------------------------- visor vivo --
    def _set_live_target(self, account):
        if account and account in list_account_ids():
            live_target["port"] = account_cdp_port(account)
        else:
            live_target["port"] = None

    def serve_live_screenshot(self, account):
        self._set_live_target(account)
        if latest_screenshot is not None and live_status["available"]:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.end_headers()
            self.wfile.write(latest_screenshot)
        else:
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "No hay captura disponible"}).encode("utf-8"))

    def _live_action(self, account, task):
        self._set_live_target(account)
        try:
            resp_q = queue.Queue()
            task["resp_queue"] = resp_q
            live_queue.put(task)
            res = resp_q.get(timeout=5.0)
            if "error" in res:
                self.send_json(500, {"error": res["error"]})
            else:
                self.send_json(200, {"status": "success"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def handle_live_click(self, account):
        params = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        self._live_action(account, {"action": "click", "x": int(params.get("x", 0)), "y": int(params.get("y", 0))})

    def handle_live_type(self, account):
        params = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        self._live_action(account, {"action": "type", "text": params.get("text", "")})

    def handle_live_press(self, account):
        params = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        self._live_action(account, {"action": "press", "key": params.get("key", "")})

    def handle_live_drag(self, account):
        params = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        self._live_action(account, {"action": "drag",
                                    "x": int(params.get("x", 0)), "y": int(params.get("y", 0)),
                                    "x2": int(params.get("x2", 0)), "y2": int(params.get("y2", 0))})

    def send_json(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))


def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def run():
    global PORT
    ensure_accounts_dir()
    while is_port_in_use(PORT):
        print(f"Puerto {PORT} ocupado. Probando con {PORT+1}...")
        PORT += 1
    httpd = HTTPServerClass(('', PORT), GUIRequestHandler)
    url = f"http://localhost:{PORT}"
    print("=========================================================")
    print(f" Panel multicuenta de OGBot iniciado en: {url}")
    print(" Abre el enlace en tu navegador para gestionar tus cuentas.")
    print(" Ctrl+C en esta consola para cerrar el panel.")
    print("=========================================================")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando panel... deteniendo bots en ejecución.")
        for acc, proc in list(bot_processes.items()):
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    pass
        httpd.server_close()

if __name__ == "__main__":
    run()
