import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import (build_flights, parse_time_to_seconds, _split_ships_cargo,
                         _retain_unlanded)

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

# El epoch ABSOLUTO del DOM (mv.arrival_epoch) tiene prioridad sobre el contador.
mv_abs = [{"mission": "3", "origin": "1:2:3", "destination": "1:2:4",
           "arrival_text": "0:10:00", "arrival_epoch": 5000, "ships": {}}]
assert build_flights(mv_abs, 1000.0)[0]["arrival_epoch"] == 5000

# departure_epoch desde el reversal absoluto de OGame (epoch unix): salida = 2*now - reversal.
NOW = 2_000_000_000
mv_rev = [{"mission": "3", "origin": "1:2:3", "destination": "1:2:4",
           "arrival_text": "0:10:00", "reversal_epoch": NOW + 300, "ships": {}}]   # vuelve en 300 s
assert build_flights(mv_rev, float(NOW))[0]["departure_epoch"] == NOW - 300
# departure_epoch desde el contador de regreso (lo ya volado): salida = now - 500.
mv_rt = [{"mission": "3", "origin": "1:2:3", "destination": "1:2:4",
          "arrival_text": "0:10:00", "reversal_text": "0:08:20", "ships": {}}]  # 500 s
assert build_flights(mv_rt, 1000.0)[0]["departure_epoch"] == 500

# departure_epoch desde fecha+hora absoluta del hover (vuelve_en = epoch de esa fecha).
import time as _t
ret = _t.mktime((2030, 1, 1, 0, 30, 0, 0, 0, -1))   # hora de retorno conocida (local)
now_dt = ret - 600                                   # ahora, 10 min antes del retorno
mv_dt = [{"mission": "3", "origin": "1:2:3", "destination": "1:2:4", "arrival_text": "0:10:00",
          "reversal_text": "El regreso será el 01.01.2030 00:30:00", "ships": {}}]
dep = build_flights(mv_dt, now_dt)[0]["departure_epoch"]
assert abs(dep - (ret - 1200)) <= 2, dep   # salida = 2*now - ret = ret - 1200

# Dedup: una fila de "vuelta" espuria con la MISMA llegada exacta que la ida se fusiona en
# una sola, conservando naves y el tipo de cuerpo más específico (luna).
dup = build_flights([
    {"mission": "4", "origin": "4:168:8", "destination": "1:125:8", "arrival_epoch": 5000,
     "is_return": True, "dest_type": "moon", "ships": {}},
    {"mission": "4", "origin": "4:168:8", "destination": "1:125:8", "arrival_epoch": 5000,
     "is_return": False, "dest_type": "planet", "ships": {"Nave grande de carga": 13}},
], 1000.0)
assert len(dup) == 1, dup
assert dup[0]["is_return"] is False and dup[0]["dest_type"] == "moon", dup[0]
assert dup[0]["ships"] == {"Nave grande de carga": 13}, dup[0]["ships"]

# Ida y vuelta vinculadas (distinta llegada) se AGRUPAN en una sola tarjeta: la ida hereda la
# hora de vuelta a casa y deriva su salida; la pata de vuelta ya no aparece suelta.
pair = build_flights([
    {"mission": "3", "origin": "1:2:3", "destination": "1:2:4", "arrival_epoch": 1600,
     "is_return": False, "ships": {"Nave grande de carga": 20}},
    {"mission": "3", "origin": "1:2:3", "destination": "1:2:4", "arrival_epoch": 2200,
     "is_return": True, "ships": {}},
], 1000.0)
assert len(pair) == 1, pair
assert pair[0]["is_return"] is False
assert pair[0]["departure_epoch"] == 2 * 1600 - 2200, pair[0]["departure_epoch"]   # = 1000
assert pair[0]["return_arrival_epoch"] == 2200, pair[0]

# Una vuelta SIN ida visible (la ida ya llegó) se conserva como tarjeta propia.
solo = build_flights([
    {"mission": "1", "origin": "1:2:3", "destination": "9:9:9", "arrival_epoch": 2200,
     "is_return": True, "ships": {"Cazador ligero": 5}},
], 1000.0)
assert len(solo) == 1 and solo[0]["is_return"] is True, solo

# Lectura vacía (fallo transitorio): NO se vacía el panel; se conservan los vuelos aún en curso
# y se descartan los ya aterrizados.
prev_keep = [{"arrival_epoch": 2000, "origin": "a"}, {"return_arrival_epoch": 3000, "origin": "b"}]
prev_drop = [{"arrival_epoch": 500, "origin": "c"}]
assert _retain_unlanded([], prev_keep + prev_drop, 1000.0) == prev_keep
# Si la lectura trae datos, se usan tal cual (ignora el previo).
assert _retain_unlanded([{"origin": "x"}], prev_keep, 1000.0) == [{"origin": "x"}]
# Sin previo y lectura vacía -> lista vacía (no hay flotas de verdad).
assert _retain_unlanded([], [], 1000.0) == []

print("OK")
