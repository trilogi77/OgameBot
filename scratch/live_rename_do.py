"""Prueba EN VIVO real: renombra cada planeta/colonia con nombre por defecto usando
GameClient.rename_planet y luego recoge las recompensas de directivas.
Ejecutar con cwd = cuenta:  python /app/scratch/live_rename_do.py
"""
import logging
import random
import sys

sys.path.insert(0, "/app")
from ogbot.config import Config
from ogbot.client import GameClient
from ogbot.planet_names import PLANET_NAMES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("renamedo")

DEFAULTS = {"planeta principal", "colonia"}

cfg = Config.load("config.yaml")
client = GameClient(cfg, log)
client.start()
if not client.login():
    log.error("Login fallido"); sys.exit(1)

planets = client.read_planets()
log.info("Planetas: %s", [(p.name, str(p.coords)) for p in planets])

used = []
for p in planets:
    if (p.name or "").strip().lower() not in DEFAULTS:
        log.info("%s ya tiene nombre personalizado (%r); no se toca.", p.coords, p.name)
        continue
    name = random.choice([n for n in PLANET_NAMES if n not in used])
    ok = client.rename_planet(p, name)
    log.info("rename_planet(%s -> %r) = %s", p.coords, name, ok)
    if ok:
        used.append(name)

# Releer para confirmar los nombres nuevos
client._planet_cache = []
planets2 = client.read_planets()
log.info("Tras renombrar: %s", [(p.name, str(p.coords)) for p in planets2])

# Recoger las recompensas de las misiones que acaban de completarse
claimed = client.claim_directive_rewards()
log.info("Recompensas de directivas recogidas: %d", claimed)

client.stop()
print("OK")
