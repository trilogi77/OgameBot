"""E2E en vivo de la fase de lectura de mensajes con gating (ejecutar en docker con
cwd=accounts/<cuenta>): login real, update_imperial_stats() dos veces (la primera
siembra/lee lo marcado; la segunda debe omitirse si el sobre está a 0) y prueba
directa de read_message_category(5) + read_message_reports(21) (vuelta a Flotas)."""
import sys

sys.path.insert(0, "/app")
from ogbot import brain
from ogbot.config import Config


def main():
    cfg = Config.load("config.yaml")
    cfg.headless = True
    cfg.dry_run = True
    b = brain.Brain(cfg)
    b.client.start()
    try:
        if not b.client.login():
            print("LOGIN FAILED")
            return
        unread = b.client.unread_messages_count()
        print(f"[live] sobre de mensajes: {unread!r}")
        ov = b.client.read_messages_overview(unread)
        print(f"[live] overview (con déficit del sobre): {ov!r}")
        print(f"[live] message_tab_active(20)={b.client.message_tab_active(20)}")

        print("[live] --- update_imperial_stats() 1ª pasada ---")
        b.update_imperial_stats()
        print("[live] --- update_imperial_stats() 2ª pasada (esperado: omitida si sobre=0) ---")
        b.update_imperial_stats()

        print("[live] --- read_message_category(5) (Universo) ---")
        msgs = b.client.read_message_category(5)
        print(f"[live] Universo: {len(msgs)} mensajes; ids={[m['id'] for m in msgs]}")
        print("[live] --- read_message_reports(21) tras estar en Universo (autocuración a Flotas) ---")
        msgs = b.client.read_message_reports(21)
        print(f"[live] tab 21: {len(msgs)} mensajes")
        print("[live] sobre al final:", b.client.unread_messages_count())
        print("LIVE OK")
    finally:
        b.client.stop()


if __name__ == "__main__":
    main()
