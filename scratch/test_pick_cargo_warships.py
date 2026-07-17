import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.fleet import pick_cargo_ships
from ogbot import gamedata as gd

# Capacidades por defecto usadas abajo (por si otro test las mutó en su propio proceso).
gd.SHIPS["large_cargo"].cargo = 25000
gd.SHIPS["small_cargo"].cargo = 5000

# 1) Con cargueros dedicados suficientes NO se tocan naves de guerra.
assert pick_cargo_ships({"large_cargo": 2, "battleship": 100}, 40000) == {"large_cargo": 2}

# 2) Sin NGC/NPC, cae a naves de guerra (battleship cap 1500 -> ceil(3000/1500)=2).
assert pick_cargo_ships({"battleship": 100}, 3000) == {"battleship": 2}

# 3) Carguero corto + relleno con nave de guerra (1 NGC=25000, faltan 5000 -> destroyer cap 2000 x3).
got = pick_cargo_ships({"large_cargo": 1, "destroyer": 10}, 30000)
assert got == {"large_cargo": 1, "destroyer": 3}, got

# 4) allow_warships=False: solo naves de guerra -> no envía nada.
assert pick_cargo_ships({"battleship": 100}, 3000, allow_warships=False) == {}

# 5) Orden por bodega descendente: reaper (10000) antes que destroyer (2000).
assert pick_cargo_ships({"reaper": 5, "destroyer": 50}, 8000) == {"reaper": 1}

print("OK")
