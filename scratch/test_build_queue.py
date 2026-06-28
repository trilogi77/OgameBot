import os, sys, time, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain
from ogbot.models import Resources


class P:
    def __init__(self, levels, res, in_progress=False):
        self._levels = levels
        self.resources = res
        self.coords = "1:2:3"
        self.building_in_progress = in_progress
        self.building_remaining_seconds = 0
        self.max_temp = 30
    def lvl(self, name):
        return self._levels.get(name, 0)


def fake(queue):
    ns = types.SimpleNamespace(
        log=logging.getLogger("t"),
        cfg=types.SimpleNamespace(keep_resources_buffer=0.10, universe_speed=1.0),
        research_levels={"plasma_tech": 0},
        _get_planet_setting=lambda p, k, d: (queue if k == "build_queue"
                                             else (True if k == "enable_build_queue" else d)),
    )
    ns._active_queue_entry = types.MethodType(Brain._active_queue_entry, ns)
    return ns

# 1) _active_queue_entry: primera entrada NO cumplida -> (real_name, real_lvl, cost).
q = [{"building": "metal_mine", "target_level": 5},
     {"building": "crystal_mine", "target_level": 4}]
e1 = Brain._active_queue_entry(fake(q), P({"metal_mine": 5, "crystal_mine": 2}, Resources(0, 0, 0)))
assert e1 and e1[0] == "crystal_mine" and e1[1] == 3, e1   # metal hecho; crystal 2 -> 3
assert Brain._active_queue_entry(fake(q), P({"metal_mine": 5, "crystal_mine": 4}, Resources(0, 0, 0))) is None
# Nombre inválido NO bloquea (se ignora -> None, la economía sigue).
assert Brain._active_queue_entry(fake([{"building": "no_existe", "target_level": 9}]),
                                 P({}, Resources(0, 0, 0))) is None

# 2) ETA: 2000 metal a 1000/h -> 2 h (toma el recurso más lento).
fe = types.SimpleNamespace(research_levels={"plasma_tech": 0},
                           _hourly_production=lambda p, plasma: {"metal": 1000, "crystal": 1000, "deut": 1000})
eta = Brain._eta_to_afford(fe, P({}, Resources(0, 0, 0)), Resources(2000, 500, 0))
assert abs(eta - 2.0) < 0.01, eta

# 3) Con recursos -> construye y devuelve un 'wake' futuro.
built = []
fb = fake([{"building": "metal_mine", "target_level": 3}])
fb.client = types.SimpleNamespace(build=lambda planet, comp, name: (built.append((comp, name)) or True))
fb._guard = lambda: True
fb.record_session_action = lambda *a, **k: None
p = P({"metal_mine": 0}, Resources(1_000_000, 1_000_000, 1_000_000))
w = Brain._build_queue_step(fb, p)
assert built == [("supplies", "metal_mine")], built
assert p.building_in_progress is True and w and w > time.time()

# 4) Si ya hay algo construyéndose -> esperar a que termine (no encola otra).
pb = P({"metal_mine": 0}, Resources(0, 0, 0), in_progress=True)
pb.building_remaining_seconds = 120
w2 = Brain._build_queue_step(fake([{"building": "metal_mine", "target_level": 3}]), pb)
assert w2 and abs(w2 - (time.time() + 120)) < 5, w2

# 5) _hourly_production re-lee si cambian los niveles de mina/energía (producción obsoleta).
reads = []
fh = types.SimpleNamespace(
    cfg=types.SimpleNamespace(universe_speed=1.0),
    state_cache={"planets": {"1:2:3:planet": {
        "hourly_production": {"metal": 100, "crystal": 50, "deut": 20},
        "hourly_production_at": time.time(),
        "hourly_production_levels": [10, 10, 5, 12, 0],
    }}},
    _loc_key=lambda c: "1:2:3:planet",
    _save_state_cache=lambda: None,
    client=types.SimpleNamespace(
        read_hourly_production=lambda p: (reads.append(1) or {"metal": 999, "crystal": 1, "deut": 1})),
)
lv = {"metal_mine": 10, "crystal_mine": 10, "deut_synth": 5, "solar_plant": 12, "fusion_reactor": 0}
# Mismos niveles que la firma cacheada -> usa la caché (no relee la página).
assert Brain._hourly_production(fh, P(dict(lv), Resources(0, 0, 0)), 0)["metal"] == 100
assert reads == [], reads
# Sube la mina de metal -> firma distinta -> relee la producción real.
lv2 = dict(lv, metal_mine=11)
assert Brain._hourly_production(fh, P(lv2, Resources(0, 0, 0)), 0)["metal"] == 999
assert reads == [1], reads

print("OK")
