import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import fleet
from ogbot.config import Config
from ogbot.models import Coords

cfg = Config()
cfg.enable_fleetsave = True
cfg.fleetsave_mission = "deploy"

origin = Coords(1, 100, 5)
dests = [origin, Coords(1, 120, 8), Coords(2, 300, 4)]
ships = {"small_cargo": 50}

cfg.fleetsave_recall_halfway = True
plan = fleet.fleetsave_plan(origin, dests, cfg, offline_hours=8.0, fleet_ships=ships)
assert plan["speed_percent"] == 0.1, plan

cfg.fleetsave_recall_halfway = False
plan2 = fleet.fleetsave_plan(origin, dests, cfg, offline_hours=8.0, fleet_ships=ships)
assert plan2["speed_percent"] <= 1.0

print("OK: recall halfway -> 10%; sin recall ->", plan2["speed_percent"])
