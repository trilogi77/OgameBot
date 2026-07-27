"""Los transportes (planeta<->planeta/luna) deben pasar la ventana hasta el fleetsave
nocturno a send_fleet, que cancela si la VUELTA aterriza después."""
import os, sys, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain
from ogbot.models import Resources, Coords
from ogbot import utils, gamedata as gd

gd.SHIPS["large_cargo"].cargo = 25000

sent = {}


class FakeClient:
    def send_fleet(self, origin, dest, ships, **kw):
        sent.update(kw)
        return True


class FakeBrain:
    cfg = types.SimpleNamespace(keep_resources_buffer=0.0, feed_min_send=5000,
                                active_hours=(8, 24))
    client = FakeClient()
    log = logging.getLogger("test")
    _guard = lambda self: True
    _deduct_ships = lambda self, loc, ships: None
    _feed_sendable = Brain._feed_sendable


src = types.SimpleNamespace(coords=Coords(1, 100, 5),
                            resources=Resources(100000, 0, 0),
                            ships={"large_cargo": 10})
dst = types.SimpleNamespace(coords=Coords(1, 100, 5, "moon"))

Brain._feed_transport(FakeBrain(), src, dst, Resources(50000, 0, 0))

assert "max_round_trip_s" in sent, "el transporte no comprueba la hora de fleetsave"
expected = utils.seconds_until_inactive((8, 24))
assert abs(sent["max_round_trip_s"] - expected) < 60, (sent, expected)

# Sin descanso configurado (24h activo) la ventana es prácticamente infinita.
FakeBrain.cfg.active_hours = (0, 24)
sent.clear()
Brain._feed_transport(FakeBrain(), src, dst, Resources(50000, 0, 0))
assert sent["max_round_trip_s"] > 100000, sent

print("OK: transporte limitado por la ventana hasta el fleetsave")
