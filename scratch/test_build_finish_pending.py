import os, sys, types, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain

coords = types.SimpleNamespace(galaxy=1, system=125, position=8, type="planet")
loc = types.SimpleNamespace(coords=coords)

def fake(epoch):
    return types.SimpleNamespace(
        state_cache={"planets": {"1:125:8:planet": {"build_finish_epoch": epoch}}},
        _loc_key=Brain._loc_key.__get__(types.SimpleNamespace()),  # usa la fórmula real
    )

# Epoch futuro -> build pendiente (red de seguridad ante falso negativo del overview).
assert Brain._build_finish_pending(fake(time.time() + 600), loc) is True

# Epoch pasado -> ya terminó, no pendiente.
assert Brain._build_finish_pending(fake(time.time() - 1), loc) is False

# Sin build (0.0) -> no pendiente.
assert Brain._build_finish_pending(fake(0.0), loc) is False

# Sin entrada en caché -> no pendiente.
empty = types.SimpleNamespace(state_cache={"planets": {}}, _loc_key=Brain._loc_key.__get__(types.SimpleNamespace()))
assert Brain._build_finish_pending(empty, loc) is False

print("OK")
