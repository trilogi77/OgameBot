# OGBot — Gestor autónomo para OGame

> Bot modular en Python que automatiza economía, investigación, flota, farmeo,
> reciclaje, expediciones, colonización y (opcional) creación de lunas, siguiendo
> una estrategia configurable. Corre en segundo plano mientras usas el ordenador.

---

## ⚠️ Aviso importante (léelo antes de nada)

**Gameforge prohíbe los bots en OGame** (sección de reglas anti-automatización).
Las cuentas detectadas se **banean de forma permanente**, y existe detección por
captcha en el Lobby y análisis de comportamiento. Este proyecto es **educativo**
y de demostración de ingeniería (teoría de juegos, optimización, simulación de
combate, automatización). Si lo usas en un servidor real:

- Asumes el riesgo de **perder tu cuenta**.
- La "humanización" (delays aleatorios, franja horaria, límite de acciones/hora,
  reutilización de sesión) **reduce pero NO elimina** el riesgo de detección.
- Empieza siempre con `dry_run: true` para validar la estrategia **sin ejecutar
  ninguna acción real**.

Úsalo sobre todo para aprender cómo se diseña un agente autónomo de estrategia.

---

## 1. Diseño

### 1.1 Arquitectura por capas

El bot separa **estrategia** (lógica pura, testeable, estable) de la **interacción
con el juego** (frágil, cambia con cada versión de OGame). Si Gameforge cambia el
HTML, solo tocas un archivo (`client.py`); el cerebro estratégico no se entera.

```
                 ┌──────────────────────────────────────────────┐
                 │                  brain.py                      │
                 │  Orquestador: bucle de ciclos en 2º plano      │
                 │  (prioriza fleetsave → economía → farmeo …)    │
                 └───────────────┬───────────────┬────────────────┘
                                 │               │
          ┌──────────────────────┘               └───────────────────────┐
          ▼  LÓGICA PURA (sin red, testeable)                 INTERACCIÓN ▼
 ┌───────────────────────────────────────────┐     ┌──────────────────────────┐
 │ economy.py   optimizador de minas (payback)│     │ client.py  (Playwright)  │
 │ research.py  prioridad de investigación    │     │  login, scraping, clicks │
 │ targets.py   rentabilidad/riesgo objetivos │     │  ⚠ selectores a verificar│
 │ combat.py    simulador Monte Carlo          │     └────────────┬─────────────┘
 │ fleet.py     fleetsave/expedición/reciclaje│                  │ solo lectura
 │ moons.py     lunas + colonización          │     ┌────────────▼─────────────┐
 │ gamedata.py  fórmulas/costes/stats          │     │ universe_api.py          │
 │ models.py    estructuras de datos          │     │  API XML pública (oficial)│
 └───────────────────────────────────────────┘     │  inteligencia del universo│
                                                     └──────────────────────────┘
```

### 1.2 Componentes

| Archivo | Responsabilidad |
|---|---|
| `gamedata.py` | Fórmulas reales de OGame: costes, producción, energía, distancia, tiempo de vuelo, combustible, slots de astrofísica. Stats de naves y defensas. |
| `models.py` | `Resources`, `Planet`, `Coords`, `EspionageReport`, `Target`, `FleetMovement`. |
| `universe_api.py` | Cliente de la **API XML pública** (`/api/players.xml`, `universe.xml`, `highscore.xml`…). Solo lectura, oficial. Es la base del buscador de objetivos. |
| `economy.py` | Decide la siguiente construcción por **payback marginal** (ver §2). Gestiona energía (solar/fusión). |
| `research.py` | Siguiente tecnología según prioridad + prerrequisitos. |
| `combat.py` | **Simulador Monte Carlo**: rapidfire, escudos con rebote, casco, 6 rondas, explosiones, bonos de tecnología, cálculo de escombros. |
| `targets.py` | Buscar, filtrar por distancia, espiar, **evaluar beneficio neto** (loot − combustible − pérdidas) y ordenar. |
| `fleet.py` | **Fleetsave**, dimensionado de cargueros, expediciones, planificación de reciclaje. |
| `moons.py` | Probabilidad de luna y naves a sacrificar; selección de mejor posición de colonia. |
| `client.py` | **Única** capa que toca el juego (Playwright). Login con reutilización de sesión, detección de captcha, scraping y acciones. Respeta `dry_run`. |
| `brain.py` | El bucle principal: prioridades, rate-limit, delays, franja horaria. |
| `config.py` / `config.example.yaml` | Toda la parametrización. Sin tocar código. |

### 1.3 Funcionamiento en segundo plano

- Bucle infinito con **intervalo aleatorio** entre ciclos (`cycle_interval_*`).
- Solo actúa dentro de tu **franja horaria** (`active_hours`), p.ej. 8:00–24:00.
- **Rate limiter** (`max_actions_per_hour`) + **delays humanizados** entre clics.
- **Reutiliza la sesión** del navegador (`ogame_session.json`) para minimizar
  logins (cada login es donde más aparece el captcha).
- Headless (`headless: true`) para no molestar mientras usas el PC.

---

## 2. La estrategia (el "cómo ganar")

OGame se gana con **economía compuesta + expansión + farmeo eficiente + no perder
flota nunca**. El bot implementa esa filosofía:

### 2.1 Economía: payback marginal, no ratios fijos
En vez del clásico "cristal 2 niveles por debajo del metal", el bot calcula para
cada mina el **tiempo de amortización** de subir un nivel:

```
payback (horas) = coste_del_nivel_siguiente / producción_extra_por_hora
```

(todo en "metal-equivalente" usando el `trade_ratio`). Sube la mina con **menor
payback**; si ninguna baja del umbral (`target_mine_ratio_payback_hours`, ~16h),
deja de invertir en minas y mete recursos en investigación/flota. La energía
(placa solar, y fusión cuando es rentable) tiene prioridad para no penalizar
producción.

### 2.2 Investigación: la columna vertebral
Orden recomendado (configurable):
1. **Energía** → desbloquea todo.
2. **Computación** → +1 slot de flota por nivel (clave para farmear en paralelo).
3. **Espionaje** → informes fiables sin que te detecten.
4. **Propulsión de combustión** → cargueros más rápidos y baratos de mover.
5. **Astrofísica** → cada 2 niveles = **+1 colonia y +1 slot de expedición**.
6. **Plasma** → **+1% metal, +0,66% cristal, +0,33% deut por nivel**. El mayor
   multiplicador económico del juego a largo plazo.

### 2.3 Farmeo de inactivos (núcleo del beneficio)
1. La **API pública** lista jugadores inactivos (`i`/`I`) y sus coordenadas.
2. Filtro por distancia desde tus planetas (combustible importa).
3. **Espía** los mejores candidatos.
4. Para cada uno: `loot = min(recursos·loot_percent, capacidad_carga)`; si tiene
   defensa, el **simulador de combate** estima probabilidad de victoria y pérdidas.
5. `score = valor(loot) − combustible − pérdidas_esperadas`. Ataca los de score
   positivo más alto, hasta `max_attack_targets_per_cycle`.
6. Por defecto `only_inactive_targets: true` (más seguro y menos conflictivo).

### 2.4 Reciclaje y expediciones
- Tras combates, se forma un **campo de escombros** (`debris_factor`, normalmente
  30%). El bot programa **recicladores** para recogerlos.
- **Expediciones** a la posición 16: recursos/naves/materia oscura gratis y
  mantienen la flota "fuera" (efecto fleetsave colateral).

### 2.5 No perder flota: FLEETSAVE
La regla de oro de OGame. **Nunca** dejes flota/recursos en el planeta estando
offline. El bot, antes de cada pausa larga, envía la flota en un **despliegue al
10% de velocidad** a tu planeta más lejano (o a expedición) de modo que **vuelva
justo cuando volverás a estar activo**. Esto es lo que te mantiene vivo.

### 2.6 Lunas
La probabilidad de luna = `min(20%, escombros / 100000)`. Para forzarla necesitas
un campo de escombros sobre tu planeta. **No puedes atacarte a ti mismo**, así que
en la práctica se aprovecha un ataque entrante contra tu defensa. El módulo calcula
cuántas naves equivalen a 100.000 de escombros (≈84 cazas ligeros) pero **deja la
ejecución a tu criterio** por las restricciones del juego.

### 2.7 Colonización
Prioriza posiciones centrales (4–12): más campos y más frías (más deuterio). El bot
busca el primer hueco libre con mejor "score" y envía una nave colonizadora cuando
la astrofísica te da un slot.

---

## 3. Cómo usarlo

### 3.1 Instalación
```bash
cd ogame-bot
python -m venv .venv && source .venv/bin/activate     # opcional
pip install -r requirements.txt
playwright install chromium
```

### 3.2 Configuración
```bash
cp config.example.yaml config.yaml
# edita config.yaml: server_url, universe_speed/fleet_speed/debris_factor
# (míralos en https://sXXX-es.ogame.gameforge.com/api/serverData.xml)
```
Credenciales **por variables de entorno** (no en el YAML):
```bash
export OGBOT_USER="tu_email@dominio.com"
export OGBOT_PASS="tu_password"
```

### 3.3 Primer arranque en modo seguro (sin ejecutar nada)
Con `dry_run: true` el bot **solo registra lo que haría**:
```bash
python main.py --once          # un solo ciclo, para ver el plan
tail -f ogbot.log
```
Verás líneas tipo `[DRY-RUN] Construir solar_plant en [1:100:8]`,
`[DRY-RUN] Flota {'large_cargo': 18} ... misión=attack`, etc.

### 3.4 Verificar selectores (paso obligatorio)
OGame cambia su HTML. Abre el juego en tu navegador con DevTools (F12), inspecciona
los elementos y **ajusta `SEL` y `PAGE` en `client.py`** a tu versión. Implementa
también el parseo de mensajes de espionaje y el formulario de `fleetdispatch`
(están marcados con `# TODO` y documentados).

### 3.5 Activarlo de verdad
Cuando el dry-run muestre decisiones razonables y los selectores estén verificados:
```yaml
dry_run: false
headless: true
```
```bash
# segundo plano sencillo (Linux/Mac)
nohup python main.py --config config.yaml > /dev/null 2>&1 &
```

#### Como servicio systemd (Linux, recomendado para 2º plano estable)
`/etc/systemd/system/ogbot.service`:
```ini
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

### 3.6 Captcha
Si aparece captcha en el login, ejecuta con `headless: false` y **resuélvelo a mano**
una vez; la sesión se guarda en `ogame_session.json` y reduce futuros logins.

---

## 4. Lo que está implementado vs. lo que debes completar

**Listo y probado (lógica pura):** fórmulas de juego, optimizador económico por
payback, prioridad de investigación con prerrequisitos, simulador de combate
Monte Carlo, evaluación de objetivos, fleetsave, expediciones, reciclaje, lunas,
colonización, cliente de la API pública, orquestador del ciclo, dry-run,
rate-limit y humanización.

**Debes verificar/completar (capa frágil del navegador, en `client.py`):**
- Selectores `SEL`/`PAGE` para tu versión de OGame.
- Parseo del **informe de espionaje** (`espionage()`).
- Relleno del formulario **`fleetdispatch`** (`send_fleet()`): naves, coords, misión
  (attack=1, transport=3, deploy=4, espionage=6, harvest=8, expedition=15), recursos.
- Lectura de **movimientos** y de **campos de escombros** en la galaxia.

Está así a propósito: esos detalles cambian con cada versión y no puedo verificarlos
contra tu servidor. El resto del bot es independiente de ellos.

---

## 5. Seguridad operativa (si decides usarlo)
- `dry_run: true` hasta validar.
- `only_inactive_targets: true`, `max_actions_per_hour` bajo, `active_hours`
  realista (no 24/7), intervalos de ciclo amplios y aleatorios.
- No uses segundas cuentas ni coordines crashes con cuentas tuyas (es sancionable).
- Recuerda: **ningún ajuste hace el botting permitido**. Es tu cuenta y tu riesgo.

---

## 6. Estructura del proyecto
```
ogame-bot/
├── main.py                 # entrada
├── requirements.txt
├── config.example.yaml     # copia a config.yaml
├── README.md
└── ogbot/
    ├── gamedata.py         # fórmulas y stats
    ├── models.py
    ├── config.py
    ├── utils.py            # logging, delays, rate-limit
    ├── universe_api.py     # API XML pública (inteligencia)
    ├── economy.py          # optimizador de minas
    ├── research.py
    ├── combat.py           # simulador Monte Carlo
    ├── targets.py          # rentabilidad/riesgo
    ├── fleet.py            # fleetsave / expediciones / reciclaje
    ├── moons.py            # lunas + colonización
    ├── client.py           # ⚠ Playwright: verifica selectores
    └── brain.py            # orquestador del ciclo
```
