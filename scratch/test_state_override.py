import os, sys, json, tempfile, logging, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain

def new_fake():
    return types.SimpleNamespace(
        log=logging.getLogger("t"),
        state_cache={"research": {"levels": {"astrophysics": 5}},
                     "planets": {"1:2:3:planet": {"buildings": {"robotics_factory": 6}}}},
        _force_resync=False,
        _resync_targets=set(),
        _save_state_cache=lambda: None,
    )

os.chdir(tempfile.mkdtemp())
fake = new_fake()

# 1) Corrección de un edificio.
with open("state_overrides.json", "w", encoding="utf-8") as f:
    json.dump([{"kind": "building", "coords": "1:2:3", "is_moon": False,
                "name": "robotics_factory", "level": 7}], f)
brain.Brain._apply_pending_gui_requests(fake)
assert fake.state_cache["planets"]["1:2:3:planet"]["buildings"]["robotics_factory"] == 7
assert not os.path.exists("state_overrides.json")  # se consume

# 2) Corrección de una investigación.
with open("state_overrides.json", "w", encoding="utf-8") as f:
    json.dump([{"kind": "research", "name": "astrophysics", "level": 9}], f)
brain.Brain._apply_pending_gui_requests(fake)
assert fake.state_cache["research"]["levels"]["astrophysics"] == 9

# 3) Re-lectura por planeta: solo esa ubicación, sin tocar el flag global.
with open("force_resync.json", "w", encoding="utf-8") as f:
    json.dump({"targets": ["1:2:3:planet"]}, f)
brain.Brain._apply_pending_gui_requests(fake)
assert "1:2:3:planet" in fake._resync_targets
assert fake._force_resync is False
assert not os.path.exists("force_resync.json")

# 4) Re-lectura total.
fake = new_fake()
with open("force_resync.json", "w", encoding="utf-8") as f:
    json.dump({"all": True}, f)
brain.Brain._apply_pending_gui_requests(fake)
assert fake._force_resync is True

# 5) Override a un planeta no cacheado: se ignora sin romper.
with open("state_overrides.json", "w", encoding="utf-8") as f:
    json.dump([{"kind": "building", "coords": "9:9:9", "is_moon": False,
                "name": "metal_mine", "level": 3}], f)
brain.Brain._apply_pending_gui_requests(fake)
assert "9:9:9:planet" not in fake.state_cache["planets"]

print("OK")
