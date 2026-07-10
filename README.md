# OGBot — Gestor autónomo para OGame

> Bot modular en Python (Playwright) que automatiza **economía, investigación,
> flota, farmeo, reciclaje, expediciones, defensa, colonización y fleetsave**
> siguiendo una estrategia configurable. Incluye un **panel web** de control
> (multicuenta), notificaciones y comandos por **Telegram**, estadísticas
> persistentes y soporte **Docker**.

---

## ⚠️ Aviso importante (léelo antes de nada)

**Gameforge prohíbe los bots en OGame.** Las cuentas detectadas se **banean de
forma permanente**; existe detección por captcha en el Lobby y análisis de
comportamiento. Este proyecto es **educativo** (teoría de juegos, optimización,
simulación de combate, automatización). Si lo usas en un servidor real:

- Asumes el riesgo de **perder tu cuenta**.
- La "humanización" (delays aleatorios, franja horaria, límite de acciones/hora,
  reutilización de sesión) **reduce pero NO elimina** el riesgo de detección.
- Empieza siempre con `dry_run: true` para ver el plan **sin ejecutar nada**.

---

## Índice

1. [Funciones](#1-funciones)
2. [Instalación y arranque rápido](#2-instalación-y-arranque-rápido)
3. [El panel web (GUI)](#3-el-panel-web-gui)
4. [Uso por línea de comandos](#4-uso-por-línea-de-comandos)
5. [Configuración completa (config.yaml)](#5-configuración-completa-configyaml)
6. [Configuración por planeta](#6-configuración-por-planeta)
7. [Telegram: avisos y comandos](#7-telegram-avisos-y-comandos)
8. [Docker / multicuenta](#8-docker--multicuenta)
9. [Captcha y sesión](#9-captcha-y-sesión)
10. [Ficheros que genera el bot](#10-ficheros-que-genera-el-bot)
11. [Arquitectura](#11-arquitectura)
12. [Estrategia que implementa](#12-estrategia-que-implementa)
13. [Seguridad operativa](#13-seguridad-operativa)

---

## 1. Funciones

### Economía y desarrollo
- **Optimizador de minas por payback marginal**: sube siempre la mina que antes
  se amortiza (`coste / producción_extra_por_hora`, en metal-equivalente según
  `trade_ratio`). Si ninguna baja del umbral, ahorra para investigación/flota.
- **Gestión de energía**: planta solar y reactor de fusión cuando compensa.
- **Instalaciones** (robótica, hangar, laboratorio, nanobots) con niveles
  objetivo configurables por planeta.
- **Investigación** por prioridad + pesos + prerrequisitos, con topes de nivel
  (`research_caps`) para tecnologías de utilidad limitada.
- **Cola de construcción manual** por planeta (tipo Comandante) desde la GUI.
- **Defensa**: construcción por lotes según el estilo de juego.
- **Formas de vida (Lifeforms)**: soporte de edificios/investigación lifeform.
- **Programas especiales**: orden óptimo de *inicio de servidor* (un planeta,
  desde cero hasta Astrofísica 1) y orden óptimo de *colonia nueva*
  (automático para colonias recién fundadas si lo activas).
- **Alimentación entre planetas**: planetas "fuente" transportan su excedente a
  planetas "destino" para acelerar sus construcciones.

### Farmeo de inactivos
- Busca inactivos vía la **API XML pública**, filtra por distancia, **espía** y
  calcula `score = botín − combustible − pérdidas esperadas` con un
  **simulador de combate Monte Carlo** (rapidfire, escudos, 6 rondas, escombros).
- **Auto-flota** opcional: elige por simulación la escolta mínima que gana
  ≥95% y dimensiona los cargueros al botín.
- **Reciclaje de escombros del ataque**: sonda suicida para crear el campo y
  recicladores despachados de inmediato.
- **Raid con sondas** en servidores donde las sondas tienen bodega.
- Protecciones: solo inactivos, evitar jugadores fuertes, descartar objetivos
  con actividad reciente (trampas), **blacklist de granjas pobres**, cooldown
  por objetivo y botín mínimo.
- **Smart schedule**: despierta al volver la flota para relanzar la ronda.

### Expediciones
- Envío a la posición 16 con rotación de sistemas.
- **Auto-cálculo de cargueros** según el tope de botín de tu universo (puntos
  del Top-1 leídos de la API), reparto entre todos los slots de expedición,
  Pathfinder / Descubridor / destructores / sondas opcionales.
- Estimación de vuelo con el **tiempo real de la página** y reenvío automático
  al volver cada expedición (smart schedule).

### Reciclaje
- Escaneo de campos de escombros en la galaxia y recogida de los que superen
  el mínimo configurado.

### Seguridad (lo que te mantiene vivo)
- **Fleetsave nocturno**: antes del descanso envía flota+recursos en despliegue
  lento, preferentemente a **luna** (no escaneable por phalanx), calculado para
  volver cuando vuelvas a estar activo. Aviso si queda expuesto a phalanx.
- **Evasión de ataques**: vigila ataques entrantes (intervalo aleatorio 5–13
  min) y pone la flota a salvo automáticamente.
- **Vigilancia de espionaje**: aviso por Telegram si te sondean (por movimiento
  o por mensajes de contraespionaje).
- **Barrido nocturno** opcional: cada N horas de descanso vacía los planetas
  marcados para recoger la flota fabricada de noche.
- **Retorno de flotas** (`/recall`), slots reservados para emergencias,
  **reserva de deuterio** de emergencia por planeta.
- **Canario de selectores**: al arrancar verifica el DOM del juego y avisa si
  GameForge cambió la interfaz.
- **Modo solo-monitoreo** (`monitor_only`): no juega, solo vigila y salva flota.

### Colonización y lunas
- Colonización automática de las mejores posiciones libres (4–12 por defecto)
  cuando Astrofísica da slot.
- Módulo de lunas: calcula escombros/naves necesarios (la ejecución del crash
  queda a tu criterio, el juego no permite auto-atacarse).

### Panel, estadísticas y control
- **Panel web** completo (ver §3) con visor del navegador en vivo y control
  remoto, multicuenta.
- **Estadísticas persistentes**: histórico de puntos/recursos/flota
  (`stats_history.jsonl`, `stats_hourly.jsonl`), botín por granja, beneficio de
  expediciones, resumen de sesión.
- **Agenda de tareas** (`task_agenda.json`): qué va a hacer y cuándo.
- **Memoria de estado**: escaneo completo inicial y re-sincronización periódica;
  entre medias decide con caché (menos navegación = menos riesgo).
- **Humanización**: delays aleatorios, franja horaria activa, límite de
  acciones/hora, intervalos de ciclo aleatorios, orden de rondas configurable.
- **Dry-run**: registra lo que haría sin ejecutar nada.

---

## 2. Instalación y arranque rápido

> 🐳 **¿Prefieres Docker?** Salta directamente a la [sección 8](#8-docker--multicuenta):
> `docker compose up -d --build` y todo lo demás se hace desde el panel web.

### Requisitos
- Python 3.10+
- Google Chrome/Chromium (lo instala Playwright)

### Instalación local

```bash
cd ogame-bot
python -m venv .venv
# Linux/Mac: source .venv/bin/activate      Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### Credenciales (nunca en el YAML)

```bash
# Linux/Mac
export OGBOT_USER="tu_email@dominio.com"
export OGBOT_PASS="tu_password"
# Windows PowerShell
$env:OGBOT_USER = "tu_email@dominio.com"
$env:OGBOT_PASS = "tu_password"
```

### Primera ejecución (recomendado: todo desde la GUI)

**No necesitas rellenar `config.yaml` a mano.** Arranca el panel web y
configúralo todo desde ahí — la GUI crea y guarda el `config.yaml` por ti:

```bash
python gui.py            # abre http://localhost:5000
```

1. Pestaña **Configuración** → pon usuario, contraseña, servidor y los
   parámetros del universo (velocidades, escombros…).
2. **GUARDAR** → la GUI escribe `config.yaml` automáticamente.
3. Deja `dry_run` activado y pulsa **► INICIAR** para ver el plan sin riesgo.
4. Cuando las decisiones te cuadren, desactiva `dry_run` desde la misma GUI.

Si ejecutas `python main.py` sin que exista `config.yaml`, se lanza el panel
automáticamente para que configures ahí.

<details>
<summary>Alternativa manual (sin GUI, editando el YAML)</summary>

```bash
cp config.example.yaml config.yaml
# edita config.yaml: server_url, universe_speed, fleet_speed, debris_factor
# (los valores reales están en https://sXXX-es.ogame.gameforge.com/api/serverData.xml)
python main.py --once    # UN ciclo en dry-run para ver el plan
tail -f ogbot.log
```
</details>

Verás líneas tipo `[DRY-RUN] Construir solar_plant en [1:100:8]`. Cuando las
decisiones te cuadren, pon `dry_run: false` y arranca en continuo:

```bash
python main.py --config config.yaml
```

---

## 3. El panel web (GUI)

Arranque: `python gui.py` → <http://localhost:5000> (puerto configurable con la
variable de entorno `PORT`). Desde el panel puedes **iniciar / pausar /
reanudar / detener** el bot y editar toda la configuración sin tocar YAML.

| Pestaña | Qué hay |
|---|---|
| **Dashboard** | Estado del bot, recursos y puntos, gráficas 24h/7d/30d, próxima acción, resumen de actividad. |
| **Bot en directo** | La pantalla real del navegador del bot (vía CDP). Puedes **tomar el control**: clicar, escribir, Enter/Tab/Esc… Útil para resolver captchas o mirar algo a mano. También captura PNG, reiniciar sesión y cerrar navegador. |
| **Planetas** | Estado por planeta (edificios, niveles, colas), configuración **por planeta** (§6), corrección manual de niveles y re-escaneo forzado. |
| **Cola de tareas** | La agenda del bot: qué tarea toca, cuándo y por qué; cola de construcción manual. |
| **Configuración** | Toda la config editable por secciones, perfiles de riesgo (🛡 Paranoico / ⚖ Normal / 🔥 Agresivo), orden de rondas por drag&drop, prueba de Telegram, gestión de **cuentas** (multicuenta). |
| **Registro** | Log en vivo con filtros por nivel y exportación CSV. |
| **¿Ataco?** | Simulador de combate manual: pega un informe y te dice si compensa atacar y con qué. |
| **Estadísticas** | Histórico de puntos/recursos/flota, botín por granja, rentabilidad de expediciones. |
| **Vuelos** | Movimientos de flota en curso, con retorno manual. |
| **Mensajes** | Bandeja de mensajes del juego (espionaje, combate…). |
| **Resumen de sesión** | Qué ha hecho el bot en la sesión actual: construcciones, ataques, botín, gastos. |
| **Localizador** | Buscador de posiciones de colonia / objetivos. |

---

## 4. Uso por línea de comandos

```bash
python main.py                     # bucle infinito con config.yaml
python main.py --config otra.yaml  # otra configuración
python main.py --once              # un único ciclo y sale (ideal con dry_run)
python gui.py                      # solo el panel web
```

- Hay un **lock por PID** (`bot.pid`): no puede haber dos instancias a la vez
  sobre la misma cuenta.
- En segundo plano (Linux/Mac): `nohup python main.py &`, o como servicio:

```ini
# /etc/systemd/system/ogbot.service
[Unit]
Description=OGBot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/ruta/a/ogame-bot
Environment=OGBOT_USER=tu_email@dominio.com
Environment=OGBOT_PASS=tu_password
ExecStart=/ruta/a/ogame-bot/.venv/bin/python main.py --config config.yaml
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now ogbot
journalctl -u ogbot -f
```

### Variables de entorno

| Variable | Uso |
|---|---|
| `OGBOT_USER` / `OGBOT_PASS` | Credenciales del Lobby (siempre tienen prioridad sobre el YAML). |
| `OGBOT_CDP_PORT` | Puerto de depuración del navegador (uno distinto por cuenta). |
| `PORT` | Puerto del panel web (por defecto 5000). |
| `OGBOT_CHROMIUM_NO_SANDBOX` | `1` para `--no-sandbox` (necesario dentro de Docker). |

---

## 5. Configuración completa (config.yaml)

> **No hace falta editar este fichero a mano**: la pestaña **Configuración**
> de la GUI lo crea y lo guarda por ti. Esta sección es la **referencia** de
> cada clave, por si prefieres el YAML o quieres saber qué hace cada opción.
> Si vas a mano, copia `config.example.yaml` a `config.yaml`. Valores = por
> defecto.

### Servidor / cuenta

| Clave | Defecto | Qué hace |
|---|---|---|
| `universe` | `""` | Nombre del universo. |
| `country` | `es` | Comunidad/idioma. |
| `server_url` | `""` | URL del servidor, ej. `https://s272-es.ogame.gameforge.com/`. |
| `proxy_server` / `proxy_username` / `proxy_password` | `""` | Proxy del navegador (útil en VPS si GameForge bloquea la IP). |

### Parámetros del universo (míralos en `/api/serverData.xml`)

| Clave | Defecto | Qué hace |
|---|---|---|
| `universe_speed` | `1.0` | Velocidad de economía. |
| `fleet_speed` | `1.0` | Velocidad de flotas. |
| `debris_factor` | `0.30` | % de naves destruidas que va a escombros. |
| `debris_includes_deut` | `false` | Si el deuterio también genera escombros. |
| `loot_percent` | `0.50` | % de recursos saqueables. |
| `trade_ratio` | `[2.5, 1.5, 1.0]` | Ratio metal:cristal:deuterio para valorar recursos. |

### Economía

| Clave | Defecto | Qué hace |
|---|---|---|
| `enable_economy` | `true` | Ronda de economía (minas/energía/almacenes). |
| `enable_build_queue` | `true` | Cola de construcción manual por planeta. |
| `target_mine_ratio_payback_hours` | `16` | Sube una mina solo si se amortiza en ≤ X horas. |
| `keep_resources_buffer` | `0.10` | % de recursos que nunca gasta. |
| `enable_fusion_reactor` | `true` | Permite reactor de fusión. |
| `fusion_reactor_solar_offset` | `25` | Niveles que la solar debe superar a la fusión antes de subir fusión. |
| `max_mine_level` | `40` | Tope voluntario de minas. |
| `target_metal_mine` / `target_crystal_mine` / `target_deut_synth` | `99` | Niveles objetivo por tipo de mina. |
| `storage_fill_trigger_percent` | `0.90` | % de llenado que dispara ampliar almacén. |
| `enable_facilities` | `true` | Construcción de instalaciones. |
| `target_robotics_factory` / `target_shipyard` / `target_research_lab` / `target_nanite_factory` | `0` | Niveles objetivo de instalaciones (0 = automático). |
| `max_saving_hours_economy` | `4.0` | Horas máximas ahorrando para un edificio. |

### Investigación

| Clave | Defecto | Qué hace |
|---|---|---|
| `enable_research` | `true` | Ronda de investigación. |
| `research_priority` | ver ejemplo | Orden de evaluación de tecnologías. |
| `research_weights` | ver ejemplo | Peso por tecnología (mayor = antes). |
| `research_caps` | láser 12, ion 5, energía 8, hiperespacio 15 | Nivel máximo voluntario por tecnología. |
| `max_saving_hours_research` | `6.0` | Horas máximas ahorrando para una tecnología. |

### Farmeo

| Clave | Defecto | Qué hace |
|---|---|---|
| `enable_farming` | `true` | Ronda de farmeo. |
| `only_inactive_targets` | `true` | Solo jugadores inactivos (i/I). **Recomendado.** |
| `avoid_strong_players` | `true` | Evita objetivos con mucha flota/defensa. |
| `max_attack_targets_per_cycle` | `8` | Ataques máximos por ronda. |
| `min_loot_value` | `50000` | Botín mínimo (metal-equivalente) para atacar. |
| `max_target_distance_systems` | `200` | Distancia máxima en sistemas. |
| `farming_attack_cooldown_hours` | `2.0` | Espera antes de repetir objetivo. |
| `attacker_fleet_template` | cargueros | Flota fija por ataque (si no hay auto-flota). |
| `farm_auto_fleet` | `false` | Escolta elegida por simulación (gana ≥95%) + cargueros al botín. |
| `farm_recycle_debris` | `true` | Sonda suicida + recicladores para los escombros del combate. |
| `farm_with_probes` | `false` | Raid con sondas (solo servidores con bodega en sondas). |
| `espionage_probe_cargo` | `0` | Bodega real por sonda (0 = tabla estándar). |
| `farming_smart_schedule` | `true` | Relanza la ronda al volver la flota. |
| `farming_skip_active_targets` | `true` | Descarta "inactivos" con actividad <60 min (trampas). |
| `farming_blacklist_days` | `7` | Días de veto a granjas pobres (3+ raids con botín medio bajo). 0 = off. |
| `deuterium_reserve` | `0` | Deuterio intocable por planeta (combustible de emergencia). |
| `enable_fleet_building` | `true` | Fabricar flota militar. |
| `fleet_auto_build` | `false` | Auto-gestión de flota: el bot calcula los objetivos (sondas, cargueros, recicladores y escolta) según el tamaño de tu economía y los sube solo al crecer el imperio. Ignora `fleet_targets`. |
| `fleet_priority` | `economy` | Prioridad de la flota automática: `economy` (cargueros y crecimiento), `military` (escolta x2) o `expeditions` (flota para llenar todos los slots de expedición al óptimo + cargueros extra para mover recursos). |
| `empire_auto` | `false` | **Autogestión del imperio**: un solo interruptor que enciende economía, instalaciones, investigación, flota auto, expediciones auto, colonización y reparto de recursos a colonias nuevas. El bot decide qué subir, dónde y desde dónde alimentar. No activa el farmeo. |
| `enable_cargo_building` | `false` | Fabricar cargueros extra. |
| `fleet_multipliers` | ver ejemplo | Objetivos de flota escalados por nivel de mina (modo ofensivo). |

### Expediciones

| Clave | Defecto | Qué hace |
|---|---|---|
| `enable_expeditions` | `true` | Ronda de expediciones. |
| `expedition_position` | `16` | Posición destino. |
| `expedition_ships` | `{large_cargo: 1}` | Flota fija por expedición (si no hay auto). |
| `expedition_auto_ships` | `false` | Dimensiona cargueros al botín máximo del universo y reparte por slots. |
| `expedition_cargo_ship` | `large_cargo` | Tipo de carguero a dimensionar. |
| `expedition_use_pathfinder` | `false` | +1 Pathfinder por expedición (x2 botín). |
| `expedition_send_probe` / `expedition_probe_count` | `false` / `1` | Sondas extra por expedición. |
| `expedition_destroyer_count` | `0` | Destructores de escolta. |
| `expedition_discoverer_class` | `false` | Marca si tienes clase Descubridor (x1.5). |
| `expedition_top1_points` | `0` | Puntos del Top-1 (0 = leer de la API). |
| `expedition_hyperspace_level` | `0` | Override de Hiperespacio para bodega (0 = leer del juego). |
| `expedition_find_safety` | `1.0` | Escala del botín objetivo. |
| `expedition_min_cargo` / `expedition_max_cargo` | `1` / `0` | Mín/máx cargueros (0 = sin tope). |
| `expedition_hold_hours` | `1.0` | Permanencia en la posición 16. |
| `expedition_rotate_systems` / `expedition_system_range` | `true` / `15` | Rotar sistemas ±N. |
| `expedition_smart_schedule` | `true` | Reenvía al volver cada expedición. |

### Reciclaje

| Clave | Defecto | Qué hace |
|---|---|---|
| `enable_recycling` | `true` | Ronda de reciclaje. |
| `recycling_min_debris` | `8000` | Escombros mínimos para ir a por ellos. |
| `recycling_system_range` | `0` | Rango de sistemas a escanear. |

### Seguridad / fleetsave / humanización

| Clave | Defecto | Qué hace |
|---|---|---|
| `monitor_only` | `false` | Solo vigilar ataques y salvar flota; no juega. |
| `enable_fleetsave` | `true` | Fleetsave antes del descanso. |
| `fleetsave_mission` | `deploy` | Misión del fleetsave (`deploy`/`transport`/`expedition`). |
| `fleetsave_prefer_moon` | `true` | Preferir destino luna (invisible a phalanx). |
| `fleetsave_carry_resources` | `true` | Llevarse también los recursos. |
| `fleetsave_recall_halfway` | `false` | Retornar el despliegue a mitad del descanso. |
| `fleetsave_warn_phalanx` | `true` | Aviso Telegram si el fleetsave es phalanxeable. |
| `fleetsave_only_if_hostile` | `false` | Solo hacer fleetsave si hubo hostilidad reciente (12 h). |
| `enable_attack_escape` | `true` | Evasión automática de ataques entrantes. |
| `enable_attack_check` | `true` | Fase de comprobación de ataques. `false` = saltarla (early game, acelera el ciclo). |
| `attack_check_interval_min_s` / `max_s` | `300` / `780` | Comprobar ataques cada 5–13 min (aleatorio). |
| `enable_spy_watch` | `true` | Aviso si te espían (movimiento entrante misión 6). |
| `spy_watch_messages` | `true` | También vía mensajes de contraespionaje. |
| `spy_watch_cooldown_mins` | `30` | No repetir aviso del mismo origen. |
| `enable_night_sweep` / `night_sweep_interval_hours` | `false` / `2.0` | Barrido nocturno de los planetas marcados. |
| `keep_free_fleet_slots` | `1` | Slots de flota reservados para emergencias. |
| `enable_selector_canary` | `true` | Verificación de selectores del DOM al arrancar. |
| `risk_profile` | `normal` | Perfil aplicado desde la GUI (`paranoid`/`normal`/`aggressive`). |
| `cycle_order` | economy, recycling, expeditions, farming, feed | Orden de las rondas (drag&drop en la GUI). |
| `min_action_delay_s` / `max_action_delay_s` | `3` / `11` | Delay aleatorio entre acciones. |
| `cycle_interval_min_s` / `max_s` | `600` / `1500` | Pausa aleatoria entre ciclos. |
| `active_hours` | `[8, 24]` | Franja horaria local en la que "juega". |
| `max_actions_per_hour` | `40` | Rate-limit de acciones. |
| `headless` | `true` | Navegador sin ventana. |
| `cdp_port` | `9222` | Puerto de depuración (visor en vivo; único por cuenta). |
| `login_human_check_timeout_s` | `300` | Espera máxima del captcha para resolverlo desde el visor. |

### Otros

| Clave | Defecto | Qué hace |
|---|---|---|
| `server_playstyle` | `defensive` | `offensive` (prioriza flota) o `defensive` (prioriza defensa). |
| `enable_defense` / `defense_batch_size` | `true` / `25` | Construcción de defensas por lotes. |
| `enable_lifeforms` | `true` | Edificios/investigación de formas de vida. |
| `enable_colonization` | `true` | Colonizar cuando Astrofísica da slot. |
| `preferred_colony_positions` | `[4..12]` | Posiciones preferidas. |
| `max_colonies` | `9` | Tope de colonias. |
| `enable_moon_creation` / `moon_target_debris` / `moon_sacrifice_ship` | `false` / `100000` / `light_fighter` | Módulo de lunas (cálculo). |
| `special_server_start` | `false` | Programa de inicio de servidor (1 planeta, orden óptimo). |
| `special_new_planet` | `""` | Coordenadas `g:s:p` de un planeta con orden de colonia. |
| `special_new_planet_auto` | `false` | Aplicar orden de colonia a colonias nuevas automáticamente. |
| `feed_min_send` / `feed_round_up` | `5000` / `1000` | Alimentación entre planetas: envío mínimo y redondeo. |
| `enable_state_cache` / `state_resync_hours` | `true` / `6.0` | Memoria de estado y re-escaneo completo periódico. |
| `economy_run_interval_mins` etc. | `0` | Frecuencia independiente por ronda (`economy`, `farming`, `expeditions`, `recycling`); 0 = en cada ciclo. |
| `state_file` / `log_file` / `log_level` | `state.json` / `ogbot.log` / `INFO` | Persistencia y logs. |
| `dry_run` | `true` | **No ejecuta acciones reales.** Déjalo así hasta validar. |
| `telegram_token` / `telegram_chat_id` | `""` | Credenciales del bot de Telegram (§7). |

---

## 6. Configuración por planeta

En la GUI, pestaña **Planetas**, cada planeta puede sobrescribir el
comportamiento global (se guarda en `planets_config` dentro del YAML):

- Activar/desactivar por planeta: economía, farmeo, expediciones, reciclaje,
  construcción de flota/defensa.
- **Cede recursos / Recibe recursos**: define fuentes y destinos de la
  alimentación entre planetas (ronda `feed`).
- **Barrido nocturno**: qué planetas se vacían durante el descanso.
- Niveles objetivo propios (minas/instalaciones) y cola de construcción manual.
- Corrección manual de niveles y re-escaneo forzado si la caché se desvía.

---

## 7. Telegram: avisos y comandos

1. Crea un bot con **@BotFather** → copia el token en `telegram_token`.
2. Averigua tu chat ID con **@userinfobot** → `telegram_chat_id`.
3. Prueba desde la GUI (botón "📨 Probar Telegram").

**Avisos**: ataques entrantes, sondeos de espionaje, fleetsave expuesto a
phalanx, captcha pendiente, cambios de interfaz (canario de selectores),
errores de login…

**Comandos** (solo atiende a tu `chat_id`; `enable_telegram_commands: true`):

| Comando | Efecto |
|---|---|
| `/status` | Estado del bot, recursos, flotas en vuelo. |
| `/fleetsave` | Fleetsave inmediato. |
| `/recall` | Retornar flotas en vuelo. |
| `/pausa` | Pausar el bot. |
| `/reanudar` | Reanudarlo. |
| `/ayuda` | Lista de comandos. |

---

## 8. Docker / multicuenta

Es la forma más cómoda de tenerlo corriendo 24/7 (VPS, NAS, mini-PC): el
contenedor levanta el **panel web multicuenta** en `http://localhost:5000` y
cada cuenta corre su propio bot (Chromium headless) dentro del contenedor.
**Todo se configura desde la GUI** — no tienes que crear ni editar ningún
`config.yaml` ni pasar variables de entorno.

### Requisitos
- Docker (y opcionalmente Docker Compose; en Windows/Mac, Docker Desktop).

### Paso a paso

```bash
git clone https://github.com/trilogi77/OgameBot.git
cd OgameBot
docker compose up -d --build
```

O sin compose:

```bash
docker build -t ogbot .
docker run -d --name ogbot -p 5000:5000 -v "$(pwd)/accounts:/app/accounts" ogbot
```

1. Abre <http://localhost:5000>.
2. Pestaña **Configuración → Cuentas** → **+ Crear cuenta** (una por cada
   cuenta de OGame que quieras manejar).
3. Con la cuenta seleccionada, rellena en **Configuración** el usuario,
   contraseña, servidor y parámetros del universo → **GUARDAR**.
4. Pulsa **► INICIAR**. Con `dry_run` activado verás el plan en la pestaña
   **Registro** sin que ejecute nada; desactívalo cuando quieras que actúe.

### Persistencia

Toda la información de cada cuenta (configuración, estado, sesión del
navegador, estadísticas) vive en `./accounts/`, montado como volumen: 
**sobrevive a reinicios y reconstrucciones** del contenedor.

```bash
docker compose restart          # reiniciar
docker compose up -d --build    # actualizar tras un git pull
docker logs -f ogbot            # logs del contenedor
```

### Captcha en headless

Dentro de Docker el navegador no tiene ventana, así que GameForge pide el
"soy humano" más a menudo. No necesitas pantalla ni VNC:

1. Cuando el bot detecta el captcha, **espera** (hasta
   `login_human_check_timeout_s`, 5 min por defecto) y avisa por log/Telegram.
2. Abre el panel → pestaña **Bot en directo** (con esa cuenta seleccionada).
3. Verás la pantalla real del navegador del bot (vía CDP interno): haz clic /
   escribe para resolver el captcha.
4. El bot detecta que se resolvió y continúa el login solo.

### Notas

- Para **menos captchas** puedes correr el navegador "headful" con display
  virtual: cambia el `CMD` del Dockerfile a `xvfb-run python gui.py` y pon
  `headless: false` en la cuenta.
- Cada cuenta usa un puerto CDP interno distinto (9222, 9223…); no hay que
  exponerlos.
- La imagen ya lleva `OGBOT_CHROMIUM_NO_SANDBOX=1` (`--no-sandbox` +
  `--disable-dev-shm-usage`), necesario para Chromium dentro de Docker.
- Más detalles en [DOCKER.md](DOCKER.md).

---

## 9. Captcha y sesión

- El bot **reutiliza la sesión** (`ogame_session.json`): cuantos menos logins,
  menos captchas.
- Si aparece el "soy humano", el bot **espera** (hasta
  `login_human_check_timeout_s`) y te avisa por log/Telegram. Resuélvelo desde
  la pestaña **Bot en directo** del panel (funciona incluso en headless, vía
  CDP) o ejecuta una vez con `headless: false` y hazlo a mano.

---

## 10. Ficheros que genera el bot

| Fichero | Contenido |
|---|---|
| `config.yaml` | Tu configuración (editada por la GUI). |
| `state.json` | Estado persistente del bot (timers, cooldowns…). |
| `ogame_session.json` | Cookies/sesión del navegador. |
| `ogbot.log` | Log principal. |
| `ogbot_stats.json` | Estadísticas acumuladas (botín, granjas, expediciones). |
| `stats_history.jsonl` / `stats_hourly.jsonl` | Histórico de puntos/recursos/flota para las gráficas. |
| `task_agenda.json` | Agenda de próximas tareas (pestaña Cola de tareas). |
| `auto_plan.json` | Plan de decisiones del modo automático: qué subirá en cada planeta, investigaciones y flota (pestaña Automático). |
| `game_state_cache.json` / `planets_cache.json` | Caché de niveles y planetas. |
| `bot.pid` | Lock de instancia única. |
| `accounts/` | (Docker/multicuenta) config+estado+sesión por cuenta. |

---

## 11. Arquitectura

El bot separa la **estrategia** (lógica pura, testeable) de la **interacción
con el juego** (frágil, cambia con cada versión de OGame). Si Gameforge cambia
el HTML, solo se toca `client.py`.

```
                 ┌──────────────────────────────────────────────┐
                 │                  brain.py                      │
                 │  Orquestador: ciclo, prioridades, agenda,      │
                 │  fleetsave, Telegram, humanización             │
                 └───────────────┬───────────────┬────────────────┘
                                 │               │
          ┌──────────────────────┘               └───────────────────────┐
          ▼  LÓGICA PURA (sin red, testeable)                 INTERACCIÓN ▼
 ┌───────────────────────────────────────────┐     ┌──────────────────────────┐
 │ economy.py   optimizador de minas (payback)│     │ client.py  (Playwright)  │
 │ research.py  prioridad de investigación    │     │  login, scraping, clicks │
 │ targets.py   rentabilidad/riesgo objetivos │     └────────────┬─────────────┘
 │ combat.py    simulador Monte Carlo         │                  │ solo lectura
 │ fleet.py     fleetsave/expedición/reciclaje│     ┌────────────▼─────────────┐
 │ moons.py     lunas + colonización          │     │ universe_api.py          │
 │ startorder.py órdenes óptimos de arranque  │     │  API XML pública (oficial)│
 │ gamedata.py  fórmulas/costes/stats         │     │  inteligencia del universo│
 │ stats.py     estadísticas persistentes     │     └──────────────────────────┘
 │ models.py    estructuras de datos          │
 └───────────────────────────────────────────┘
          gui.py + gui_web/  →  panel de control (multicuenta, visor CDP)
```

```
ogame-bot/
├── main.py                 # entrada CLI
├── gui.py                  # servidor del panel web (puerto 5000)
├── gui_web/                # frontend del panel
├── config.example.yaml     # copia a config.yaml
├── Dockerfile / docker-compose.yml / DOCKER.md
└── ogbot/
    ├── gamedata.py         # fórmulas y stats del juego
    ├── models.py           # Resources, Planet, Coords, Target…
    ├── config.py           # carga de config.yaml + env vars
    ├── utils.py            # logging, delays, rate-limit
    ├── universe_api.py     # API XML pública (inteligencia)
    ├── economy.py          # optimizador de minas + instalaciones
    ├── research.py         # prioridad de investigación
    ├── combat.py           # simulador Monte Carlo
    ├── targets.py          # búsqueda/valoración de objetivos
    ├── fleet.py            # fleetsave / expediciones / reciclaje
    ├── moons.py            # lunas + colonización
    ├── startorder.py       # órdenes óptimos (server start / colonia)
    ├── prereqs.py          # prerrequisitos de edificios/techs
    ├── stats.py            # estadísticas persistentes
    ├── client.py           # ÚNICA capa que toca el juego (Playwright)
    └── brain.py            # orquestador del ciclo
```

---

## 12. Estrategia que implementa

OGame se gana con **economía compuesta + expansión + farmeo eficiente + no
perder flota nunca**:

1. **Economía por payback marginal**: en vez de ratios fijos, sube siempre la
   mina que antes se amortiza; si ninguna baja del umbral, invierte en
   investigación/flota. Plasma es el mayor multiplicador a largo plazo.
2. **Investigación como columna vertebral**: Energía → Computación (slots) →
   Espionaje → Propulsión → **Astrofísica** (colonias + expediciones) →
   **Plasma** (+1%/+0,66%/+0,33% producción por nivel).
3. **Farmeo de inactivos** como fuente principal de ingresos, con simulación
   de combate para no atacar nunca a pérdida.
4. **Expediciones** para recursos/naves gratis con la flota dimensionada al
   tope de botín del universo.
5. **Fleetsave religioso**: la flota nunca duerme en casa. Preferencia por
   lunas (invisibles a phalanx) y retorno sincronizado con tu horario.
6. **Expansión**: colonias en posiciones 4–12 (más campos, más deuterio).

---

## 13. Seguridad operativa

- `dry_run: true` hasta que el plan te convenza.
- `only_inactive_targets: true`, `max_actions_per_hour` bajo, `active_hours`
  realista (no 24/7), intervalos amplios y aleatorios.
- Perfil **Paranoico** de la GUI si tu prioridad es no destacar.
- No uses segundas cuentas ni coordines crashes entre cuentas tuyas.
- Recuerda: **ningún ajuste hace el botting permitido**. Es tu cuenta y tu
  riesgo.
