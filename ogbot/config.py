"""
config.py
=========
Carga la configuración desde config.yaml. Todos los parámetros estratégicos
y de seguridad viven aquí para que NO tengas que tocar el código.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List, Tuple
import yaml


@dataclass
class Config:
    # --- Cuenta / servidor ---
    universe: str = ""               # ej. "Quantum"
    country: str = "es"              # comunidad/idioma del servidor
    username: str = ""               # email de Gameforge
    password: str = ""               # mejor vía variable de entorno OGBOT_PASS
    server_url: str = ""             # ej. https://s123-es.ogame.gameforge.com
    # Proxy del navegador (opcional; útil en VPS si GameForge bloquea el login por IP)
    proxy_server: str = ""           # ej. http://host:puerto o socks5://host:puerto
    proxy_username: str = ""
    proxy_password: str = ""

    # --- Parámetros del universo (varían entre universos) ---
    universe_speed: float = 1.0      # economía
    fleet_speed: float = 1.0         # velocidad de flotas
    debris_factor: float = 0.30      # % de naves destruidas que va a escombros
    debris_includes_deut: bool = False
    loot_percent: float = 0.50       # % de recursos saqueables
    trade_ratio: Tuple[float, float, float] = (2.5, 1.5, 1.0)

    # --- Estrategia económica ---
    enable_economy: bool = True
    enable_build_queue: bool = True   # cola de construcción manual por planeta (tipo Comandante)
    target_mine_ratio_payback_hours: float = 16.0  # umbral payback para subir mina
    keep_resources_buffer: float = 0.10            # % de recursos a no gastar
    enable_fusion_reactor: bool = True
    fusion_reactor_solar_offset: int = 25            # Niveles que debe tener la planta solar por encima del reactor de fusión
    max_mine_level: int = 40
    target_metal_mine: int = 99
    target_crystal_mine: int = 99
    target_deut_synth: int = 99
    storage_fill_trigger_percent: float = 0.90
    enable_facilities: bool = True
    target_robotics_factory: int = 0
    target_shipyard: int = 0
    target_research_lab: int = 0
    target_nanite_factory: int = 0

    # --- Investigación ---
    enable_research: bool = True
    research_priority: List[str] = field(default_factory=lambda: [
        "astrophysics", "plasma_tech", "computer_tech", "combustion_drive",
        "impulse_drive", "hyperspace_drive", "espionage_tech", "weapons_tech",
        "shielding_tech", "armor_tech", "hyperspace_tech", "energy_tech",
        "laser_tech", "ion_tech"
    ])
    research_weights: dict = field(default_factory=lambda: {
        "astrophysics": 2.0,
        "plasma_tech": 1.8,
        "computer_tech": 1.5,
        "combustion_drive": 1.2,
        "impulse_drive": 1.1,
        "hyperspace_drive": 1.0,
        "espionage_tech": 1.0,
        "weapons_tech": 1.0,
        "shielding_tech": 1.0,
        "armor_tech": 1.0,
        "hyperspace_tech": 0.9,
        "energy_tech": 0.5,
        "laser_tech": 0.5,
        "ion_tech": 0.5,
    })
    research_caps: dict = field(default_factory=lambda: {
        "laser_tech": 12,
        "ion_tech": 5,
        "energy_tech": 8,
        "hyperspace_tech": 15,
    })


    # --- Farming ---
    enable_farming: bool = True
    enable_fleet_building: bool = True
    enable_cargo_building: bool = False
    max_attack_targets_per_cycle: int = 8
    min_loot_value: float = 50_000          # ignorar objetivos por debajo de esto
    max_target_distance_systems: int = 200
    only_inactive_targets: bool = True       # solo jugadores inactivos (más seguro/legal-grey)
    avoid_strong_players: bool = True
    farming_attack_cooldown_hours: float = 2.0  # tiempo de espera (en horas) para volver a atacar/espiar
    # Servidores donde las sondas de espionaje tienen bodega y son más rápidas: permite
    # atacar inactivos con sondas (raid con sondas) en lugar de cargueros.
    farm_with_probes: bool = False
    espionage_probe_cargo: int = 0   # bodega real por sonda en este servidor (0 = usar gamedata)
    # Reciclar los escombros de los ataques con combate: los escombros simulados cuentan
    # como beneficio, y tras enviar la flota se lanza 1 sonda suicida (muere contra la
    # defensa y crea el campo de escombros) para poder despachar los recicladores
    # calculados sin esperar a que llegue la flota principal.
    farm_recycle_debris: bool = True
    # Auto-flota de ataque: ignora attacker_fleet_template y el bot elige la escolta
    # militar por simulación (mínimo que gana >=95% con margen) según el hangar del
    # origen y la defensa del informe; los cargueros se dimensionan al botín.
    farm_auto_fleet: bool = False
    # Despertar al volver la flota de farmeo para relanzar la ronda (como las expediciones)
    farming_smart_schedule: bool = True
    # Descartar el ataque si el informe de espionaje muestra actividad reciente (<60 min):
    # en un "inactivo" es señal de trampa o de que va a mover los recursos.
    farming_skip_active_targets: bool = True
    # Blacklist de granjas pobres: tras 3+ raids con botín real medio < min_loot_value,
    # no volver a espiarlas/atacarlas durante estos días (0 = desactivado).
    farming_blacklist_days: float = 7.0
    attacker_fleet_template: dict = field(default_factory=lambda: {
        "small_cargo": 0, "large_cargo": 0, "light_fighter": 0, "cruiser": 0,
    })
    fleet_multipliers: dict = field(default_factory=lambda: {
        "small_cargo": 5.0,
        "large_cargo": 6.0,
        "recycler": 2.5,
        "light_fighter": 40.0,
        "heavy_fighter": 20.0,
        "cruiser": 10.0,
        "battleship": 5.0,
        "battlecruiser": 2.4,
    })

    # --- Expediciones ---
    enable_expeditions: bool = True
    expedition_position: int = 16
    expedition_ships: dict = field(default_factory=lambda: {"large_cargo": 1})
    # Auto-cálculo de naves: dimensiona la carga al botín máximo y reparte por slots
    expedition_auto_ships: bool = False
    expedition_cargo_ship: str = "large_cargo"   # tipo de carguero a dimensionar (NGC)
    expedition_use_pathfinder: bool = False      # incluir 1 Pathfinder (x2 botín) si hay
    expedition_send_probe: bool = False          # enviar también sonda(s) de espionaje con cada expedición
    expedition_probe_count: int = 1              # nº de sondas por expedición
    expedition_discoverer_class: bool = False    # clase Descubridor (x1.5 botín)
    expedition_destroyer_count: int = 0          # destructores por expedición (0 = ninguno; defienden contra combates)
    expedition_top1_points: int = 0              # override puntos Top-1 (0 = leer de la API)
    expedition_hyperspace_level: int = 0         # override nivel Hiperespacio para la bodega (0 = leer del juego)
    expedition_find_safety: float = 1.0          # escala el botín objetivo (1.0 = tope)
    expedition_min_cargo: int = 1                # mínimo de cargueros por expedición
    expedition_max_cargo: int = 0                # tope de cargueros por expedición (0 = sin tope)
    expedition_hold_hours: float = 1.0           # tiempo de permanencia en la posición 16
    # Rotación de sistemas (para no agotar siempre el mismo sistema)
    expedition_rotate_systems: bool = True
    expedition_system_range: int = 15            # +/- sistemas alrededor del tuyo
    # Reactivarse al volver/terminar las expediciones para reenviar nuevas
    expedition_smart_schedule: bool = True

    # --- Configuraciones especiales (programas de desarrollo fijos) ---
    # Inicio de servidor: se asume UN solo planeta (el principal) y se sigue sin
    # parar el orden óptimo de arranque de universo (ver startorder.SERVER_START_ORDER),
    # incluida la investigación, hasta completarlo; luego vuelve la economía normal.
    special_server_start: bool = False
    # Planeta nuevo: coordenadas "g:s:p" de un planeta que seguirá el orden óptimo
    # de colonia (solo edificios). Vacío = ninguno elegido a mano.
    special_new_planet: str = ""
    # Aplicar el orden de colonia automáticamente a los planetas que aún no lo hayan
    # completado (colonias recién fundadas, p.ej. con autocolonizar).
    special_new_planet_auto: bool = False

    # --- Memoria de estado (caché de niveles de edificios/investigación/defensas) ---
    # El bot escanea todo al inicio y luego solo lee recursos/cola/naves en vivo,
    # usando la caché para decidir "a tiro hecho" sin recorrer todas las ramas.
    enable_state_cache: bool = True
    state_resync_hours: float = 6.0   # cada cuánto re-escanea todo para corregir desfases

    # --- Reciclaje ---
    enable_recycling: bool = True
    recycling_system_range: int = 0
    recycling_min_debris: int = 8000

    # --- Lunas ---
    enable_moon_creation: bool = False
    moon_target_debris: int = 100_000        # escombros objetivo para 20% prob.
    moon_sacrifice_ship: str = "light_fighter"

    # --- Colonización ---
    enable_colonization: bool = True
    preferred_colony_positions: List[int] = field(default_factory=lambda: [4, 5, 6, 7, 8, 9, 10, 11, 12])
    max_colonies: int = 9

    # --- Seguridad / fleetsave / humanización ---
    # Modo solo-monitoreo: el bot NO hace economía/farming/expediciones/defensa/investigación.
    # Solo vigila ataques y espionaje entrantes y ejecuta la salvación de flota (fleetsave).
    monitor_only: bool = False
    enable_fleetsave: bool = True
    enable_attack_escape: bool = True       # Huir de ataques enemigos de forma automática
    # Intervalo entre comprobaciones de ataque: aleatorio en [min,max] segundos para
    # no dar un patrón fijo de sondeo que delate el bot (5-13 min por defecto).
    attack_check_interval_min_s: int = 300
    attack_check_interval_max_s: int = 780
    # Vigilancia de espionaje entrante (misión 6): avisa por Telegram cuando te sondean.
    enable_spy_watch: bool = True
    spy_watch_cooldown_mins: int = 30   # no re-avisar del mismo origen dentro de este tiempo
    # Además, rescatar sondeos vía los mensajes de contraespionaje ("se ha detectado una
    # flota...") que el polling de movimientos no pilló (fueron y volvieron entre chequeos).
    spy_watch_messages: bool = True
    fleetsave_mission: str = "deploy"        # deploy/transport/expedition
    fleetsave_carry_resources: bool = True   # llevarse los recursos del planeta en el fleetsave
    fleetsave_recall_halfway: bool = False   # retornar despliegues a mitad del descanso
    fleetsave_prefer_moon: bool = True       # preferir destino LUNA (no escaneable por phalanx)
    fleetsave_warn_phalanx: bool = True      # avisar por Telegram si el fleetsave queda expuesto a phalanx
    # Slots de flota reservados para emergencias (evasión/fleetsave). El farmeo,
    # las expediciones y la alimentación nunca consumen estos slots.
    keep_free_fleet_slots: int = 1
    # Comandos por Telegram (/status, /fleetsave, /recall, /pausa...). Solo atiende
    # al chat_id configurado en telegram_chat_id.
    enable_telegram_commands: bool = True
    # Canario de selectores: al arrancar verifica los selectores clave del DOM y
    # avisa por Telegram si GameForge cambió la interfaz.
    enable_selector_canary: bool = True
    # Perfil de riesgo aplicado desde la GUI (informativo): paranoid/normal/aggressive
    risk_profile: str = "normal"
    # Orden en que cycle() ejecuta las rondas (reordenable desde la GUI por drag&drop)
    cycle_order: List[str] = field(default_factory=lambda: [
        "economy", "recycling", "expeditions", "farming", "feed"])
    # Fleetsave nocturno solo si hubo actividad hostil (ataque o sondeo) reciente
    fleetsave_only_if_hostile: bool = False
    # Barrido nocturno: cada N horas durante el descanso, vacía (fleetsave) los planetas
    # activados para recoger la flota fabricada de noche. Activable por planeta.
    enable_night_sweep: bool = False
    night_sweep_interval_hours: float = 2.0
    min_action_delay_s: float = 3.0          # delays aleatorios entre acciones
    max_action_delay_s: float = 11.0
    cycle_interval_min_s: float = 600         # cada cuánto corre el ciclo principal
    cycle_interval_max_s: float = 1500
    active_hours: Tuple[int, int] = (8, 24)  # horas (local) en que el bot "juega"
    max_actions_per_hour: int = 40           # rate-limit para parecer humano
    headless: bool = True
    cdp_port: int = 9222                      # puerto de depuración del navegador (único por cuenta)
    login_human_check_timeout_s: int = 300    # espera máx. para resolver el CAPTCHA desde el visor del GUI
    max_saving_hours_research: float = 6.0   # horas máx a ahorrar para investigación
    max_saving_hours_economy: float = 4.0    # horas máx a ahorrar para economía
    # Deuterio mínimo a conservar en cada planeta como combustible de emergencia
    # (fleetsave nocturno / evasión de ataques). Farmeo y expediciones no despegan
    # si dejarían el depósito por debajo. Las misiones de emergencia lo ignoran. 0 = sin reserva.
    deuterium_reserve: int = 0

    # --- Alimentación de recursos entre planetas (transporte para construir) ---
    # Se activa marcando planetas como "Recibe recursos" (destino) y "Cede recursos"
    # (fuente) en la pestaña "Por Planeta". El destino usa sus objetivos de
    # instalaciones/economía (p.ej. lab a 12) y las fuentes le mandan su excedente.
    feed_min_send: int = 5000                # no mandar transportes de alimentación menores que esto
    feed_round_up: int = 1000                # redondea el déficit hacia arriba a este múltiplo + 1 (51k->52k, 20k->21k). 0 = desactivado

    # --- Estilo de Juego (ofensivo / defensivo) ---
    server_playstyle: str = "defensive"      # "offensive" (prioriza flotas) o "defensive" (prioriza defensas)

    # --- Defensa ---
    enable_defense: bool = True
    defense_batch_size: int = 25          # unidades a construir por ciclo

    # --- Formas de vida (Lifeforms) ---
    enable_lifeforms: bool = True

    # --- Configuración por planeta ---
    planets_config: dict = field(default_factory=dict)

    # --- Objetivos de flota ---
    fleet_targets: dict = field(default_factory=dict)

    # --- Persistencia / logs ---
    state_file: str = "state.json"
    log_file: str = "ogbot.log"
    log_level: str = "INFO"
    dry_run: bool = True                     # True = NO ejecuta acciones reales

    # --- Notificaciones de Telegram ---
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # --- Frecuencias de rondas independientes ---
    economy_run_interval_mins: int = 0
    farming_run_interval_mins: int = 0
    expeditions_run_interval_mins: int = 0
    recycling_run_interval_mins: int = 0


    @staticmethod
    def load(path: str = "config.yaml") -> "Config":
        cfg = Config()
        cfg._path = path
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for k, v in data.items():
                if hasattr(cfg, k):
                    if k in ("trade_ratio", "active_hours", "night_hours") and isinstance(v, list):
                        v = tuple(v)
                    setattr(cfg, k, v)
        # La contraseña SIEMPRE preferimos leerla del entorno
        cfg.password = os.environ.get("OGBOT_PASS", cfg.password)
        cfg.username = os.environ.get("OGBOT_USER", cfg.username)
        # Puerto CDP por cuenta (permite varios navegadores a la vez)
        cdp_env = os.environ.get("OGBOT_CDP_PORT")
        if cdp_env:
            try:
                cfg.cdp_port = int(cdp_env)
            except ValueError:
                pass
        return cfg
