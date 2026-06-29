import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain
from ogbot.models import Resources
from ogbot import gamedata as gd

gd.SHIPS["large_cargo"].cargo = 25000
gd.SHIPS["small_cargo"].cargo = 5000

def fake(buffer=0.0):
    return types.SimpleNamespace(cfg=types.SimpleNamespace(keep_resources_buffer=buffer))

need = Resources(40000, 0, 0)

# Recursos y cargueros de sobra -> cubre todo el need (candidato a "1 solo envío").
rich = types.SimpleNamespace(resources=Resources(1_000_000, 0, 0), ships={"large_cargo": 100})
assert abs(Brain._feed_sendable(fake(), rich, need).total() - 40000) < 1

# Pocos recursos -> envío parcial (< need).
poor = types.SimpleNamespace(resources=Resources(10000, 0, 0), ships={"large_cargo": 100})
assert abs(Brain._feed_sendable(fake(), poor, need).total() - 10000) < 1

# Recursos pero SIN cargueros -> 0.
nocargo = types.SimpleNamespace(resources=Resources(1_000_000, 0, 0), ships={})
assert Brain._feed_sendable(fake(), nocargo, need).total() == 0

# Limitado por capacidad: 1 large_cargo (25k) aunque sobren recursos -> 25k (no cubre todo).
fewcargo = types.SimpleNamespace(resources=Resources(1_000_000, 0, 0), ships={"large_cargo": 1})
assert abs(Brain._feed_sendable(fake(), fewcargo, need).total() - 25000) < 1

# Buffer 0.5 -> solo la mitad del excedente es enviable.
half = types.SimpleNamespace(resources=Resources(20000, 0, 0), ships={"large_cargo": 100})
assert abs(Brain._feed_sendable(fake(0.5), half, need).total() - 10000) < 1

print("OK")
