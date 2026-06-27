import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import parse_found_ships

# Informe real del usuario (nombre y luego cifra, separador de miles ".")
txt = "Botín\nCazador ligero 1.310\nExplorador 17\nNave pequeña de carga 23"
got = parse_found_ships(txt)
assert got == {"light_fighter": 1310, "pathfinder": 17, "small_cargo": 23}, got

# Varias naves seguidas: el regex viejo asignaba la cifra de la nave anterior.
txt2 = "Nave de batalla 285 Sonda de espionaje 1"
assert parse_found_ships(txt2) == {"battleship": 285, "espionage_probe": 1}, parse_found_ships(txt2)

# "cruiser" no debe casar dentro de "battlecruiser".
assert parse_found_ships("Acorazado 4") == {"battlecruiser": 4}
assert parse_found_ships("Battlecruiser 4") == {"battlecruiser": 4}
assert parse_found_ships("Crucero 9") == {"cruiser": 9}

# Sin naves -> dict vacío.
assert parse_found_ships("Encontramos una estacion pirata desierta.") == {}

print("OK")
