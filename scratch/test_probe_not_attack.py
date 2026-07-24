"""Verifica que un SONDEO entrante (misión 6, marcado hostil por OGame) NO se trate
como ataque: no debe poblar under_attack, no debe evadir ni hacer panic-build; solo
avisar vía _watch_incoming_spy. Un ATAQUE real (misión 1) sí debe evadir/panic."""
import os, sys, tempfile, logging, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain

os.chdir(tempfile.mkdtemp())


class _Coords:
    def __init__(self, g, s, p, t="planet"):
        self.galaxy, self.system, self.position, self.type = g, s, p, t
    def tuple(self): return (self.galaxy, self.system, self.position)


class _Planet:
    def __init__(self, g, s, p, t="planet"):
        self.coords = _Coords(g, s, p, t)
        self.has_moon = False
        self.moon = None
        self.ships = {"light_fighter": 100}
        self.resources = types.SimpleNamespace(metal=10000, crystal=0, deut=0)


class FakeClient:
    def __init__(self, mvs, planets):
        self._mvs = mvs
        self._planets = planets
    def read_movements(self): return self._mvs
    def read_planets(self): return self._planets
    def read_planet_state(self, loc): pass


def make_brain(mvs):
    planets = [_Planet(4, 5, 6)]
    cfg = types.SimpleNamespace(
        enable_attack_escape=True, enable_attack_check=True,
        enable_spy_watch=False, telegram_token="", telegram_chat_id="",
        spy_watch_cooldown_mins=30)
    b = types.SimpleNamespace(
        log=logging.getLogger("t"), cfg=cfg,
        client=FakeClient(mvs, planets),
        last_planets=planets, escaped_fleets=[],
        telegram_notified_attacks={}, recent_attacks={}, last_hostile_epoch=0.0,
        _spy_seen={})
    b.record_session_action = lambda *a, **k: None
    b._save_state = lambda: None
    b._last_known_ships = lambda coords: {}
    b._last_known_loc = lambda c, t: None
    # Espías de acción: registran si se dispara evasión o panic.
    calls = {"escape": 0, "panic": 0}
    b._escape_attack_loc = lambda *a, **k: calls.__setitem__("escape", calls["escape"] + 1)
    b._panic_build_resources = lambda *a, **k: calls.__setitem__("panic", calls["panic"] + 1)
    b._watch_incoming_spy = types.MethodType(brain.Brain._watch_incoming_spy, b)
    b._next_own_fleet_recheck = types.MethodType(brain.Brain._next_own_fleet_recheck, b)
    b._check_and_escape_attacks = types.MethodType(brain.Brain._check_and_escape_attacks, b)
    return b, calls


def spy_mv():
    return {"mission": "6", "is_hostile": True, "is_return": False,
            "destination": "4:5:6", "dest_type": "planet",
            "origin": "1:2:3", "arrival_text": "0h 2m 0s", "arrival_epoch": 0}


def attack_mv():
    return {"mission": "1", "is_hostile": True, "is_return": False,
            "destination": "4:5:6", "dest_type": "planet",
            "origin": "1:2:3", "arrival_text": "0h 2m 0s", "arrival_epoch": 0}


def spy_probes_only_mv():
    # Sondeo con composición visible: solo sondas -> sigue siendo espionaje.
    m = spy_mv()
    m["ships"] = {"Sonda de espionaje": 6}
    return m


def spy_with_fleet_mv():
    # Sondeo con sondas + naves de guerra -> ataque camuflado.
    m = spy_mv()
    m["ships"] = {"Sonda de espionaje": 3, "Cazador ligero": 80}
    return m


# 1) Solo un SONDEO entrante -> NO evasión, NO panic.
b, calls = make_brain([spy_mv()])
b._check_and_escape_attacks()
assert calls["escape"] == 0, "un sondeo NO debe disparar evasión"
assert calls["panic"] == 0, "un sondeo NO debe disparar panic-build"
assert b.last_hostile_epoch > 0, "el sondeo sí marca actividad hostil (C7)"

# 2) Un ATAQUE real -> sí evasión y panic (llega en 2 min < 5 min, recursos > 5000).
b, calls = make_brain([attack_mv()])
b._check_and_escape_attacks()
assert calls["escape"] == 1, "un ataque real debe disparar evasión"
assert calls["panic"] == 1, "un ataque real debe disparar panic-build"

# 3) Sondeo con SOLO sondas visibles -> sigue siendo espionaje: NO evade, NO panic.
b, calls = make_brain([spy_probes_only_mv()])
b._check_and_escape_attacks()
assert calls["escape"] == 0, "un sondeo de solo sondas NO debe evadir"
assert calls["panic"] == 0, "un sondeo de solo sondas NO debe hacer panic"

# 4) Sondeo con FLOTA de guerra (ataque camuflado de espionaje) -> evade y panic.
b, calls = make_brain([spy_with_fleet_mv()])
b._check_and_escape_attacks()
assert calls["escape"] == 1, "un sondeo con flota real debe evadir"
assert calls["panic"] == 1, "un sondeo con flota real debe hacer panic"

# 5) Unidad: spy_has_attack_fleet detecta la flota (es/en) e ignora sondas y carga.
assert brain.spy_has_attack_fleet({"ships": {"Sonda de espionaje": 6}}) is False
assert brain.spy_has_attack_fleet({"ships": {"Espionage Probe": 6}}) is False
assert brain.spy_has_attack_fleet({"ships": {}}) is False
assert brain.spy_has_attack_fleet({"ships": {"Sonda de espionaje": 3, "Cazador ligero": 80}}) is True
assert brain.spy_has_attack_fleet({"ships": {"Metal": 5000, "Sonda de espionaje": 2}}) is False

print("OK")
