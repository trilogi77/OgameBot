import os
import sys
import json
import yaml
import subprocess
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
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

# Cola de tareas e indicadores para transmisión y control en directo
live_queue = queue.Queue()
latest_screenshot = None
live_status = {"available": False}

def get_game_page(browser):
    if not browser or not browser.contexts:
        return None
    context = browser.contexts[0]
    for page in context.pages:
        if "index.php" in page.url or "/game/" in page.url:
            return page
    if context.pages:
        return context.pages[-1]
    return None

def live_worker():
    global latest_screenshot
    if sync_playwright is None:
        print("Playwright no está instalado, deshabilitando transmisión en vivo.")
        return

    while True:
        try:
            with sync_playwright() as p:
                browser = None
                while True:
                    if browser is None:
                        try:
                            # Conectarse al Chromium del bot por el puerto 9222
                            browser = p.chromium.connect_over_cdp("http://localhost:9222")
                            live_status["available"] = True
                        except Exception:
                            live_status["available"] = False
                            # Resolver tareas pendientes con error si no hay conexión
                            try:
                                while not live_queue.empty():
                                    task = live_queue.get_nowait()
                                    if "resp_queue" in task:
                                        task["resp_queue"].put({"error": "El navegador del bot no está activo"})
                            except queue.Empty:
                                pass
                            time.sleep(1.0)
                            continue

                    try:
                        # Comprobar si hay alguna acción en cola
                        try:
                            task = live_queue.get(timeout=1.0)
                        except queue.Empty:
                            # Si no hay peticiones, tomar una captura periódica del juego
                            page = get_game_page(browser)
                            if page:
                                try:
                                    latest_screenshot = page.screenshot(type="jpeg", quality=60)
                                except Exception:
                                    browser = None
                                    live_status["available"] = False
                            continue

                        action = task.get("action")
                        page = get_game_page(browser)
                        
                        if not page:
                            if "resp_queue" in task:
                                task["resp_queue"].put({"error": "No hay páginas de juego abiertas en el navegador"})
                            continue

                        try:
                            if action == "click":
                                page.mouse.click(task["x"], task["y"])
                                latest_screenshot = page.screenshot(type="jpeg", quality=60)
                                if "resp_queue" in task:
                                    task["resp_queue"].put({"success": True})
                            elif action == "type":
                                page.keyboard.type(task["text"])
                                latest_screenshot = page.screenshot(type="jpeg", quality=60)
                                if "resp_queue" in task:
                                    task["resp_queue"].put({"success": True})
                            elif action == "press":
                                page.keyboard.press(task["key"])
                                latest_screenshot = page.screenshot(type="jpeg", quality=60)
                                if "resp_queue" in task:
                                    task["resp_queue"].put({"success": True})
                        except Exception as e:
                            if "resp_queue" in task:
                                task["resp_queue"].put({"error": str(e)})
                    except Exception:
                        browser = None
                        live_status["available"] = False
        except Exception:
            time.sleep(2.0)

# Lanzar worker de directo en segundo plano
threading.Thread(target=live_worker, daemon=True).start()


PORT = 5000
bot_process = None

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

class GUIRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Desactivar logs de peticiones HTTP en consola para no ensuciar la salida del usuario
        pass

    def do_GET(self):
        global bot_process
        if self.path == "/" or self.path == "/index.html":
            self.serve_static("gui_web/index.html", "text/html")
        elif self.path == "/index.css":
            self.serve_static("gui_web/index.css", "text/css")
        elif self.path == "/index.js":
            self.serve_static("gui_web/index.js", "application/javascript")
        elif self.path == "/api/config":
            self.get_config()
        elif self.path == "/api/status":
            self.get_status()
        elif self.path == "/api/logs":
            self.get_logs()
        elif self.path == "/api/planets":
            self.get_planets()
        elif self.path == "/api/stats":
            self.get_stats()
        elif self.path == "/api/live/status":
            self.send_json(200, {"available": live_status["available"]})
        elif self.path.startswith("/api/live/screenshot"):
            self.serve_live_screenshot()
        else:
            self.send_error(404, "File not found")

    def do_POST(self):
        if self.path == "/api/config":
            self.save_config()
        elif self.path == "/api/start":
            self.start_bot()
        elif self.path == "/api/stop":
            self.stop_bot()
        elif self.path == "/api/locator":
            self.run_locator()
        elif self.path == "/api/live/click":
            self.handle_live_click()
        elif self.path == "/api/live/type":
            self.handle_live_type()
        elif self.path == "/api/live/press":
            self.handle_live_press()
        else:
            self.send_error(404, "Endpoint not found")

    def serve_static(self, rel_path, content_type):
        abs_path = os.path.join(os.path.dirname(__file__), rel_path)
        if os.path.exists(abs_path):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            # Desactivar caché durante el desarrollo para que los cambios se apliquen al recargar
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.end_headers()
            with open(abs_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, f"File {rel_path} not found")

    def get_config(self):
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                self.send_json(200, data)
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            # Si config.yaml no existe, intentar cargar config.example.yaml o usar valores por defecto
            example_path = os.path.join(os.path.dirname(__file__), "config.example.yaml")
            if os.path.exists(example_path):
                try:
                    with open(example_path, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    # Limpiar las credenciales de ejemplo para que el usuario las rellene
                    data["username"] = ""
                    data["password"] = ""
                    self.send_json(200, data)
                except Exception as e:
                    self.send_json(500, {"error": str(e)})
            else:
                try:
                    from ogbot.config import Config
                    import dataclasses
                    cfg = Config()
                    data = dataclasses.asdict(cfg)
                    self.send_json(200, data)
                except Exception as e:
                    self.send_json(500, {"error": str(e)})

    def save_config(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            new_settings = json.loads(post_data)
            config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
            
            # Cargar actual para preservar claves extras
            current_config = {}
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    current_config = yaml.safe_load(f) or {}

            # Actualizar campos
            for k, v in new_settings.items():
                current_config[k] = v

            # Escribir de nuevo al YAML
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(current_config, f, default_flow_style=False, allow_unicode=True)

            self.send_json(200, {"status": "success"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def reset_stats_file(self):
        stats_path = os.path.join(os.path.dirname(__file__), "ogbot_stats.json")
        empty_stats = {
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
        try:
            with open(stats_path, "w", encoding="utf-8") as f:
                json.dump(empty_stats, f, indent=2)
        except Exception:
            pass

    def get_saved_pid(self):
        pid_file = os.path.join(os.path.dirname(__file__), "bot.pid")
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r") as f:
                    return int(f.read().strip())
            except Exception:
                pass
        return None

    def save_pid(self, pid: int):
        pid_file = os.path.join(os.path.dirname(__file__), "bot.pid")
        try:
            with open(pid_file, "w") as f:
                f.write(str(pid))
        except Exception:
            pass

    def clear_pid(self):
        pid_file = os.path.join(os.path.dirname(__file__), "bot.pid")
        if os.path.exists(pid_file):
            try:
                os.remove(pid_file)
            except Exception:
                pass

    def get_status(self):
        global bot_process
        is_running = False
        if bot_process is not None and bot_process.poll() is None:
            is_running = True
        else:
            pid = self.get_saved_pid()
            if pid and is_pid_running(pid):
                is_running = True
            else:
                bot_process = None
                self.clear_pid()
        self.send_json(200, {"running": is_running})

    def start_bot(self):
        global bot_process
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        if not os.path.exists(config_path):
            self.send_json(400, {"error": "Debes configurar y guardar la configuración antes de iniciar el bot"})
            return

        pid = self.get_saved_pid()
        if (bot_process is not None and bot_process.poll() is None) or (pid and is_pid_running(pid)):
            self.send_json(400, {"error": "El bot ya está en ejecución"})
            return

        try:
            self.reset_stats_file()
            main_path = os.path.join(os.path.dirname(__file__), "main.py")
            with open("bot_stderr.log", "w", encoding="utf-8") as err_file:
                bot_process = subprocess.Popen(
                    [sys.executable, main_path],
                    stdout=subprocess.DEVNULL,
                    stderr=err_file,
                    cwd=os.path.dirname(__file__)
                )
            self.save_pid(bot_process.pid)
            self.send_json(200, {"status": "started", "pid": bot_process.pid})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def stop_bot(self):
        global bot_process
        pid = self.get_saved_pid()
        if (bot_process is None or bot_process.poll() is not None) and not (pid and is_pid_running(pid)):
            self.send_json(400, {"error": "El bot no está en ejecución"})
            return

        try:
            if bot_process is not None:
                bot_process.terminate()
                try:
                    bot_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    bot_process.kill()
                    bot_process.wait()
                bot_process = None
            
            if pid:
                kill_pid(pid)
                self.clear_pid()

            self.send_json(200, {"status": "stopped"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def run_locator(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            params = json.loads(post_data)
            coordinate = params.get("coordinate", "").strip()
            server = params.get("server", "").strip()
            
            if not coordinate:
                self.send_json(400, {"error": "Coordenada vacía"})
                return
            
            # Si no se provee servidor, intentar obtenerlo de config.yaml
            if not server:
                config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
                if os.path.exists(config_path):
                    try:
                        with open(config_path, "r", encoding="utf-8") as f:
                            cfg = yaml.safe_load(f) or {}
                        server_url = cfg.get("server_url", "")
                        if server_url:
                            from urllib.parse import urlparse
                            parsed = urlparse(server_url)
                            server = parsed.netloc or parsed.path
                    except Exception:
                        pass
            
            # Si sigue vacío, usar el valor por defecto
            if not server:
                server = "s273-es.ogame.gameforge.com"
                
            script_path = os.path.join(os.path.dirname(__file__), "Localizador de colonias.py")
            
            # Ejecutar el script usando el ejecutable actual de python
            p = subprocess.Popen(
                [sys.executable, script_path, coordinate, server],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                cwd=os.path.dirname(__file__)
            )
            stdout, stderr = p.communicate()
            
            self.send_json(200, {"output": stdout, "error": stderr})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def get_logs(self):
        log_path = os.path.join(os.path.dirname(__file__), "ogbot.log")
        if os.path.exists(log_path):
            try:
                # Leer las últimas 150 líneas de forma segura con encoding y ignore errors
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                self.send_json(200, {"logs": lines[-150:]})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(200, {"logs": ["El archivo ogbot.log no existe todavía. Inicia el bot para generarlo."]})

    def get_planets(self):
        cache_path = os.path.join(os.path.dirname(__file__), "planets_cache.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    planets = json.load(f)
                self.send_json(200, {"planets": planets})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(200, {"planets": []})

    def get_stats(self):
        stats_path = os.path.join(os.path.dirname(__file__), "ogbot_stats.json")
        if os.path.exists(stats_path):
            try:
                with open(stats_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.send_json(200, data)
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(200, {
                "total_farming": {"metal": 0, "crystal": 0, "deut": 0},
                "total_recycling": {"metal": 0, "crystal": 0, "deut": 0},
                "total_expeditions": {"metal": 0, "crystal": 0, "deut": 0, "dark_matter": 0, "ships_found": {}}
            })

    def serve_live_screenshot(self):
        global latest_screenshot
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

    def handle_live_click(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            params = json.loads(post_data)
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            
            resp_q = queue.Queue()
            live_queue.put({"action": "click", "x": x, "y": y, "resp_queue": resp_q})
            
            res = resp_q.get(timeout=5.0)
            if "error" in res:
                self.send_json(500, {"error": res["error"]})
            else:
                self.send_json(200, {"status": "success"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def handle_live_type(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            params = json.loads(post_data)
            text = params.get("text", "")
            
            resp_q = queue.Queue()
            live_queue.put({"action": "type", "text": text, "resp_queue": resp_q})
            
            res = resp_q.get(timeout=5.0)
            if "error" in res:
                self.send_json(500, {"error": res["error"]})
            else:
                self.send_json(200, {"status": "success"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

    def handle_live_press(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            params = json.loads(post_data)
            key = params.get("key", "")
            
            resp_q = queue.Queue()
            live_queue.put({"action": "press", "key": key, "resp_queue": resp_q})
            
            res = resp_q.get(timeout=5.0)
            if "error" in res:
                self.send_json(500, {"error": res["error"]})
            else:
                self.send_json(200, {"status": "success"})
        except Exception as e:
            self.send_json(500, {"error": str(e)})

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
    while is_port_in_use(PORT):
        print(f"Puerto {PORT} ocupado. Probando con {PORT+1}...")
        PORT += 1

    server_address = ('', PORT)
    httpd = HTTPServerClass(server_address, GUIRequestHandler)
    url = f"http://localhost:{PORT}"
    print(f"=========================================================")
    print(f" Servidor Web GUI de OGBot iniciado en: {url}")
    print(f" Abre este enlace en tu navegador para configurar el bot.")
    print(f" Presiona Ctrl+C en esta consola para cerrar el servidor.")
    print(f"=========================================================")
    
    # Abrir navegador automáticamente
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando servidor Web GUI...")
        global bot_process
        if bot_process is not None and bot_process.poll() is None:
            print("Deteniendo bot en segundo plano...")
            bot_process.terminate()
            bot_process.wait()
        httpd.server_close()

if __name__ == "__main__":
    run()
