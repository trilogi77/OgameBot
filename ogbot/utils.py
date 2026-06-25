"""
utils.py
========
Herramientas transversales: logging, comportamiento humanizado y rate limiting.
La "humanización" reduce (no elimina) el riesgo de detección.
"""
from __future__ import annotations
import logging
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


def send_telegram_message(token: str, chat_id: str, text: str, logger=None) -> None:
    """Envía un mensaje a un chat de Telegram de forma asíncrona para no bloquear el bot."""
    if not token or not chat_id:
        return
    import threading
    import urllib.request
    import urllib.parse
    import json

    def _send():
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read()
        except Exception as e:
            if logger:
                logger.debug("Error al enviar mensaje de Telegram: %s", e)

    threading.Thread(target=_send, daemon=True).start()

