import os, sys, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain
from ogbot.planet_names import PLANET_NAMES


class P:
    def __init__(self, pid, name, coords="1:2:3"):
        self.id = pid
        self.name = name
        g, s, pos = (int(x) for x in coords.split(":"))
        self.coords = types.SimpleNamespace(galaxy=g, system=s, position=pos)


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
    def rename_planet(self, planet, name):
        self.calls.append((planet.id, name))
        if self.fail:
            return False
        planet.name = name
        return True


def make(client, cfg_on=True):
    return types.SimpleNamespace(
        log=logging.getLogger("t"),
        cfg=types.SimpleNamespace(enable_planet_rename=cfg_on),
        client=client,
        renamed_planets=set(),
        used_planet_names=[],
        _save_state=lambda: None,
        _DEFAULT_PLANET_NAMES=brain.Brain._DEFAULT_PLANET_NAMES,
    )


# 1) Renombra planeta principal y colonia (nombres por defecto), sin repetir, una vez.
c = FakeClient()
b = make(c)
planets = [P("planet-1", "Planeta principal"), P("planet-2", "Colonia", "1:2:5")]
brain.Brain._rename_step(b, planets)
assert len(c.calls) == 2, c.calls
assert c.calls[0][1] != c.calls[1][1], "no debe repetir nombre"           # sin repetir
assert all(n in PLANET_NAMES for _, n in c.calls)
assert b.renamed_planets == {"planet-1", "planet-2"}
assert len(b.used_planet_names) == 2

# 2) Segunda pasada: ya renombrados -> no vuelve a llamar.
brain.Brain._rename_step(b, planets)
assert len(c.calls) == 2, "no debe renombrar de nuevo"

# 3) Guarda de nombre manual: un planeta con nombre no-default se marca pero NO se renombra.
c = FakeClient()
b = make(c)
brain.Brain._rename_step(b, [P("planet-9", "MiBaseSecreta")])
assert c.calls == [], "no debe pisar un nombre personalizado"
assert "planet-9" in b.renamed_planets

# 4) Reintento tras fallo: si rename falla, NO se marca (se reintenta luego).
c = FakeClient(fail=True)
b = make(c)
brain.Brain._rename_step(b, [P("planet-3", "Colonia")])
assert b.renamed_planets == set(), "un fallo no debe marcar como renombrado"
assert b.used_planet_names == []

# 5) Flag off -> no hace nada.
c = FakeClient()
b = make(c, cfg_on=False)
brain.Brain._rename_step(b, [P("planet-4", "Colonia")])
assert c.calls == []

# 6) Sin nombres libres -> no revienta.
c = FakeClient()
b = make(c)
b.used_planet_names = list(PLANET_NAMES)
brain.Brain._rename_step(b, [P("planet-5", "Colonia")])
assert c.calls == []

print("OK")
