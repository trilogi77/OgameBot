"""Self-check de la Ola 2 (autonomía).

    python scratch/test_wave2_fixes.py

1. _apply_server_params corrige los parámetros físicos del universo desde serverData.xml.
2. atomic_write_json(backup=True) + load_json_or_backup recuperan de una corrupción en vez
   de resetear el estado en silencio.
"""
import os, sys, types, logging, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain
from ogbot import utils


def test_apply_server_params():
    cfg = types.SimpleNamespace(
        auto_server_params=True, fleet_speed=2, debris_factor=0.3,
        debris_includes_deut=False, universe_speed=8, farm_with_probes=True,
        espionage_probe_cargo=8, telegram_token="", telegram_chat_id="")
    # serverData real de Regulus (s273-es): x8, flota 5, escombros 0.8, deut en escombros, sonda 5.
    fake_api = types.SimpleNamespace(server_data=lambda: {
        "speed": "8", "speedFleetPeaceful": "5", "debrisFactor": "0.8",
        "deuteriumInDebris": "1", "probeCargo": "5"})
    stub = types.SimpleNamespace(cfg=cfg, api=fake_api, log=logging.getLogger("t"),
                                 _apply_probe_cargo=lambda: None)
    Brain._apply_server_params(stub)
    assert cfg.fleet_speed == 5.0, cfg.fleet_speed
    assert cfg.debris_factor == 0.8, cfg.debris_factor
    assert cfg.debris_includes_deut is True, cfg.debris_includes_deut
    assert cfg.espionage_probe_cargo == 5, cfg.espionage_probe_cargo
    assert cfg.universe_speed == 8, cfg.universe_speed   # ya correcto -> sin cambio


def test_apply_server_params_respects_off():
    cfg = types.SimpleNamespace(auto_server_params=False, fleet_speed=2)
    called = {"n": 0}
    fake_api = types.SimpleNamespace(server_data=lambda: called.__setitem__("n", 1) or {})
    stub = types.SimpleNamespace(cfg=cfg, api=fake_api, log=logging.getLogger("t"),
                                 _apply_probe_cargo=lambda: None)
    Brain._apply_server_params(stub)
    assert cfg.fleet_speed == 2 and called["n"] == 0   # ni siquiera consulta la API


def test_backup_restore():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "state.json")
    utils.atomic_write_json(p, {"v": 1}, backup=True)    # aún sin .bak (no había fichero previo)
    utils.atomic_write_json(p, {"v": 2}, backup=True)    # .bak <- {"v":1}
    assert os.path.exists(p + ".bak")
    with open(p, "w", encoding="utf-8") as f:
        f.write("{ esto no es json valido")              # corromper el principal
    data = utils.load_json_or_backup(p, logging.getLogger("t"))
    assert data == {"v": 1}, data                        # recuperado del .bak
    assert os.path.exists(p + ".corrupt")                # el corrupto se conserva como evidencia


if __name__ == "__main__":
    test_apply_server_params()
    test_apply_server_params_respects_off()
    test_backup_restore()
    print("OK")
