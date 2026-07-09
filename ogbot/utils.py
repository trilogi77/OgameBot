"""
utils.py
========
Herramientas transversales: logging, comportamiento humanizado y rate limiting.
La "humanización" reduce (no elimina) el riesgo de detección.
"""
from __future__ import annotations
import json
import logging
import os
import random
import time
from collections import deque
from datetime import datetime


def setup_logger(name: str, log_file: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if logger.handlers:
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def human_delay(lo: float, hi: float) -> None:
    """Pausa con distribución sesgada hacia el medio (más natural que uniforme)."""
    t = (random.random() + random.random()) / 2 * (hi - lo) + lo
    time.sleep(t)


def jitter(base: float, pct: float = 0.25) -> float:
    return base * (1 + random.uniform(-pct, pct))


def within_active_hours(active: tuple) -> bool:
    start, end = active
    h = datetime.now().hour
    if start == end or (start == 0 and end == 24):
        return True
    if start <= end:
        return start <= h < end
    return h >= start or h < end


def seconds_until_inactive(active: tuple) -> float:
    """Devuelve la cantidad de segundos restantes en el horario activo actual."""
    start, end = active
    if start == end or (start == 0 and end == 24):
        return 999999.0
        
    now = datetime.now()
    curr_hour = now.hour + now.minute / 60.0 + now.second / 3600.0
    
    if start <= end:
        if curr_hour < end:
            hours_left = end - curr_hour
        else:
            hours_left = 0.0
    else:
        if curr_hour >= start:
            hours_left = (24.0 - curr_hour) + end
        elif curr_hour < end:
            hours_left = end - curr_hour
        else:
            hours_left = 0.0
            
    return max(0.0, hours_left * 3600.0)


def hours_until_active(active: tuple) -> float:
    start, end = active
    now = datetime.now()
    h = now.hour
    m = now.minute
    s = now.second
    current_time_float = h + m / 60.0 + s / 3600.0
    
    if start <= end:
        if current_time_float < start:
            return start - current_time_float
        else:
            return (24.0 - current_time_float) + start
    else:
        if current_time_float >= end and current_time_float < start:
            return start - current_time_float
        else:
            return 0.0


def is_night(night_hours: tuple) -> bool:
    start, end = night_hours
    h = datetime.now().hour
    if start <= end:
        return start <= h < end
    return h >= start or h < end


class RateLimiter:
    """Limita acciones/hora para no parecer un bot agresivo."""

    def __init__(self, max_per_hour: int):
        self.max = max_per_hour
        self.events: deque[float] = deque()

    def allow(self) -> bool:
        now = time.time()
        while self.events and now - self.events[0] > 3600:
            self.events.popleft()
        return len(self.events) < self.max

    def record(self) -> None:
        self.events.append(time.time())

    def wait_if_needed(self) -> None:
        while not self.allow():
            oldest = self.events[0]
            sleep_for = max(1.0, 3600 - (time.time() - oldest))
            time.sleep(min(sleep_for, 60))


def send_telegram_message(token: str, chat_id: str, text: str, logger=None, block: bool = False):
    """Envía un mensaje a un chat de Telegram. Por defecto es asíncrono (no bloquea el bot).
    Con block=True envía de forma síncrona y devuelve True/False según el éxito, para flujos
    que necesitan saber si la notificación llegó (p.ej. la vigilancia de espionaje)."""
    if not token or not chat_id:
        return False
    import threading
    import urllib.request
    import urllib.parse
    import json

    import urllib.error
    import time as _time

    def _send() -> bool:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }).encode("utf-8")
        # Reintentos: una alerta de ataque no puede perderse por un blip de red, y Telegram
        # devuelve 429 (rate-limit) con 'retry_after' que hay que respetar.
        for attempt in range(3):
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    response.read()
                return True
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    try:
                        body = json.loads(e.read().decode("utf-8"))
                        wait = float(body.get("parameters", {}).get("retry_after", 1))
                    except Exception:
                        wait = 1.0
                    _time.sleep(min(wait, 30))
                    continue
                if 500 <= e.code < 600 and attempt < 2:
                    _time.sleep(2 ** attempt)
                    continue
                if logger:
                    logger.debug("Telegram HTTP %s: %s", e.code, e)
                return False
            except Exception as e:
                if attempt < 2:
                    _time.sleep(2 ** attempt)
                    continue
                if logger:
                    logger.debug("Error al enviar mensaje de Telegram: %s", e)
                return False
        return False

    if block:
        return _send()
    threading.Thread(target=_send, daemon=True).start()
    return None


def atomic_write_json(path: str, obj, indent: int = 2, backup: bool = False) -> None:
    """Escritura atómica (tmp + os.replace): la GUI sondea estos JSON y no debe
    ver nunca un fichero a medio escribir.

    backup=True conserva la última versión buena en path+'.bak' antes de reemplazar
    (para state.json / caché: si una corrupción -corte de luz, disco lleno- rompe el
    fichero, load_json_or_backup lo recupera en vez de resetear timers/aprendizaje)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)
    if backup:
        try:
            if os.path.exists(path):
                import shutil
                shutil.copy2(path, path + ".bak")
        except Exception:
            pass
    os.replace(tmp, path)


def load_json_or_backup(path: str, logger=None):
    """Carga un JSON; si está corrupto, restaura de path+'.bak' (última versión buena)
    en vez de degradar en silencio a estado vacío. Devuelve el objeto o None si no hay
    ni fichero ni backup válidos. Renombra el corrupto a .corrupt para conservar evidencia."""
    import json as _json
    for candidate, is_bak in ((path, False), (path + ".bak", True)):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if is_bak and logger:
                logger.warning("¡%s corrupto! Restaurado desde %s.bak.", path, path)
                try:
                    os.replace(path, path + ".corrupt")
                except Exception:
                    pass
            return data
        except Exception as e:
            if not is_bak and logger:
                logger.warning("No se pudo leer %s (%s); intentando .bak...", path, e)
            continue
    return None


def parse_localized_number(text) -> float:
    """Parsea números del DOM de OGame en cualquier locale ('1.234.567', '1,5M',
    '12k', '3,5', '1 234'...). Devuelve 0.0 para None/''/'-' o texto no numérico."""
    if text is None:
        return 0.0
    s = str(text).strip()
    if s in ("", "-"):
        return 0.0
    # espacios normales, nbsp, finos y de cifra usados como separador de miles
    for ch in (" ", " ", " ", " ", " "):
        s = s.replace(ch, "")
    if not s:
        return 0.0
    neg = s.startswith("-")
    if s[0] in "+-":
        s = s[1:]

    mult = 1.0
    low = s.lower()
    # multi-letra antes que 'm'/'g'/'b' sueltas para no recortar de menos
    for suf, m in (("mio", 1e6), ("mrd", 1e9), ("k", 1e3), ("m", 1e6), ("g", 1e9), ("b", 1e9)):
        if low.endswith(suf):
            s = s[: -len(suf)]
            mult = m
            break

    if mult != 1.0:
        # con sufijo el separador es decimal ("1,5M" -> 1.5 millones)
        s = s.replace(",", ".")
        if s.count(".") > 1:
            head, _, tail = s.rpartition(".")
            s = head.replace(".", "") + "." + tail
    else:
        has_dot = "." in s
        has_comma = "," in s
        if has_dot and has_comma:
            # el ÚLTIMO separador es el decimal, el otro es de miles
            if s.rfind(".") > s.rfind(","):
                dec, thou = ".", ","
            else:
                dec, thou = ",", "."
            s = s.replace(thou, "").replace(dec, ".")
        elif has_dot or has_comma:
            sep = "." if has_dot else ","
            parts = s.split(sep)
            looks_thousands = (
                all(len(p) == 3 for p in parts[1:])
                and 1 <= len(parts[0]) <= 3
                and parts[0] != "0"
            )
            if looks_thousands:
                s = s.replace(sep, "")
            elif len(parts) > 2:
                s = "".join(parts[:-1]) + "." + parts[-1]
            else:
                s = s.replace(sep, ".")
    try:
        val = float(s)
    except ValueError:
        return 0.0
    return (-val if neg else val) * mult


def tg_escape(text) -> str:
    """Escapa &, < y > para enviar texto con parse_mode=HTML de Telegram."""
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_telegram_photo(token, chat_id, photo_path, caption="", logger=None, block=False):
    """Envía una foto a un chat de Telegram (sendPhoto). Mismo patrón que
    send_telegram_message: asíncrono por defecto; con block=True devuelve True/False.
    Multipart construido a mano con urllib para no depender de requests."""
    if not token or not chat_id:
        return False
    import threading
    import urllib.request
    import uuid

    def _send() -> bool:
        try:
            with open(photo_path, "rb") as f:
                photo = f.read()
            boundary = uuid.uuid4().hex
            parts = []
            for name, value in (("chat_id", str(chat_id)),
                                ("caption", caption or ""),
                                ("parse_mode", "HTML")):
                parts.append((
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8"))
            filename = os.path.basename(str(photo_path))
            parts.append((
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8"))
            parts.append(photo)
            parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data=b"".join(parts),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                response.read()
            return True
        except Exception as e:
            if logger:
                logger.debug("Error al enviar foto de Telegram: %s", e)
            return False

    if block:
        return _send()
    threading.Thread(target=_send, daemon=True).start()
    return None


def telegram_get_updates(token, offset=0, timeout=0) -> list:
    """GET a /getUpdates (long-poll opcional). Nunca lanza: devuelve [] ante
    cualquier error. Timeout HTTP corto para no colgar el hilo llamante."""
    if not token:
        return []
    import urllib.request
    import urllib.parse
    try:
        qs = urllib.parse.urlencode({"offset": offset, "timeout": timeout})
        url = f"https://api.telegram.org/bot{token}/getUpdates?{qs}"
        with urllib.request.urlopen(url, timeout=min(timeout + 5, 10)) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("ok"):
            return data.get("result") or []
    except Exception:
        pass
    return []


if __name__ == "__main__":
    assert parse_localized_number('1.234.567') == 1234567
    assert parse_localized_number('1,5M') == 1500000
    assert parse_localized_number('12k') == 12000
    assert parse_localized_number('3,5') == 3.5
    assert parse_localized_number('1,234,567') == 1234567
    assert parse_localized_number('') == 0
    assert parse_localized_number(None) == 0
    assert parse_localized_number('-') == 0
    assert parse_localized_number('1 234 567') == 1234567
    assert parse_localized_number('1.234,56') == 1234.56
    assert parse_localized_number('2Mio') == 2000000
    assert parse_localized_number('1,2mrd') == 1200000000
    assert parse_localized_number('-12.345') == -12345
    assert tg_escape('a<b & c>d') == 'a&lt;b &amp; c&gt;d'
    print("utils.py: asserts OK")

