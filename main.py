#!/usr/bin/env python3
"""
Punto de entrada de OGBot.

Uso:
    export OGBOT_USER="tu_email@dominio.com"
    export OGBOT_PASS="tu_password"
    python main.py --config config.yaml

Empieza SIEMPRE con dry_run: true en config.yaml para ver qué haría sin riesgo.
Para correr en segundo plano:  nohup python main.py &   (Linux/Mac)
                               o como servicio systemd (ver README).
"""
import argparse
from ogbot.config import Config
from ogbot.brain import Brain


def main():
    ap = argparse.ArgumentParser(description="OGBot - gestor autónomo de OGame")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--once", action="store_true", help="ejecuta un único ciclo y sale")
    args = ap.parse_args()

    import os
    if not os.path.exists(args.config):
        print(f"La configuración '{args.config}' no existe (primera ejecución).")
        print("Iniciando el servidor GUI en tu navegador para configurar el bot...")
        import gui
        gui.run()
        return

    # Comprobación de PID único para evitar instancias concurrentes en conflicto
    pid_file = "bot.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            import subprocess
            is_running = False
            if os.name == 'nt':
                out = subprocess.check_output(f'tasklist /FI "PID eq {old_pid}"', shell=True, text=True, stderr=subprocess.DEVNULL)
                if str(old_pid) in out:
                    is_running = True
            else:
                try:
                    os.kill(old_pid, 0)
                    is_running = True
                except OSError:
                    pass
            if is_running and old_pid != os.getpid():
                print(f"El bot ya está en ejecución (PID {old_pid}) para evitar conflictos de navegación. Abortando.")
                return
        except Exception:
            pass

    # Guardar nuestro PID
    try:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    try:
        cfg = Config.load(args.config)
        brain = Brain(cfg)
        if args.once:
            brain.client.start()
            if brain.client.login():
                brain.initialize_session_stats()
                brain.cycle()
            brain.client.stop()
        else:
            brain.run_forever()
    finally:
        # Eliminar bot.pid si somos nosotros
        try:
            if os.path.exists(pid_file):
                with open(pid_file, "r") as f:
                    saved_pid = int(f.read().strip())
                if saved_pid == os.getpid():
                    os.remove(pid_file)
        except Exception:
            pass


if __name__ == "__main__":
    main()
