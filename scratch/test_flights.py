import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import build_flights, parse_time_to_seconds, _split_ships_cargo

# Parser canónico: HH:MM:SS, Xh Ym Zs y segundos.
assert parse_time_to_seconds("1:02:03") == 3723
assert parse_time_to_seconds("12m 30s") == 750
assert parse_time_to_seconds("45") == 45

# Separar carga (es/en) de naves.
ships, cargo = _split_ships_cargo({"Cazador ligero": 10, "Metal": 1000, "Crystal": 50, "Deuterio": 5})
assert ships == {"Cazador ligero": 10}, ships
assert cargo == {"metal": 1000, "crystal": 50, "deut": 5}, cargo

# Vuelo completo (fuente detallada).
mvs = [{"mission": "3", "origin": "1:2:3", "destination": "1:2:4", "arrival_text": "0:10:00",
        "ships": {"Nave grande de carga": 20, "Metal": 5000},
        "origin_type": "planet", "dest_type": "moon"}]
fl = build_flights(mvs, 1000.0)
f = fl[0]
assert f["mission"] == "Transporte" and f["ships"] == {"Nave grande de carga": 20}
assert f["cargo"]["metal"] == 5000 and f["arrival_epoch"] == 1600 and f["dest_type"] == "moon"

# Las flotas hostiles entrantes se excluyen (no son vuelos propios).
mvs_h = [{"mission": "1", "origin": "9:9:9", "destination": "1:2:3",
          "arrival_text": "0:01:00", "ships": {}, "is_hostile": True}]
assert build_flights(mvs_h, 1000.0) == []

# Merge: event_list (sin naves) conserva las del previo; epoch ~estable -> mismo bucket.
mvs2 = [{"mission": "3", "origin": "1:2:3", "destination": "1:2:4", "arrival_text": "0:05:00",
         "ships": {}, "origin_type": "planet", "dest_type": "moon"}]
fl2 = build_flights(mvs2, 1300.0, prev=fl)   # 1300 + 300 = 1600 -> mismo bucket que el previo
assert fl2[0]["ships"] == {"Nave grande de carga": 20}, fl2[0]["ships"]
assert fl2[0]["cargo"]["metal"] == 5000

# Dos flotas en la MISMA ruta/misión con distinta llegada NO intercambian naves.
prevA = build_flights([
    {"mission": "15", "origin": "1:2:3", "destination": "1:2:16", "arrival_text": "0:10:00",
     "ships": {"Cazador ligero": 100}},
    {"mission": "15", "origin": "1:2:3", "destination": "1:2:16", "arrival_text": "1:00:00",
     "ships": {"Nave grande de carga": 500}},
], 1000.0)
cur = build_flights([
    {"mission": "15", "origin": "1:2:3", "destination": "1:2:16", "arrival_text": "0:09:30", "ships": {}},
    {"mission": "15", "origin": "1:2:3", "destination": "1:2:16", "arrival_text": "0:59:30", "ships": {}},
], 1030.0, prev=prevA)
assert cur[0]["ships"] == {"Cazador ligero": 100}, cur[0]["ships"]
assert cur[1]["ships"] == {"Nave grande de carga": 500}, cur[1]["ships"]

print("OK")
