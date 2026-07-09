"""Self-check de la Fase 2 (A1 watchdog de sesión + S1 servicio nocturno).

    python scratch/test_phase2_fixes.py
"""
import os, sys, types, logging, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain


def test_watchdog_escalates():
    calls = {"login": 0}
    client = types.SimpleNamespace(
        stop=lambda: None, start=lambda: None,
        login=lambda: (calls.__setitem__("login", calls["login"] + 1) or True))
    stub = types.SimpleNamespace(
        _empty_read_streak=0, log=logging.getLogger("t"), client=client,
        paused_until=0.0, _apply_server_params=lambda: None,
        _tg_alert=lambda m: None, _save_state=lambda: None)
    # 6 lecturas vacías seguidas: reinicio en la 2ª y la 4ª, pausa en la 6ª.
    t0 = time.time()
    for _ in range(6):
        Brain._watchdog_empty_read(stub)
    assert calls["login"] == 2, calls                    # reinicios en n=2 y n=4
    assert stub.paused_until >= t0, stub.paused_until     # pausa activada en n>=6


def test_watchdog_no_reset_single_fail():
    # Un solo fallo aislado NO reinicia la sesión (evita reinicios por un blip transitorio).
    calls = {"login": 0}
    client = types.SimpleNamespace(
        stop=lambda: None, start=lambda: None,
        login=lambda: (calls.__setitem__("login", calls["login"] + 1) or True))
    stub = types.SimpleNamespace(
        _empty_read_streak=0, log=logging.getLogger("t"), client=client,
        paused_until=0.0, _apply_server_params=lambda: None,
        _tg_alert=lambda m: None, _save_state=lambda: None)
    Brain._watchdog_empty_read(stub)   # n=1
    assert calls["login"] == 0 and stub.paused_until == 0.0


def test_service_night_tick_swallows_errors():
    hits = []
    def boom():
        hits.append("recall"); raise RuntimeError("navegación caída")
    stub = types.SimpleNamespace(
        log=logging.getLogger("t"),
        _process_recall_requests=boom,
        _process_control_requests=lambda: hits.append("control"),
        _poll_telegram_commands=lambda: hits.append("tg"))
    Brain._service_night_tick(stub)   # no debe propagar aunque el primero falle
    assert hits == ["recall", "control", "tg"], hits


if __name__ == "__main__":
    test_watchdog_escalates()
    test_watchdog_no_reset_single_fail()
    test_service_night_tick_swallows_errors()
    print("OK")
