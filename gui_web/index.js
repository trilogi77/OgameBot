// ==========================================================================
// OGBot Dashboard — Logic & Frontend API client
// ==========================================================================

let globalConfig = {};
let configSnapshot = null;     // copia profunda de la config tal y como llegó del servidor (para guardar solo diffs)
let planetsCache = [];
let fleetInMotionCache = {};   // naves en vuelo (flotas en movimiento)
let localPlanetsConfig = {};
let researchPriorityList = [];
let configLoaded = false;
let currentAccount = localStorage.getItem("ogbot_account") || "";
let accountsCache = [];

// Estado nuevo (ORION·OPS): dashboard, agenda, control remoto, registro
let uiBaseline = null;         // copia de la config tal y como quedó la UI tras cargar (para el badge de cambios)
let agendaCache = { tasks: [] };
let hourlyCache = [];          // stats_hourly.jsonl (C2)
let statsCache = {};           // último /api/stats (puntos, ranking, expe_outcomes)
let botStatusCache = null;     // último /api/botstatus (C6)
let expeStatusCache = {};      // último /api/expedition (para slots del KPI de flotas)
let buildStatusCache = {};     // último /api/buildstatus (línea de cola por tarjeta)
let parsedLogsCache = [];      // logs parseados (hora/nivel/módulo) para filtros y CSV
let dashRange = "7d";          // rango activo de la gráfica de evolución (24h|7d|30d)

// Parseo numérico seguro: campo vacío/no numérico => default (nunca 0 por accidente)
function parseI(val, def = null) {
    const parsed = parseInt(val);
    return isNaN(parsed) ? def : parsed;
}

function parseF(val, def = null) {
    const parsed = parseFloat(val);
    return isNaN(parsed) ? def : parsed;
}

// Añade ?account=<actual> a las rutas de API (multicuenta)
function api(path) {
    if (!currentAccount) return path;
    const sep = path.includes("?") ? "&" : "?";
    return path + sep + "account=" + encodeURIComponent(currentAccount);
}

// --------------------------------------------------------------------------
// Gestión de cuentas (multicuenta)
// --------------------------------------------------------------------------
function loadAccounts() {
    return fetch("/api/accounts")
        .then(r => r.json())
        .then(data => {
            accountsCache = data.accounts || [];
            if (!accountsCache.find(a => a.id === currentAccount)) {
                currentAccount = accountsCache.length ? accountsCache[0].id : "";
                localStorage.setItem("ogbot_account", currentAccount);
            }
            renderAccountSelect();
            renderAccountsTab();
        })
        .catch(() => {});
}

function renderAccountSelect() {
    const meta = document.getElementById("sb_accounts_meta");
    if (meta) {
        const running = accountsCache.filter(a => a.running).length;
        meta.textContent = accountsCache.length
            ? `${running}/${accountsCache.length} en marcha` : "sin cuentas";
    }
    const sel = document.getElementById("accountSelect");
    if (!sel) return;
    sel.innerHTML = "";
    if (!accountsCache.length) {
        const o = document.createElement("option");
        o.value = ""; o.textContent = "(sin cuentas)";
        sel.appendChild(o);
        return;
    }
    accountsCache.forEach(a => {
        const o = document.createElement("option");
        o.value = a.id;
        o.textContent = (a.running ? "🟢 " : "⚪ ") + a.id;
        if (a.id === currentAccount) o.selected = true;
        sel.appendChild(o);
    });
}

function renderAccountsTab() {
    const c = document.getElementById("accountsList");
    if (!c) return;
    if (!accountsCache.length) {
        c.innerHTML = '<div class="text-muted" style="font-size:13px;">No hay cuentas todavía. Crea una abajo.</div>';
        return;
    }
    c.innerHTML = accountsCache.map(a => `
        <div class="account-row${a.id === currentAccount ? ' active' : ''}">
            <span class="account-dot ${a.running ? 'on' : 'off'}"></span>
            <span class="account-name">${a.id}</span>
            <span class="account-port">CDP ${a.cdp_port}</span>
            <div class="account-actions">
                <button class="btn-secondary" onclick="switchAccount('${a.id}')">Seleccionar</button>
                ${a.running
                    ? `<button class="btn btn-danger" onclick="stopAccount('${a.id}')">Detener</button>`
                    : `<button class="btn btn-success" onclick="startAccount('${a.id}')">Iniciar</button>`}
                <button class="btn-exp-remove" title="Eliminar cuenta" onclick="deleteAccount('${a.id}')">🗑️</button>
            </div>
        </div>`).join("");
}

function switchAccount(id) {
    if (!id) return;
    currentAccount = id;
    localStorage.setItem("ogbot_account", id);
    configLoaded = false;
    configSnapshot = null;
    uiBaseline = null;
    messagesCache = [];
    lastMessagesSig = "";
    flightsCache = [];
    lastFlightsSig = "";
    queueDrafts = {};
    queueLocations = [];
    lastQueueLocSig = "";
    // Estado nuevo (dashboard/agenda/control remoto/registro)
    agendaCache = { tasks: [] };
    hourlyCache = [];
    statsCache = {};
    botStatusCache = null;
    expeStatusCache = {};
    buildStatusCache = {};
    historyCache = [];
    lastLogsLength = 0;
    rawLogsCache = [];
    parsedLogsCache = [];
    renderAccountSelect();
    renderAccountsTab();
    loadConfig();
    loadPlanets();
    loadStats();
    loadMessages();
    loadFlights();
    loadLogs();
    checkBotStatus();
    loadExpeditionStatus();
    loadBuildStatus();
    loadHistory();
    loadAgenda();
    loadHourly();
    loadBotStatus();
}

function createAccount() {
    const inp = document.getElementById("newAccountId");
    const id = (inp.value || "").trim();
    if (!id) { showToast("Escribe un nombre de cuenta", "warning"); return; }
    fetch("/api/accounts/create", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) })
        .then(r => r.json())
        .then(d => {
            if (d.error) { showToast(d.error, "danger"); return; }
            inp.value = "";
            showToast("Cuenta '" + d.id + "' creada", "success");
            loadAccounts().then(() => switchAccount(d.id));
        })
        .catch(e => showToast("Error: " + e, "danger"));
}

function deleteAccount(id) {
    if (!confirm("¿Eliminar la cuenta '" + id + "' y TODOS sus datos? Esto no se puede deshacer.")) return;
    fetch("/api/accounts/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) })
        .then(r => r.json())
        .then(d => {
            if (d.error) { showToast(d.error, "danger"); return; }
            showToast("Cuenta eliminada", "success");
            loadAccounts();
        });
}

function startAccount(id) {
    fetch("/api/start?account=" + encodeURIComponent(id), { method: "POST" })
        .then(r => r.json())
        .then(d => {
            showToast(d.error ? "Error: " + d.error : "Cuenta '" + id + "' iniciada", d.error ? "danger" : "success");
            setTimeout(loadAccounts, 600);
        });
}

function stopAccount(id) {
    fetch("/api/stop?account=" + encodeURIComponent(id), { method: "POST" })
        .then(r => r.json())
        .then(d => {
            showToast(d.error ? "Error: " + d.error : "Cuenta '" + id + "' detenida", d.error ? "danger" : "success");
            setTimeout(loadAccounts, 600);
        });
}

// Elementos del DOM
const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const btnStart = document.getElementById("btnStart");
const btnStop = document.getElementById("btnStop");
const btnSave = document.getElementById("btnSave");
const btnClearLogs = document.getElementById("btnClearLogs");
const terminalConsole = document.getElementById("terminalConsole");
const autoScrollCheck = document.getElementById("autoScroll");
const saveStatus = document.getElementById("saveStatus");
const planetsListContainer = document.getElementById("planetsListContainer");
const planetAlert = document.getElementById("planetAlert");

// Carga Inicial
window.addEventListener("DOMContentLoaded", () => {
    initTabs();
    initExpeditionShipsSelector();
    const expAutoToggle = document.getElementById("expedition_auto_ships");
    if (expAutoToggle) expAutoToggle.addEventListener("change", toggleExpeditionMode);
    const accSel = document.getElementById("accountSelect");
    if (accSel) accSel.addEventListener("change", e => switchAccount(e.target.value));
    loadAccounts().then(() => {
        loadConfig();
        loadPlanets();
        checkBotStatus();
        loadLogs();
        loadStats();
        loadMessages();
        loadFlights();
        loadExpeditionStatus();
        loadBuildStatus();
    });
    const msgFilter = document.getElementById("messages_filter");
    if (msgFilter) msgFilter.addEventListener("change", renderMessages);

    const fixLoc = document.getElementById("fix_location");
    if (fixLoc) fixLoc.addEventListener("change", populateFixBuildings);
    const fixBld = document.getElementById("fix_building");
    if (fixBld) fixBld.addEventListener("change", syncFixLevel);
    const btnFix = document.getElementById("btnFixLevel");
    if (btnFix) btnFix.addEventListener("click", submitLevelFix);
    const btnResync = document.getElementById("btnForceResync");
    if (btnResync) btnResync.addEventListener("click", forceResync);
    const btnResyncAll = document.getElementById("btnForceResyncAll");
    if (btnResyncAll) btnResyncAll.addEventListener("click", forceResyncAll);

    const queueLoc = document.getElementById("queue_location");
    if (queueLoc) queueLoc.addEventListener("change", renderQueueList);
    const btnQueueAdd = document.getElementById("btnQueueAdd");
    if (btnQueueAdd) btnQueueAdd.addEventListener("click", addToQueue);
    const btnQueueSave = document.getElementById("btnQueueSave");
    if (btnQueueSave) btnQueueSave.addEventListener("click", saveBuildQueue);
    initColonyLocator();
    
    const defSelect = document.getElementById("defense_planet_select");
    if (defSelect) {
        defSelect.addEventListener("change", (e) => {
            const coords = e.target.value;
            if (coords) {
                document.getElementById("defense_targets_panel").style.display = "block";
                renderDefenseTargetsList(coords);
            } else {
                document.getElementById("defense_targets_panel").style.display = "none";
            }
        });
    }
    
    const facSelect = document.getElementById("facilities_planet_select");
    if (facSelect) {
        facSelect.addEventListener("change", (e) => {
            const coords = e.target.value;
            if (coords) {
                document.getElementById("facilities_targets_panel").style.display = "block";
                renderFacilitiesTargetsList(coords);
            } else {
                document.getElementById("facilities_targets_panel").style.display = "none";
            }
        });
    }
    
    // Inicializar pestaña de directo del bot
    initLiveTab();
    
    // Configurar Intervalos
    setInterval(checkBotStatus, 3000);
    setInterval(loadLogs, 2000);
    setInterval(loadPlanets, 5000); // Recargar planetas si se detectan nuevos en vivo
    setInterval(loadStats, 4000);
    setInterval(loadMessages, 4000);
    setInterval(loadFlights, 4000);
    setInterval(updateFlightTimers, 1000);   // contador en vivo sin repintar la lista
    setInterval(loadExpeditionStatus, 2000);
    setInterval(loadBuildStatus, 2000);
    setInterval(loadAccounts, 4000);
    
    // Refrescar el directo si la pestaña está activa
    setInterval(() => {
        const liveTabBtn = document.querySelector('.tab-btn[data-tab="tab-live"]');
        if (liveTabBtn && liveTabBtn.classList.contains('active')) {
            updateLiveTab();
        }
    }, 1500);
});

// Event Listeners para Botones de Control
btnStart.addEventListener("click", () => {
    fetch(api("/api/start"), { method: "POST" })
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                showToast("Error: " + data.error, "danger");
            } else {
                showToast("Bot iniciado en segundo plano", "success");
                checkBotStatus();
            }
        })
        .catch(err => showToast("Error al iniciar bot: " + err, "danger"));
});

btnStop.addEventListener("click", () => {
    fetch(api("/api/stop"), { method: "POST" })
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                showToast("Error: " + data.error, "danger");
            } else {
                showToast("Bot detenido correctamente", "success");
                checkBotStatus();
            }
        })
        .catch(err => showToast("Error al detener bot: " + err, "danger"));
});

btnSave.addEventListener("click", saveChanges);
btnClearLogs.addEventListener("click", () => {
    terminalConsole.innerHTML = '<div class="log-line text-muted">Pantalla limpia. Esperando nuevos registros...</div>';
});

const btnTestTelegram = document.getElementById("btnTestTelegram");
if (btnTestTelegram) btnTestTelegram.addEventListener("click", testTelegram);

function testTelegram() {
    const token = getVal("telegram_token");
    const chatId = getVal("telegram_chat_id");
    const statusEl = document.getElementById("telegram_test_status");
    const setStatus = (msg, ok) => {
        if (!statusEl) return;
        statusEl.textContent = msg;
        statusEl.style.color = ok === undefined ? "" : (ok ? "var(--accent-success, #22c55e)" : "var(--accent-danger, #ef4444)");
    };
    if (!token || !chatId) {
        setStatus("Rellena el token y el ID de chat primero.", false);
        showToast("Faltan el token o el ID de chat de Telegram", "danger");
        return;
    }
    setStatus("Enviando mensaje de prueba…");
    btnTestTelegram.disabled = true;
    fetch(api("/api/telegram/test"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, chat_id: chatId })
    })
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                setStatus("Error: " + data.error, false);
                showToast("Telegram: " + data.error, "danger");
            } else {
                setStatus("✅ Mensaje enviado. Revisa tu Telegram.", true);
                showToast("Mensaje de prueba enviado a Telegram", "success");
            }
        })
        .catch(err => {
            setStatus("Error de red: " + err, false);
            showToast("Error al probar Telegram: " + err, "danger");
        })
        .finally(() => { btnTestTelegram.disabled = false; });
}

// --------------------------------------------------------------------------
// Gestión de Tabs (Pestañas)
// --------------------------------------------------------------------------
function initTabs() {
    const tabButtons = document.querySelectorAll(".tab-btn");
    const tabPanes = document.querySelectorAll(".tab-pane");

    tabButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const target = btn.dataset.tab;
            
            tabButtons.forEach(b => b.classList.remove("active"));
            tabPanes.forEach(p => p.classList.remove("active"));
            
            btn.classList.add("active");
            document.getElementById(target).classList.add("active");
        });
    });

    // Subpestañas (Objetivos, etc.)
    const subtabButtons = document.querySelectorAll(".subtab-btn");
    subtabButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const target = btn.dataset.subtab;
            const parent = btn.closest(".targets-container");
            if (parent) {
                parent.querySelectorAll(".subtab-btn").forEach(b => b.classList.remove("active"));
                parent.querySelectorAll(".subtab-pane").forEach(p => p.classList.remove("active"));
            }
            btn.classList.add("active");
            const targetPane = document.getElementById(target);
            if (targetPane) targetPane.classList.add("active");
        });
    });
}

// --------------------------------------------------------------------------
// Leer/Escribir Configuración
// --------------------------------------------------------------------------
function loadConfig() {
    fetch(api("/api/config"))
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                showToast("Error cargando configuración: " + data.error, "danger");
                return;
            }
            globalConfig = data;
            configSnapshot = JSON.parse(JSON.stringify(data));
            localPlanetsConfig = data.planets_config || {};
            mapConfigToUI(data);
            if (!configLoaded) {
                configLoaded = true;
                forceRerenderPlanets();
            }
            // Línea base de la UI para el contador de "cambios sin guardar"
            collectUIIntoConfig();
            uiBaseline = JSON.parse(JSON.stringify(globalConfig));
            updateDirtyBadge();
        })
        .catch(err => showToast("Error de conexión: " + err, "danger"));
}

function mapConfigToUI(cfg) {
    // Globales
    setVal("universe", cfg.universe);
    setVal("country", cfg.country);
    setVal("universe_speed", cfg.universe_speed !== undefined ? cfg.universe_speed : 1);
    setVal("server_url", cfg.server_url);
    setVal("username", cfg.username || "");
    setVal("password", cfg.password || "");
    
    if (cfg.active_hours && cfg.active_hours.length === 2) {
        setVal("active_hours_start", cfg.active_hours[0]);
        setVal("active_hours_end", cfg.active_hours[1]);
    }

    // Perfil de riesgo y ritmo/límites
    updateRiskProfileBadge(cfg.risk_profile || "normal");
    setVal("cycle_interval_min_s", cfg.cycle_interval_min_s !== undefined ? cfg.cycle_interval_min_s : 600);
    setVal("cycle_interval_max_s", cfg.cycle_interval_max_s !== undefined ? cfg.cycle_interval_max_s : 1500);
    setVal("min_action_delay_s", cfg.min_action_delay_s !== undefined ? cfg.min_action_delay_s : 3);
    setVal("max_action_delay_s", cfg.max_action_delay_s !== undefined ? cfg.max_action_delay_s : 11);
    setVal("max_actions_per_hour", cfg.max_actions_per_hour !== undefined ? cfg.max_actions_per_hour : 40);
    setVal("farming_attack_cooldown_hours", cfg.farming_attack_cooldown_hours !== undefined ? cfg.farming_attack_cooldown_hours : 2);

    setCheck("dry_run", cfg.dry_run);
    setCheck("headless", cfg.headless);
    setCheck("monitor_only", cfg.monitor_only);
    setCheck("enable_attack_escape", cfg.enable_attack_escape);
    setVal("attack_check_min_mins", Math.round((cfg.attack_check_interval_min_s !== undefined ? cfg.attack_check_interval_min_s : 300) / 60));
    setVal("attack_check_max_mins", Math.round((cfg.attack_check_interval_max_s !== undefined ? cfg.attack_check_interval_max_s : 780) / 60));
    setCheck("enable_spy_watch", cfg.enable_spy_watch !== false);
    setVal("spy_watch_cooldown_mins", cfg.spy_watch_cooldown_mins !== undefined ? cfg.spy_watch_cooldown_mins : 30);
    setCheck("spy_watch_messages", cfg.spy_watch_messages !== false);
    setCheck("enable_fleetsave", cfg.enable_fleetsave);
    setCheck("fleetsave_only_if_hostile", !!cfg.fleetsave_only_if_hostile);
    setVal("fleetsave_mission", cfg.fleetsave_mission || "deploy");
    setCheck("fleetsave_carry_resources", cfg.fleetsave_carry_resources !== false);
    setCheck("fleetsave_recall_halfway", !!cfg.fleetsave_recall_halfway);
    setCheck("fleetsave_prefer_moon", cfg.fleetsave_prefer_moon !== false);
    setCheck("enable_night_sweep", cfg.enable_night_sweep);
    setVal("night_sweep_interval_hours", cfg.night_sweep_interval_hours !== undefined ? cfg.night_sweep_interval_hours : 2.0);
    setVal("telegram_token", cfg.telegram_token || "");
    setVal("telegram_chat_id", cfg.telegram_chat_id || "");

    // Módulos
    setCheck("enable_economy", cfg.enable_economy);
    setVal("economy_run_interval_mins", cfg.economy_run_interval_mins !== undefined ? cfg.economy_run_interval_mins : 0);
    setCheck("enable_research", cfg.enable_research);
    setCheck("enable_facilities", cfg.enable_facilities);
    setCheck("enable_farming", cfg.enable_farming);
    setVal("farming_run_interval_mins", cfg.farming_run_interval_mins !== undefined ? cfg.farming_run_interval_mins : 0);
    setCheck("enable_fleet_building", cfg.enable_fleet_building);
    setCheck("fleet_auto_build", cfg.fleet_auto_build);
    setCheck("empire_auto", cfg.empire_auto);
    setVal("fleet_priority", cfg.fleet_priority || "economy");
    setCheck("enable_expeditions", cfg.enable_expeditions);
    setVal("expeditions_run_interval_mins", cfg.expeditions_run_interval_mins !== undefined ? cfg.expeditions_run_interval_mins : 0);
    setCheck("expedition_auto_ships", cfg.expedition_auto_ships);
    setCheck("expedition_smart_schedule", cfg.expedition_smart_schedule !== false);
    setCheck("expedition_rotate_systems", cfg.expedition_rotate_systems !== false);
    setCheck("expedition_use_pathfinder", cfg.expedition_use_pathfinder);
    setCheck("expedition_send_probe", cfg.expedition_send_probe);
    setVal("expedition_probe_count", cfg.expedition_probe_count !== undefined ? cfg.expedition_probe_count : 1);
    setCheck("expedition_discoverer_class", cfg.expedition_discoverer_class);
    setVal("expedition_destroyer_count", cfg.expedition_destroyer_count !== undefined ? cfg.expedition_destroyer_count : 0);
    setVal("expedition_cargo_ship", cfg.expedition_cargo_ship || "large_cargo");
    setVal("expedition_hyperspace_level", cfg.expedition_hyperspace_level !== undefined ? cfg.expedition_hyperspace_level : 0);
    setVal("expedition_top1_points", cfg.expedition_top1_points !== undefined ? cfg.expedition_top1_points : 0);
    setVal("expedition_find_safety", cfg.expedition_find_safety !== undefined ? cfg.expedition_find_safety : 1.0);
    setVal("expedition_min_cargo", cfg.expedition_min_cargo !== undefined ? cfg.expedition_min_cargo : 1);
    setVal("expedition_max_cargo", cfg.expedition_max_cargo !== undefined ? cfg.expedition_max_cargo : 0);
    setVal("expedition_hold_hours", cfg.expedition_hold_hours !== undefined ? cfg.expedition_hold_hours : 1.0);
    setVal("expedition_system_range", cfg.expedition_system_range !== undefined ? cfg.expedition_system_range : 15);
    setVal("expedition_position", cfg.expedition_position !== undefined ? cfg.expedition_position : 16);
    setCheck("enable_recycling", cfg.enable_recycling);
    setVal("recycling_min_debris", cfg.recycling_min_debris !== undefined ? cfg.recycling_min_debris : 8000);
    setVal("recycling_run_interval_mins", cfg.recycling_run_interval_mins !== undefined ? cfg.recycling_run_interval_mins : 0);
    setCheck("enable_defense", cfg.enable_defense);
    setCheck("enable_lifeforms", cfg.enable_lifeforms);
    setCheck("enable_colonization", cfg.enable_colonization);
    setCheck("only_inactive_targets", cfg.only_inactive_targets);
    setVal("max_attack_targets_per_cycle", cfg.max_attack_targets_per_cycle !== undefined ? cfg.max_attack_targets_per_cycle : 8);
    setVal("min_loot_value", cfg.min_loot_value !== undefined ? cfg.min_loot_value : 50000);
    setCheck("farm_with_probes", cfg.farm_with_probes);
    setCheck("farm_recycle_debris", cfg.farm_recycle_debris !== undefined ? cfg.farm_recycle_debris : true);
    setCheck("farm_auto_fleet", cfg.farm_auto_fleet);
    setCheck("farming_smart_schedule", cfg.farming_smart_schedule !== undefined ? cfg.farming_smart_schedule : true);
    setCheck("farming_skip_active_targets", cfg.farming_skip_active_targets !== undefined ? cfg.farming_skip_active_targets : true);
    setVal("farming_blacklist_days", cfg.farming_blacklist_days !== undefined ? cfg.farming_blacklist_days : 7);
    setVal("deuterium_reserve", cfg.deuterium_reserve !== undefined ? cfg.deuterium_reserve : 0);
    setCheck("special_server_start", cfg.special_server_start);
    setVal("special_new_planet", cfg.special_new_planet || "");
    setCheck("special_new_planet_auto", cfg.special_new_planet_auto);
    setVal("espionage_probe_cargo", cfg.espionage_probe_cargo !== undefined ? cfg.espionage_probe_cargo : 0);

    // Objetivos de flota
    const targets = cfg.fleet_targets || {};
    setVal("target_large_cargo", targets.large_cargo || "");
    setVal("target_small_cargo", targets.small_cargo || "");
    setVal("target_recycler", targets.recycler || "");
    setVal("target_espionage_probe", targets.espionage_probe || "");
    setVal("target_light_fighter", targets.light_fighter || "");
    setVal("target_cruiser", targets.cruiser || "");
    setVal("target_battleship", targets.battleship || "");

    // Plantilla de flota de ataque (farmeo)
    const template = cfg.attacker_fleet_template || {};
    setVal("temp_small_cargo", template.small_cargo || "");
    setVal("temp_large_cargo", template.large_cargo || "");
    setVal("temp_light_fighter", template.light_fighter || "");
    setVal("temp_cruiser", template.cruiser || "");

    // Límites de investigación
    const caps = cfg.research_caps || {};
    setVal("cap_energy_tech", caps.energy_tech || "");
    setVal("cap_laser_tech", caps.laser_tech || "");
    setVal("cap_ion_tech", caps.ion_tech || "");
    setVal("cap_hyperspace_tech", caps.hyperspace_tech || "");

    // Flota de expedición (dinámico)
    renderExpeditionShips(cfg);
    toggleExpeditionMode();

    // Objetivos de minas
    setVal("target_metal_mine", cfg.target_metal_mine !== undefined ? cfg.target_metal_mine : 99);
    setVal("target_crystal_mine", cfg.target_crystal_mine !== undefined ? cfg.target_crystal_mine : 99);
    setVal("target_deut_synth", cfg.target_deut_synth !== undefined ? cfg.target_deut_synth : 99);
    setVal("target_mine_ratio_payback_hours", cfg.target_mine_ratio_payback_hours !== undefined ? cfg.target_mine_ratio_payback_hours : 30);

    // Objetivos de defensa
    setVal("defense_batch_size", cfg.defense_batch_size !== undefined ? cfg.defense_batch_size : 25);

    // Lista de prioridades de investigación
    researchPriorityList = cfg.research_priority || [];
    renderResearchPriorityList();

    // Cobertura completa del config.yaml (campos sin GUI hasta ahora)
    setVal("proxy_server", cfg.proxy_server || "");
    setVal("proxy_username", cfg.proxy_username || "");
    setVal("proxy_password", cfg.proxy_password || "");
    setVal("fleet_speed", cfg.fleet_speed !== undefined ? cfg.fleet_speed : 1);
    setVal("loot_percent", cfg.loot_percent !== undefined ? cfg.loot_percent : 0.5);
    setVal("debris_factor", cfg.debris_factor !== undefined ? cfg.debris_factor : 0.3);
    setCheck("debris_includes_deut", cfg.debris_includes_deut);
    setVal("server_playstyle", cfg.server_playstyle || "defensive");
    setCheck("fleetsave_warn_phalanx", cfg.fleetsave_warn_phalanx !== false);
    setVal("keep_free_fleet_slots", cfg.keep_free_fleet_slots !== undefined ? cfg.keep_free_fleet_slots : 1);
    setVal("max_mine_level", cfg.max_mine_level !== undefined ? cfg.max_mine_level : 40);
    setVal("keep_resources_buffer", cfg.keep_resources_buffer !== undefined ? cfg.keep_resources_buffer : 0.10);
    setVal("storage_fill_trigger_percent", cfg.storage_fill_trigger_percent !== undefined ? cfg.storage_fill_trigger_percent : 0.90);
    setVal("storage_min_capacity_target", cfg.storage_min_capacity_target !== undefined ? cfg.storage_min_capacity_target : 1000000);
    setVal("max_saving_hours_economy", cfg.max_saving_hours_economy !== undefined ? cfg.max_saving_hours_economy : 4);
    setCheck("enable_fusion_reactor", cfg.enable_fusion_reactor !== false);
    setVal("fusion_reactor_solar_offset", cfg.fusion_reactor_solar_offset !== undefined ? cfg.fusion_reactor_solar_offset : 25);
    setVal("target_robotics_factory", cfg.target_robotics_factory || "");
    setVal("target_shipyard", cfg.target_shipyard || "");
    setVal("target_research_lab", cfg.target_research_lab || "");
    setVal("target_nanite_factory", cfg.target_nanite_factory || "");
    setVal("max_colonies", cfg.max_colonies !== undefined ? cfg.max_colonies : 9);
    setVal("preferred_colony_positions", fmtList(cfg.preferred_colony_positions));
    setVal("max_saving_hours_research", cfg.max_saving_hours_research !== undefined ? cfg.max_saving_hours_research : 6);
    setVal("research_weights", fmtPairs(cfg.research_weights));
    setVal("max_target_distance_systems", cfg.max_target_distance_systems !== undefined ? cfg.max_target_distance_systems : 200);
    setCheck("avoid_strong_players", cfg.avoid_strong_players !== false);
    setVal("fleet_multipliers", fmtPairs(cfg.fleet_multipliers));
    setVal("recycling_system_range", cfg.recycling_system_range !== undefined ? cfg.recycling_system_range : 0);
    setCheck("enable_cargo_building", cfg.enable_cargo_building);
    setCheck("enable_moon_creation", cfg.enable_moon_creation);
    setVal("moon_target_debris", cfg.moon_target_debris !== undefined ? cfg.moon_target_debris : 100000);
    setVal("moon_sacrifice_ship", cfg.moon_sacrifice_ship || "light_fighter");
    setVal("feed_min_send", cfg.feed_min_send !== undefined ? cfg.feed_min_send : 5000);
    setVal("feed_round_up", cfg.feed_round_up !== undefined ? cfg.feed_round_up : 1000);
    setCheck("enable_telegram_commands", cfg.enable_telegram_commands !== false);
    setCheck("enable_build_queue", cfg.enable_build_queue !== false);
    setCheck("enable_state_cache", cfg.enable_state_cache !== false);
    setVal("state_resync_hours", cfg.state_resync_hours !== undefined ? cfg.state_resync_hours : 6);
    setCheck("enable_selector_canary", cfg.enable_selector_canary !== false);
    setVal("cdp_port", cfg.cdp_port !== undefined ? cfg.cdp_port : 9222);
    setVal("login_human_check_timeout_s", cfg.login_human_check_timeout_s !== undefined ? cfg.login_human_check_timeout_s : 300);
    setVal("log_level", cfg.log_level || "INFO");

    // Orden del ciclo (C4) + módulos del dashboard
    applyCycleOrderToChips(cfg.cycle_order);
    renderDashModules();
}

// Vuelca TODO el formulario en globalConfig / localPlanetsConfig. Extraído de saveChanges
// (mismo código) para reutilizarlo en el contador de "cambios sin guardar". Sin red.
function collectUIIntoConfig() {
    if (!configLoaded || !configSnapshot) return;

    // Guardar objetivos de defensa actuales del planeta seleccionado en memoria
    const selectedPlanetCoords = document.getElementById("defense_planet_select").value;
    if (selectedPlanetCoords) {
        saveCurrentDefenseTargetsInMemory(selectedPlanetCoords);
    }

    // Guardar objetivos de instalaciones actuales del planeta seleccionado en memoria
    const selectedFacPlanetCoords = document.getElementById("facilities_planet_select").value;
    if (selectedFacPlanetCoords) {
        saveCurrentFacilitiesTargetsInMemory(selectedFacPlanetCoords);
    }

    // Actualizar globalConfig desde la UI
    globalConfig.universe = getVal("universe");
    globalConfig.country = getVal("country");
    globalConfig.universe_speed = parseF(getVal("universe_speed"), 1);
    globalConfig.server_url = getVal("server_url");
    globalConfig.username = getVal("username");
    globalConfig.password = getVal("password");
    
    const hStart = parseInt(getVal("active_hours_start"));
    const hEnd = parseInt(getVal("active_hours_end"));
    if (!isNaN(hStart) && !isNaN(hEnd)) {
        globalConfig.active_hours = [hStart, hEnd];
    }

    globalConfig.dry_run = getCheck("dry_run");
    globalConfig.headless = getCheck("headless");
    globalConfig.monitor_only = getCheck("monitor_only");
    globalConfig.enable_attack_escape = getCheck("enable_attack_escape");
    globalConfig.attack_check_interval_min_s = parseI(getVal("attack_check_min_mins"), 5) * 60;
    globalConfig.attack_check_interval_max_s = parseI(getVal("attack_check_max_mins"), 13) * 60;
    globalConfig.cycle_interval_min_s = parseF(getVal("cycle_interval_min_s"), 600);
    globalConfig.cycle_interval_max_s = parseF(getVal("cycle_interval_max_s"), 1500);
    globalConfig.min_action_delay_s = parseF(getVal("min_action_delay_s"), 3);
    globalConfig.max_action_delay_s = parseF(getVal("max_action_delay_s"), 11);
    globalConfig.max_actions_per_hour = parseI(getVal("max_actions_per_hour"), 40);
    globalConfig.farming_attack_cooldown_hours = parseF(getVal("farming_attack_cooldown_hours"), 2);
    globalConfig.enable_spy_watch = getCheck("enable_spy_watch");
    globalConfig.spy_watch_cooldown_mins = parseI(getVal("spy_watch_cooldown_mins"), 30);
    globalConfig.spy_watch_messages = getCheck("spy_watch_messages");
    globalConfig.enable_fleetsave = getCheck("enable_fleetsave");
    globalConfig.fleetsave_mission = getVal("fleetsave_mission");
    globalConfig.fleetsave_carry_resources = getCheck("fleetsave_carry_resources");
    globalConfig.fleetsave_recall_halfway = getCheck("fleetsave_recall_halfway");
    globalConfig.fleetsave_prefer_moon = getCheck("fleetsave_prefer_moon");
    globalConfig.enable_night_sweep = getCheck("enable_night_sweep");
    const _nsi = parseF(getVal("night_sweep_interval_hours"), 2.0);
    globalConfig.night_sweep_interval_hours = _nsi <= 0 ? 2.0 : _nsi;
    globalConfig.telegram_token = getVal("telegram_token");
    globalConfig.telegram_chat_id = getVal("telegram_chat_id");

    globalConfig.enable_economy = getCheck("enable_economy");
    globalConfig.economy_run_interval_mins = parseI(getVal("economy_run_interval_mins"), 0);
    globalConfig.enable_research = getCheck("enable_research");
    globalConfig.enable_facilities = getCheck("enable_facilities");
    globalConfig.enable_farming = getCheck("enable_farming");
    globalConfig.farming_run_interval_mins = parseI(getVal("farming_run_interval_mins"), 0);
    globalConfig.enable_fleet_building = getCheck("enable_fleet_building");
    globalConfig.fleet_auto_build = getCheck("fleet_auto_build");
    globalConfig.empire_auto = getCheck("empire_auto");
    globalConfig.fleet_priority = getVal("fleet_priority") || "economy";
    globalConfig.enable_expeditions = getCheck("enable_expeditions");
    globalConfig.expeditions_run_interval_mins = parseI(getVal("expeditions_run_interval_mins"), 0);
    globalConfig.expedition_auto_ships = getCheck("expedition_auto_ships");
    globalConfig.expedition_smart_schedule = getCheck("expedition_smart_schedule");
    globalConfig.expedition_rotate_systems = getCheck("expedition_rotate_systems");
    globalConfig.expedition_use_pathfinder = getCheck("expedition_use_pathfinder");
    globalConfig.expedition_send_probe = getCheck("expedition_send_probe");
    globalConfig.expedition_probe_count = parseI(getVal("expedition_probe_count"), 1);
    globalConfig.expedition_discoverer_class = getCheck("expedition_discoverer_class");
    globalConfig.expedition_destroyer_count = parseI(getVal("expedition_destroyer_count"), 0);
    globalConfig.expedition_cargo_ship = getVal("expedition_cargo_ship") || "large_cargo";
    globalConfig.expedition_hyperspace_level = parseI(getVal("expedition_hyperspace_level"), 0);
    globalConfig.expedition_top1_points = parseI(getVal("expedition_top1_points"), 0);
    globalConfig.expedition_find_safety = parseF(getVal("expedition_find_safety"), 1.0);
    globalConfig.expedition_min_cargo = parseI(getVal("expedition_min_cargo"), 1);
    globalConfig.expedition_max_cargo = parseI(getVal("expedition_max_cargo"), 0);
    globalConfig.expedition_hold_hours = parseF(getVal("expedition_hold_hours"), 1.0);
    globalConfig.expedition_system_range = parseI(getVal("expedition_system_range"), 15);
    globalConfig.expedition_position = parseI(getVal("expedition_position"), 16);
    globalConfig.enable_recycling = getCheck("enable_recycling");
    globalConfig.recycling_run_interval_mins = parseI(getVal("recycling_run_interval_mins"), 0);
    globalConfig.enable_defense = getCheck("enable_defense");
    globalConfig.enable_lifeforms = getCheck("enable_lifeforms");
    globalConfig.enable_colonization = getCheck("enable_colonization");
    globalConfig.only_inactive_targets = getCheck("only_inactive_targets");
    globalConfig.max_attack_targets_per_cycle = parseI(getVal("max_attack_targets_per_cycle"), 8);
    globalConfig.min_loot_value = parseI(getVal("min_loot_value"), 50000);
    globalConfig.farm_with_probes = getCheck("farm_with_probes");
    globalConfig.farm_recycle_debris = getCheck("farm_recycle_debris");
    globalConfig.farm_auto_fleet = getCheck("farm_auto_fleet");
    globalConfig.farming_smart_schedule = getCheck("farming_smart_schedule");
    globalConfig.farming_skip_active_targets = getCheck("farming_skip_active_targets");
    globalConfig.farming_blacklist_days = parseF(getVal("farming_blacklist_days"), 7);
    globalConfig.deuterium_reserve = parseI(getVal("deuterium_reserve"), 0);
    globalConfig.special_server_start = getCheck("special_server_start");
    globalConfig.special_new_planet = (getVal("special_new_planet") || "").trim();
    globalConfig.special_new_planet_auto = getCheck("special_new_planet_auto");
    globalConfig.espionage_probe_cargo = parseI(getVal("espionage_probe_cargo"), 0);

    // Guardar objetivos de flota
    globalConfig.fleet_targets = {
        large_cargo: parseI(getVal("target_large_cargo"), 0),
        small_cargo: parseI(getVal("target_small_cargo"), 0),
        recycler: parseI(getVal("target_recycler"), 0),
        espionage_probe: parseI(getVal("target_espionage_probe"), 0),
        light_fighter: parseI(getVal("target_light_fighter"), 0),
        cruiser: parseI(getVal("target_cruiser"), 0),
        battleship: parseI(getVal("target_battleship"), 0)
    };

    // Guardar plantilla de flota de ataque (farmeo)
    globalConfig.attacker_fleet_template = {
        small_cargo: parseI(getVal("temp_small_cargo"), 0),
        large_cargo: parseI(getVal("temp_large_cargo"), 0),
        light_fighter: parseI(getVal("temp_light_fighter"), 0),
        cruiser: parseI(getVal("temp_cruiser"), 0)
    };

    // Guardar límites de investigación: solo las claves con valor. Un campo vacío
    // NO es 0 ("nunca investigar"); si todos están vacíos no se toca research_caps.
    const researchCaps = {};
    [["energy_tech", "cap_energy_tech"],
     ["laser_tech", "cap_laser_tech"],
     ["ion_tech", "cap_ion_tech"],
     ["hyperspace_tech", "cap_hyperspace_tech"]].forEach(([key, inputId]) => {
        const cap = parseI(getVal(inputId), null);
        if (cap !== null) researchCaps[key] = cap;
    });
    if (Object.keys(researchCaps).length > 0) {
        globalConfig.research_caps = researchCaps;
    }

    // Guardar flota de expedición (dinámico)
    const expShips = {};
    const rows = document.querySelectorAll(".exp-ship-row");
    rows.forEach(row => {
        const shipType = row.dataset.shipType;
        const qtyInput = row.querySelector(".exp-ship-qty");
        const qty = parseI(qtyInput.value, 0);
        if (qty > 0) {
            expShips[shipType] = qty;
        }
    });
    globalConfig.expedition_ships = expShips;

    // Guardar objetivos de minas
    globalConfig.target_metal_mine = parseI(getVal("target_metal_mine"), 99);
    globalConfig.target_crystal_mine = parseI(getVal("target_crystal_mine"), 99);
    globalConfig.target_deut_synth = parseI(getVal("target_deut_synth"), 99);
    globalConfig.target_mine_ratio_payback_hours = parseI(getVal("target_mine_ratio_payback_hours"), 30);

    // Guardar objetivos de defensas
    globalConfig.defense_batch_size = parseI(getVal("defense_batch_size"), 25);
    globalConfig.recycling_min_debris = parseI(getVal("recycling_min_debris"), 8000);

    // Guardar prioridades de investigación
    globalConfig.research_priority = researchPriorityList;

    // Cobertura completa del config.yaml (campos sin GUI hasta ahora)
    globalConfig.proxy_server = getVal("proxy_server");
    globalConfig.proxy_username = getVal("proxy_username");
    globalConfig.proxy_password = getVal("proxy_password");
    globalConfig.fleet_speed = parseF(getVal("fleet_speed"), 1);
    globalConfig.loot_percent = parseF(getVal("loot_percent"), 0.5);
    globalConfig.debris_factor = parseF(getVal("debris_factor"), 0.3);
    globalConfig.debris_includes_deut = getCheck("debris_includes_deut");
    globalConfig.server_playstyle = getVal("server_playstyle") || "defensive";
    globalConfig.fleetsave_warn_phalanx = getCheck("fleetsave_warn_phalanx");
    globalConfig.keep_free_fleet_slots = parseI(getVal("keep_free_fleet_slots"), 1);
    globalConfig.max_mine_level = parseI(getVal("max_mine_level"), 40);
    globalConfig.keep_resources_buffer = parseF(getVal("keep_resources_buffer"), 0.10);
    globalConfig.storage_fill_trigger_percent = parseF(getVal("storage_fill_trigger_percent"), 0.90);
    globalConfig.storage_min_capacity_target = parseI(getVal("storage_min_capacity_target"), 1000000);
    globalConfig.max_saving_hours_economy = parseF(getVal("max_saving_hours_economy"), 4);
    globalConfig.enable_fusion_reactor = getCheck("enable_fusion_reactor");
    globalConfig.fusion_reactor_solar_offset = parseI(getVal("fusion_reactor_solar_offset"), 25);
    globalConfig.target_robotics_factory = parseI(getVal("target_robotics_factory"), 0);
    globalConfig.target_shipyard = parseI(getVal("target_shipyard"), 0);
    globalConfig.target_research_lab = parseI(getVal("target_research_lab"), 0);
    globalConfig.target_nanite_factory = parseI(getVal("target_nanite_factory"), 0);
    globalConfig.max_colonies = parseI(getVal("max_colonies"), 9);
    const colonyPos = parseIntList(getVal("preferred_colony_positions"));
    if (colonyPos.length) globalConfig.preferred_colony_positions = colonyPos;
    globalConfig.max_saving_hours_research = parseF(getVal("max_saving_hours_research"), 6);
    const rWeights = parsePairs(getVal("research_weights"));
    if (Object.keys(rWeights).length) globalConfig.research_weights = rWeights;
    globalConfig.max_target_distance_systems = parseI(getVal("max_target_distance_systems"), 200);
    globalConfig.avoid_strong_players = getCheck("avoid_strong_players");
    const fMult = parsePairs(getVal("fleet_multipliers"));
    if (Object.keys(fMult).length) globalConfig.fleet_multipliers = fMult;
    globalConfig.recycling_system_range = parseI(getVal("recycling_system_range"), 0);
    globalConfig.enable_cargo_building = getCheck("enable_cargo_building");
    globalConfig.enable_moon_creation = getCheck("enable_moon_creation");
    globalConfig.moon_target_debris = parseI(getVal("moon_target_debris"), 100000);
    globalConfig.moon_sacrifice_ship = getVal("moon_sacrifice_ship") || "light_fighter";
    globalConfig.feed_min_send = parseI(getVal("feed_min_send"), 5000);
    globalConfig.feed_round_up = parseI(getVal("feed_round_up"), 1000);
    globalConfig.enable_telegram_commands = getCheck("enable_telegram_commands");
    globalConfig.enable_build_queue = getCheck("enable_build_queue");
    globalConfig.enable_state_cache = getCheck("enable_state_cache");
    globalConfig.state_resync_hours = parseF(getVal("state_resync_hours"), 6);
    globalConfig.enable_selector_canary = getCheck("enable_selector_canary");
    globalConfig.cdp_port = parseI(getVal("cdp_port"), 9222);
    globalConfig.login_human_check_timeout_s = parseI(getVal("login_human_check_timeout_s"), 300);
    globalConfig.log_level = getVal("log_level") || "INFO";

    // Recoger valores de planetas
    const planetCards = document.querySelectorAll(".planet-card");
    planetCards.forEach(card => {
        const coords = card.dataset.coords;
        if (!localPlanetsConfig[coords]) {
            localPlanetsConfig[coords] = {};
        }
        
        localPlanetsConfig[coords].enable_economy = card.querySelector(".planet-economy").checked;
        localPlanetsConfig[coords].enable_defense = card.querySelector(".planet-defense").checked;
        localPlanetsConfig[coords].enable_facilities = card.querySelector(".planet-facilities").checked;
        localPlanetsConfig[coords].enable_lifeforms = card.querySelector(".planet-lifeforms").checked;
        localPlanetsConfig[coords].enable_expeditions = card.querySelector(".planet-expeditions").checked;
        localPlanetsConfig[coords].enable_fleet_building = card.querySelector(".planet-fleet-building").checked;
        localPlanetsConfig[coords].enable_farming = card.querySelector(".planet-farming").checked;
        localPlanetsConfig[coords].enable_recycling = card.querySelector(".planet-recycling").checked;
        const _ns = card.querySelector(".planet-night-sweep");
        if (_ns) localPlanetsConfig[coords].enable_night_sweep = _ns.checked;
        // Origen por función (planeta/luna/ambos); solo presentes si la ubicación tiene luna.
        [["planet-expeditions-from", "expeditions_from"],
         ["planet-farming-from", "farming_from"],
         ["planet-recycling-from", "recycling_from"],
         ["planet-night-sweep-from", "night_sweep_from"]].forEach(([cls, key]) => {
            const sel = card.querySelector("." + cls);
            if (sel) localPlanetsConfig[coords][key] = sel.value;
        });
        const _ft = card.querySelector(".planet-feed-target");
        if (_ft) localPlanetsConfig[coords].feed_target = _ft.checked;
        const _fs = card.querySelector(".planet-feed-source");
        if (_fs) localPlanetsConfig[coords].feed_source = _fs.checked;

        // Eliminar ratios de defensa obsoletos
        delete localPlanetsConfig[coords].defense_rockets_per_mine;
        delete localPlanetsConfig[coords].defense_lasers_per_mine;
        delete localPlanetsConfig[coords].defense_heavy_laser_per_mine;
        delete localPlanetsConfig[coords].defense_ion_per_mine;
        delete localPlanetsConfig[coords].defense_gauss_per_mine;
        delete localPlanetsConfig[coords].defense_plasma_per_mine;
    });

    globalConfig.planets_config = localPlanetsConfig;

    // Claves nuevas (C4/C7): solo se escriben si ya existían en el snapshot o si el
    // usuario las activó/cambió, para no generar diffs fantasma con configs antiguas.
    const fsHostile = getCheck("fleetsave_only_if_hostile");
    if (configSnapshot.fleetsave_only_if_hostile !== undefined || fsHostile) {
        globalConfig.fleetsave_only_if_hostile = fsHostile;
    }
    const cycleOrder = currentCycleOrder();
    if (cycleOrder.length === DEFAULT_CYCLE_ORDER.length &&
        (configSnapshot.cycle_order !== undefined || cycleOrder.join() !== DEFAULT_CYCLE_ORDER.join())) {
        globalConfig.cycle_order = cycleOrder;
    }
}

function saveChanges() {
    // Sin config cargada no se guarda nada: se machacaría el YAML con valores por defecto
    if (!configLoaded || !configSnapshot) {
        showToast("La configuración aún no se ha cargado. Espera un momento y vuelve a intentarlo.", "danger");
        return;
    }
    collectUIIntoConfig();

    // Guardado por diff: POSTear solo las claves que cambiaron respecto al snapshot
    // (el backend hace merge clave a clave, así que lo no enviado se conserva).
    const changed = {};
    Object.keys(globalConfig).forEach(key => {
        if (JSON.stringify(globalConfig[key]) !== JSON.stringify(configSnapshot[key])) {
            changed[key] = globalConfig[key];
        }
    });
    if (Object.keys(changed).length === 0) {
        showToast("No hay cambios que guardar", "success");
        return;
    }

    fetch(api("/api/config"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(changed)
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === "success") {
            configSnapshot = JSON.parse(JSON.stringify(globalConfig));
            uiBaseline = JSON.parse(JSON.stringify(globalConfig));
            updateDirtyBadge();
            renderDashModules();
            showToast("Configuración guardada correctamente", "success");
        } else {
            showToast("Error al guardar: " + data.error, "danger");
        }
    })
    .catch(err => showToast("Error de conexión: " + err, "danger"));
}

// --------------------------------------------------------------------------
// Carga y Renderizado de Planetas
// --------------------------------------------------------------------------
function loadPlanets() {
    fetch(api("/api/planets"))
        .then(res => res.json())
        .then(data => {
            if (data.planets && data.planets.length > 0) {
                planetsCache = data.planets;
                planetAlert.style.display = "none";
                renderPlanetsList();
                loadFleetMotion();   // refresca naves en vuelo y re-renderiza inventario
                populateDefensePlanetSelect();
                populateFacilitiesPlanetSelect();
                populateLevelFix();
                populateBuildQueue();
                renderEmpireProduction();
                updatePlanetsSummary();
                updateDashKPIs();
            } else {
                planetsListContainer.innerHTML = `
                    <div class="text-center text-muted" style="padding: 40px 0;">
                        No se detectaron planetas en la caché local.<br>
                        Ejecuta el bot al menos una vez para raspar tu cuenta de OGame.
                    </div>
                `;
            }
        });
}

function loadFleetMotion() {
    fetch(api("/api/fleet_motion"))
        .then(res => res.json())
        .then(data => { fleetInMotionCache = data || {}; })
        .catch(() => { fleetInMotionCache = {}; })
        .finally(() => updateFleetInventory());
}

function forceRerenderPlanets() {
    if (planetsCache.length > 0) {
        planetsListContainer.innerHTML = "";
        renderPlanetsList();
    }
}

// --------------------------------------------------------------------------
// Corrección manual de niveles registrados + re-lectura forzada
// --------------------------------------------------------------------------
let fixLocations = [];
let lastFixLocSig = "";

function populateLevelFix() {
    const locSel = document.getElementById("fix_location");
    if (!locSel) return;
    const locs = [];
    planetsCache.forEach(p => {
        locs.push({ label: `🪐 ${p.name} [${p.coords}]`, coords: p.coords, is_moon: false, buildings: p.buildings || {} });
        if (p.moon && p.moon.coords) {
            locs.push({ label: `🌙 Luna [${p.moon.coords}]`, coords: p.moon.coords, is_moon: true, buildings: p.moon.buildings || {} });
        }
    });
    fixLocations = locs;
    const sig = locs.map(l => l.coords + (l.is_moon ? "m" : "p")).join(",");
    // Solo reconstruimos el desplegable si cambió el conjunto (no perder la selección).
    if (sig === lastFixLocSig && locSel.options.length) return;
    lastFixLocSig = sig;
    locSel.innerHTML = locs.map((l, i) => `<option value="${i}">${l.label}</option>`).join("");
    populateFixBuildings();
}

function populateFixBuildings() {
    const locSel = document.getElementById("fix_location");
    const bSel = document.getElementById("fix_building");
    if (!locSel || !bSel) return;
    const loc = fixLocations[locSel.value];
    const names = loc ? Object.keys(loc.buildings).sort() : [];
    bSel.innerHTML = names.length
        ? names.map(n => `<option value="${n}">${(BUILDING_TRANSLATIONS[n] || n)} (registrado: ${loc.buildings[n]})</option>`).join("")
        : `<option value="">(sin datos; ejecuta el bot un ciclo)</option>`;
    syncFixLevel();
}

function syncFixLevel() {
    const loc = fixLocations[document.getElementById("fix_location").value];
    const name = document.getElementById("fix_building").value;
    const lvlInput = document.getElementById("fix_level");
    if (loc && name && loc.buildings[name] !== undefined) lvlInput.value = loc.buildings[name];
}

function fixStatus(msg, ok) {
    const el = document.getElementById("fix_status");
    if (!el) return;
    el.textContent = msg;
    el.style.color = ok === undefined ? "" : (ok ? "var(--accent-success, #22c55e)" : "var(--accent-danger, #ef4444)");
}

function submitLevelFix() {
    const loc = fixLocations[document.getElementById("fix_location").value];
    const name = document.getElementById("fix_building").value;
    const level = parseInt(document.getElementById("fix_level").value);
    if (!loc || !name) { fixStatus("Selecciona planeta y edificio.", false); return; }
    if (isNaN(level) || level < 0) { fixStatus("Nivel inválido.", false); return; }
    fetch(api("/api/state_override"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "building", coords: loc.coords, is_moon: loc.is_moon, name, level })
    })
        .then(r => r.json())
        .then(d => {
            if (d.error) { fixStatus("Error: " + d.error, false); showToast("Error: " + d.error, "danger"); }
            else {
                fixStatus(`✅ Guardado. Se aplicará en el próximo ciclo del bot.`, true);
                showToast("Corrección de nivel guardada", "success");
            }
        })
        .catch(e => fixStatus("Error de red: " + e, false));
}

function postResync(body, okMsg) {
    fetch(api("/api/resync"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    })
        .then(r => r.json())
        .then(d => {
            if (d.error) { fixStatus("Error: " + d.error, false); showToast("Error: " + d.error, "danger"); }
            else { fixStatus(okMsg, true); showToast("Re-lectura solicitada", "success"); }
        })
        .catch(e => fixStatus("Error de red: " + e, false));
}

function forceResync() {
    const loc = fixLocations[document.getElementById("fix_location").value];
    if (!loc) { fixStatus("Selecciona un planeta o luna.", false); return; }
    postResync({ coords: loc.coords, is_moon: loc.is_moon },
        `🔄 El bot releerá ${loc.label} en el próximo ciclo.`);
}

function forceResyncAll() {
    postResync({ all: true },
        "🔄 El bot releerá TODOS los niveles del juego en el próximo ciclo.");
}

// --------------------------------------------------------------------------
// Cola de construcción por planeta (tipo Comandante)
// --------------------------------------------------------------------------
const QUEUE_BUILDINGS = ["metal_mine", "crystal_mine", "deut_synth", "solar_plant", "fusion_reactor",
    "metal_storage", "crystal_storage", "deut_tank", "robotics_factory", "shipyard",
    "research_lab", "nanite_factory"];
let queueDrafts = {};        // coords -> [{building, target_level}] (edición en memoria)
let queueLocations = [];     // solo planetas (las lunas no tienen cola de construcción)
let lastQueueLocSig = "";

function currentQueueCoords() {
    const sel = document.getElementById("queue_location");
    const loc = sel ? queueLocations[sel.value] : null;
    return loc ? loc.coords : null;
}

function getQueueDraft(coords) {
    if (!coords) return [];
    if (!queueDrafts[coords]) {
        const pcfg = (globalConfig.planets_config || {})[coords] || {};
        queueDrafts[coords] = Array.isArray(pcfg.build_queue) ? pcfg.build_queue.map(e => ({ ...e })) : [];
    }
    return queueDrafts[coords];
}

function populateBuildQueue() {
    const locSel = document.getElementById("queue_location");
    const bSel = document.getElementById("queue_building");
    if (!locSel || !bSel) return;
    queueLocations = fixLocations.filter(l => !l.is_moon);   // las lunas no tienen cola
    const sig = queueLocations.map(l => l.coords).join(",");
    if (sig !== lastQueueLocSig || !locSel.options.length) {
        lastQueueLocSig = sig;
        locSel.innerHTML = queueLocations.map((l, i) => `<option value="${i}">${l.label}</option>`).join("");
        bSel.innerHTML = QUEUE_BUILDINGS.map(n => `<option value="${n}">${BUILDING_TRANSLATIONS[n] || n}</option>`).join("");
    }
    renderQueueList();
}

function renderQueueList() {
    const listEl = document.getElementById("queue_list");
    if (!listEl) return;
    const loc = queueLocations[(document.getElementById("queue_location") || {}).value];
    const draft = getQueueDraft(loc ? loc.coords : null);
    if (!draft.length) {
        listEl.innerHTML = `<div class="text-muted" style="font-size:13px;">Cola vacía. Añade construcciones abajo.</div>`;
        return;
    }
    listEl.innerHTML = "";
    draft.forEach((e, i) => {
        const cur = loc && loc.buildings ? (loc.buildings[e.building] || 0) : null;
        const done = cur !== null && cur >= e.target_level;
        const row = document.createElement("div");
        row.className = "session-list-item";
        row.style.borderLeftColor = done ? "#22c55e" : "var(--accent-primary, #8a2be2)";
        const label = document.createElement("span");
        label.className = "session-item-name";
        label.textContent = `${i + 1}. ${BUILDING_TRANSLATIONS[e.building] || e.building} → nivel ${e.target_level}`
            + (cur !== null ? `  (actual ${cur})${done ? " ✓" : ""}` : "");
        const ctrls = document.createElement("span");
        ctrls.style.cssText = "display:flex; gap:4px;";
        const mk = (txt, fn) => {
            const b = document.createElement("button");
            b.type = "button"; b.className = "btn-secondary btn-sm"; b.textContent = txt; b.onclick = fn;
            return b;
        };
        ctrls.appendChild(mk("↑", () => moveQueue(i, -1)));
        ctrls.appendChild(mk("↓", () => moveQueue(i, 1)));
        ctrls.appendChild(mk("✕", () => removeQueue(i)));
        row.appendChild(label);
        row.appendChild(ctrls);
        listEl.appendChild(row);
    });
}

function moveQueue(i, d) {
    const draft = getQueueDraft(currentQueueCoords());
    const j = i + d;
    if (j < 0 || j >= draft.length) return;
    [draft[i], draft[j]] = [draft[j], draft[i]];
    renderQueueList();
}

function removeQueue(i) {
    getQueueDraft(currentQueueCoords()).splice(i, 1);
    renderQueueList();
}

function queueStatus(msg, ok) {
    const el = document.getElementById("queue_status");
    if (!el) return;
    el.textContent = msg;
    el.style.color = ok === undefined ? "" : (ok ? "var(--accent-success, #22c55e)" : "var(--accent-danger, #ef4444)");
}

function addToQueue() {
    const coords = currentQueueCoords();
    if (!coords) { queueStatus("Selecciona un planeta.", false); return; }
    const building = document.getElementById("queue_building").value;
    const target = parseInt(document.getElementById("queue_target").value);
    if (!building || isNaN(target) || target < 1) { queueStatus("Indica edificio y nivel válido.", false); return; }
    getQueueDraft(coords).push({ building, target_level: target });
    renderQueueList();
    queueStatus("Añadido. Pulsa «Guardar cola» para aplicarlo.", true);
}

function saveBuildQueue() {
    const coords = currentQueueCoords();
    if (!coords) { queueStatus("Selecciona un planeta.", false); return; }
    const queue = getQueueDraft(coords);
    fetch(api("/api/build_queue"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ coords, queue })
    })
        .then(r => r.json())
        .then(d => {
            if (d.error) { queueStatus("Error: " + d.error, false); showToast("Error: " + d.error, "danger"); }
            else {
                // Reflejar en memoria para que «Guardar cambios» no revierta la cola.
                if (!globalConfig.planets_config) globalConfig.planets_config = {};
                if (!globalConfig.planets_config[coords]) globalConfig.planets_config[coords] = {};
                globalConfig.planets_config[coords].build_queue = queue.map(e => ({ ...e }));
                if (!localPlanetsConfig[coords]) localPlanetsConfig[coords] = {};
                localPlanetsConfig[coords].build_queue = queue.map(e => ({ ...e }));
                queueStatus("✅ Cola guardada. El bot la seguirá en el próximo ciclo.", true);
                showToast("Cola de construcción guardada", "success");
            }
        })
        .catch(e => queueStatus("Error de red: " + e, false));
}

// Selector de origen (planeta/luna/ambos) para una función. Solo tiene sentido si la
// ubicación tiene luna; planeta y luna comparten coords, así que decide desde cuál se lanza.
function originSelectHTML(cls, val) {
    const v = val || "both";
    const opt = (k, t) => `<option value="${k}" ${v === k ? "selected" : ""}>${t}</option>`;
    return `<select class="${cls}" title="¿Desde dónde se lanza?" `
        + `style="font-size:11px;width:100%;">`
        + opt("both", "🪐+🌙 Ambos") + opt("planet", "🪐 Planeta") + opt("moon", "🌙 Luna")
        + `</select>`;
}

// Casilla de una función con (opcional) selector de origen debajo, en la MISMA celda de la
// rejilla, para que el selector quede alineado bajo su función. Sin luna -> solo la casilla.
function toggleWithOrigin(cls, label, checked, hasMoon, fromCls, fromVal) {
    const lbl = `<label class="planet-toggle"><input type="checkbox" class="${cls}" `
        + `${checked ? "checked" : ""}><span>${label}</span></label>`;
    if (!hasMoon) return lbl;
    return `<div class="planet-toggle-group">${lbl}${originSelectHTML(fromCls, fromVal)}</div>`;
}

function renderPlanetsList() {
    if (!configLoaded) return;

    const existingCards = document.querySelectorAll(".planet-card");
    if (existingCards.length === planetsCache.length) {
        return;
    }

    planetsListContainer.innerHTML = "";
    
    planetsCache.forEach(p => {
        const coords = p.coords;
        const config = localPlanetsConfig[coords] || {};
        const isEconomy = config.enable_economy !== undefined ? config.enable_economy : globalConfig.enable_economy !== false;
        const isDefense = config.enable_defense !== undefined ? config.enable_defense : globalConfig.enable_defense !== false;
        const isFacilities = config.enable_facilities !== undefined ? config.enable_facilities : globalConfig.enable_facilities !== false;
        const isLifeforms = config.enable_lifeforms !== undefined ? config.enable_lifeforms : globalConfig.enable_lifeforms !== false;
        const isExpeditions = config.enable_expeditions !== undefined ? config.enable_expeditions : globalConfig.enable_expeditions !== false;
        const isFleet = config.enable_fleet_building !== undefined ? config.enable_fleet_building : globalConfig.enable_fleet_building !== false;
        const isFarming = config.enable_farming !== undefined ? config.enable_farming : globalConfig.enable_farming !== false;
        const isRecycling = config.enable_recycling !== undefined ? config.enable_recycling : globalConfig.enable_recycling !== false;
        const isNightSweep = config.enable_night_sweep === true;  // opt-in por planeta
        const isFeedTarget = config.feed_target === true;         // recibe recursos para construir
        const isFeedSource = config.feed_source === true;         // cede su excedente
        // Selector de origen por función (solo si la ubicación tiene luna).
        const hasMoon = !!(p.has_moon || (p.moon && p.moon.coords));

        const card = document.createElement("div");
        card.className = "planet-card collapsed";   // config por tarjeta plegada por defecto
        card.dataset.coords = coords;

        // Chip de niveles M·C·D·E desde los edificios registrados
        const b = p.buildings || {};
        const levelsChip = `M${b.metal_mine || 0}·C${b.crystal_mine || 0}·D${b.deut_synth || 0}·E${b.solar_plant || 0}`;
        // Recursos por planeta: /api/planets no los trae hoy; si algún día llegan
        // (p.resources), barra relativa al máximo entre planetas (sin "/ max").
        let resHTML = "";
        const res = p.resources;
        if (res && typeof res.metal === "number") {
            const maxRes = Math.max(1, ...planetsCache.map(q => {
                const r = q.resources || {};
                return Math.max(r.metal || 0, r.crystal || 0, r.deut || 0);
            }));
            const bar = (val, cls) => `
                <div class="planet-res-row mono sm" style="display:flex;align-items:center;gap:8px;">
                    <span class="dim" style="width:14px;">${cls[0].toUpperCase()}</span>
                    <div class="meter" style="flex:1;"><div class="meter-fill cyan" style="width:${Math.round((val || 0) / maxRes * 100)}%"></div></div>
                    <span style="min-width:64px;text-align:right;">${formatNumber(val || 0)}</span>
                </div>`;
            resHTML = `<div class="planet-res">${bar(res.metal, "metal")}${bar(res.crystal, "cristal")}${bar(res.deut, "deuterio")}</div>`;
        }

        card.innerHTML = `
            <div class="planet-card-header">
                <div class="planet-name-wrapper">
                    <span class="planet-icon">🪐</span>
                    <span class="planet-title">${p.name}</span>
                    <span class="planet-coords-tag">[${coords}]</span>
                    ${hasMoon ? '<span class="planet-icon" title="Tiene luna">🌙</span>' : ""}
                </div>
                <div class="topbar-spacer"></div>
                <span class="mono dim sm" title="Niveles: mina metal · mina cristal · sint. deuterio · planta solar">${levelsChip}</span>
                <button type="button" class="btn btn-secondary btn-sm planet-expand" title="Configurar módulos de este planeta">⚙</button>
            </div>
            ${resHTML}
            <div class="planet-queue-line mono dim sm" data-coords="${coords}">⏳ —</div>
            <div class="planet-card-body">
                <label class="planet-toggle">
                    <input type="checkbox" class="planet-economy" ${isEconomy ? "checked" : ""}>
                    <span>Economía</span>
                </label>
                <label class="planet-toggle">
                    <input type="checkbox" class="planet-defense" ${isDefense ? "checked" : ""}>
                    <span>Defensa</span>
                </label>
                <label class="planet-toggle">
                    <input type="checkbox" class="planet-facilities" ${isFacilities ? "checked" : ""}>
                    <span>Instalaciones</span>
                </label>
                <label class="planet-toggle">
                    <input type="checkbox" class="planet-lifeforms" ${isLifeforms ? "checked" : ""}>
                    <span>Lifeforms</span>
                </label>
                ${toggleWithOrigin("planet-expeditions", "Expediciones", isExpeditions, hasMoon, "planet-expeditions-from", config.expeditions_from)}
                <label class="planet-toggle">
                    <input type="checkbox" class="planet-fleet-building" ${isFleet ? "checked" : ""}>
                    <span>Crear Flota</span>
                </label>
                ${toggleWithOrigin("planet-farming", "Farmeo", isFarming, hasMoon, "planet-farming-from", config.farming_from)}
                ${toggleWithOrigin("planet-recycling", "Reciclaje", isRecycling, hasMoon, "planet-recycling-from", config.recycling_from)}
                ${toggleWithOrigin("planet-night-sweep", "Barrido nocturno", isNightSweep, hasMoon, "planet-night-sweep-from", config.night_sweep_from)}
                <label class="planet-toggle" title="Recibe recursos de los planetas-fuente para pagar sus objetivos de construcción (p.ej. lab a 12).">
                    <input type="checkbox" class="planet-feed-target" ${isFeedTarget ? "checked" : ""}>
                    <span>Recibe recursos</span>
                </label>
                <label class="planet-toggle" title="Cede su excedente (dejando el buffer) a los planetas marcados como 'Recibe recursos'.">
                    <input type="checkbox" class="planet-feed-source" ${isFeedSource ? "checked" : ""}>
                    <span>Cede recursos</span>
                </label>
            </div>
        `;

        const expander = card.querySelector(".planet-expand");
        if (expander) expander.addEventListener("click", () => card.classList.toggle("collapsed"));
        planetsListContainer.appendChild(card);
    });
    updatePlanetQueueLines();
    // Las tarjetas se regeneran con los valores derivados de la config: rebasar la línea
    // base de planets_config para que el propio render no cuente como cambio sin guardar.
    if (uiBaseline) {
        collectUIIntoConfig();
        uiBaseline.planets_config = JSON.parse(JSON.stringify(globalConfig.planets_config || {}));
        updateDirtyBadge();
    }
}

// --------------------------------------------------------------------------
// Estado del Bot (En ejecución / Detenido)
// --------------------------------------------------------------------------
function checkBotStatus() {
    fetch(api("/api/status"))
        .then(res => res.json())
        .then(data => {
            if (data.running) {
                statusDot.className = "status-dot active";
                // Si el bot está en pausa remota (C1/C6), la píldora lo refleja
                const paused = botStatusCache && (botStatusCache.paused_until || 0) * 1000 > Date.now();
                statusText.innerText = paused
                    ? "EN PAUSA hasta " + new Date(botStatusCache.paused_until * 1000).toLocaleTimeString().slice(0, 5)
                    : "EN MARCHA";
                btnStart.disabled = true;
                btnStop.disabled = false;
            } else {
                statusDot.className = "status-dot stopped";
                statusText.innerText = "Detenido";
                btnStart.disabled = false;
                btnStop.disabled = true;
            }
        })
        .catch(err => {
            statusDot.className = "status-dot stopped";
            statusText.innerText = "Desconectado";
        });
}

// --------------------------------------------------------------------------
// Lectura de Logs en Vivo
// --------------------------------------------------------------------------
let lastLogsLength = 0;
let rawLogsCache = [];

function loadLogs() {
    fetch(api("/api/logs"))
        .then(res => res.json())
        .then(data => {
            const logs = data.logs || [];
            if (logs.length === 0) return;
            
            // Si el tamaño de los logs es el mismo, no repintamos
            if (logs.length === lastLogsLength && rawLogsCache[logs.length-1] === logs[logs.length-1]) {
                return;
            }
            
            lastLogsLength = logs.length;
            rawLogsCache = logs;
            
            renderLogs(logs);
        });
}

// Etiqueta de módulo por palabras clave (heurística; "otros" si no casa)
const LOG_MODULES = ["farmeo", "expediciones", "reciclaje", "fleetsave", "ataques",
    "construcción", "investigación", "telegram", "sesión", "otros"];

function detectLogModule(line) {
    const l = line.toLowerCase();
    if (l.includes("expedic")) return "expediciones";
    if (l.includes("recicl") || l.includes("escombro")) return "reciclaje";
    if (l.includes("fleetsave") || l.includes("salvar la flota") || l.includes("evasión")) return "fleetsave";
    if (l.includes("farm") || l.includes("granja") || l.includes("saqueo") || l.includes("inactivo")) return "farmeo";
    if (l.includes("ataque") || l.includes("hostil") || l.includes("espion") || l.includes("sonde")) return "ataques";
    if (l.includes("investig")) return "investigación";
    if (l.includes("construc") || l.includes("edificio") || l.includes("mina") || l.includes("instalaci")) return "construcción";
    if (l.includes("telegram")) return "telegram";
    if (l.includes("sesión") || l.includes("sesion") || l.includes("login") || l.includes("navegador") || l.includes("ciclo")) return "sesión";
    return "otros";
}

function parseLogLine(line) {
    let level = "info";
    if (line.includes("ERROR") || line.includes("Exception") || line.includes("fallido")) level = "error";
    else if (line.includes("WARNING") || line.includes("omitido")) level = "warn";
    else if (/✅|correctamente|enviad[ao]|completad|guardad|éxito/i.test(line)) level = "ok";
    const time = (line.match(/\b(\d{2}:\d{2}:\d{2})\b/) || [])[1] || "";
    return { time, level, module: detectLogModule(line), text: line.trim() };
}

// Filtros activos del registro
let logLevelFilter = "all";

function logMatchesFilters(entry) {
    if (logLevelFilter !== "all" && entry.level !== logLevelFilter) return false;
    const modSel = document.getElementById("logModuleFilter");
    if (modSel && modSel.value && entry.module !== modSel.value) return false;
    const q = (document.getElementById("logSearch") || {}).value || "";
    if (q && !entry.text.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
}

function renderLogs(lines) {
    parsedLogsCache = lines.map(parseLogLine);
    applyLogFilters();
    renderLiveFeed();
}

function applyLogFilters() {
    terminalConsole.innerHTML = "";
    let shown = 0;

    parsedLogsCache.forEach(entry => {
        if (!logMatchesFilters(entry)) return;
        shown++;
        const line = entry.text;
        const div = document.createElement("div");
        div.className = "log-line";

        // Formateo y Colores (mismas reglas de siempre)
        if (entry.level === "error") {
            div.classList.add("log-error");
        } else if (entry.level === "warn") {
            div.classList.add("log-warn");
        } else if (line.includes("[DRY-RUN]")) {
            div.classList.add("log-dryrun");
        } else if (line.includes("[ACCION]") || line.includes("Ataque desde") || line.includes("Fleetsave") || entry.level === "ok") {
            div.classList.add("log-action");
        } else if (line.includes("INFO") || line.includes("--- Nuevo ciclo ---")) {
            div.classList.add("log-info");
        }

        div.textContent = line;
        terminalConsole.appendChild(div);
    });

    const stats = document.getElementById("logStats");
    if (stats) stats.textContent = `${shown}/${parsedLogsCache.length} líneas`;

    // Auto-scroll
    if (autoScrollCheck.checked) {
        terminalConsole.scrollTop = terminalConsole.scrollHeight;
    }
}

// Feed "Acciones en vivo" (Bot en directo): últimas ~15 líneas del log
function renderLiveFeed() {
    const feed = document.getElementById("liveActionsFeed");
    if (!feed) return;
    const entries = parsedLogsCache.slice(-15);
    if (!entries.length) {
        feed.innerHTML = '<div class="text-muted empty-note">Sin acciones registradas todavía.</div>';
        return;
    }
    feed.innerHTML = "";
    entries.forEach(e => {
        const row = document.createElement("div");
        row.className = "feed-row";
        const t = document.createElement("span");
        t.className = "feed-time";
        t.textContent = e.time || "--:--:--";
        const k = document.createElement("span");
        k.className = "feed-kind" + (e.level === "ok" ? " ok" : (e.level === "warn" || e.level === "error") ? " wait" : "");
        k.textContent = e.level === "ok" ? "OK" : e.level.toUpperCase().slice(0, 4);
        const x = document.createElement("span");
        x.className = "feed-text";
        // Quitar prefijos de fecha/nivel para dejar solo el mensaje
        x.textContent = e.text.replace(/^[\d\-:,\s]*\[?(INFO|WARNING|ERROR|DEBUG)\]?\s*/i, "").slice(0, 160);
        row.appendChild(t); row.appendChild(k); row.appendChild(x);
        feed.appendChild(row);
    });
    feed.scrollTop = feed.scrollHeight;
    const meta = document.getElementById("liveActionsMeta");
    if (meta) meta.textContent = "actualizado " + new Date().toLocaleTimeString();
}

// Exportación CSV del registro (client-side, respeta los filtros activos)
function exportLogCSV() {
    const rows = [["hora", "nivel", "modulo", "mensaje"]];
    parsedLogsCache.forEach(e => {
        if (logMatchesFilters(e)) rows.push([e.time, e.level, e.module, e.text]);
    });
    if (rows.length === 1) { showToast("No hay líneas que exportar", "warning"); return; }
    const csv = "﻿" + rows.map(r =>
        r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(",")
    ).join("\r\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `ogbot_log_${currentAccount || "cuenta"}_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

// --------------------------------------------------------------------------
// Helpers de UI
// --------------------------------------------------------------------------
function setVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = val !== undefined ? val : "";
}

function getVal(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : "";
}

// Dicts "clave: valor, clave: valor" y listas CSV (campos avanzados del config.yaml)
function fmtPairs(obj) {
    return Object.entries(obj || {}).map(([k, v]) => `${k}: ${v}`).join(", ");
}
function parsePairs(str) {
    const out = {};
    (str || "").split(",").forEach(part => {
        const i = part.indexOf(":");
        if (i < 0) return;
        const k = part.slice(0, i).trim();
        const v = parseFloat(part.slice(i + 1));
        if (k && !isNaN(v)) out[k] = v;
    });
    return out;
}
function fmtList(arr) { return (arr || []).join(", "); }
function parseIntList(str) {
    return (str || "").split(",").map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
}

function setCheck(id, val) {
    const el = document.getElementById(id);
    if (el) el.checked = !!val;
}

function getCheck(id) {
    const el = document.getElementById(id);
    return el ? el.checked : false;
}

let toastTimer = null;
function showToast(message, type) {
    saveStatus.innerText = message;
    saveStatus.className = "save-status show";

    if (type === "success") {
        saveStatus.style.color = "var(--accent-success)";
    } else if (type === "danger") {
        saveStatus.style.color = "var(--accent-danger)";
    } else {
        saveStatus.style.color = "var(--accent-secondary)";
    }

    // Cancelar el timer del toast anterior: que un éxito previo no tape un error nuevo
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
        saveStatus.classList.remove("show");
    }, 4000);
}

// --------------------------------------------------------------------------
// Nombres legibles para tecnologías de OGame
// --------------------------------------------------------------------------
const TECH_NAMES = {
    "astrophysics": "Astrofísica (Astrophysics)",
    "plasma_tech": "Tecnología de Plasma (Plasma Tech)",
    "computer_tech": "Tecnología de Computación (Computer Tech)",
    "combustion_drive": "Motor de Combustión (Combustion Drive)",
    "impulse_drive": "Motor de Impulso (Impulse Drive)",
    "hyperspace_drive": "Propulsor Hiperespacial (Hyperspace Drive)",
    "espionage_tech": "Tecnología de Espionaje (Espionage Tech)",
    "weapons_tech": "Tecnología de Armas (Weapons Tech)",
    "shielding_tech": "Tecnología de Escudo (Shielding Tech)",
    "armor_tech": "Tecnología de Blindaje (Armor Tech)",
    "hyperspace_tech": "Tecnología de Hiperespacio (Hyperspace Tech)",
    "energy_tech": "Tecnología de Energía (Energy Tech)",
    "laser_tech": "Tecnología Láser (Laser Tech)",
    "ion_tech": "Tecnología Iónica (Ion Tech)"
};

function renderResearchPriorityList() {
    const container = document.getElementById("researchPriorityList");
    if (!container) return;

    container.innerHTML = "";
    researchPriorityList.forEach((tech, index) => {
        const readableName = TECH_NAMES[tech] || tech;
        const div = document.createElement("div");
        div.className = "priority-item";
        
        div.innerHTML = `
            <div class="priority-name">
                <span class="priority-number">${index + 1}</span>
                <span>${readableName}</span>
            </div>
            <div class="priority-controls">
                <button class="btn-move btn-up" ${index === 0 ? "disabled" : ""} onclick="movePriority(${index}, -1)">▲</button>
                <button class="btn-move btn-down" ${index === researchPriorityList.length - 1 ? "disabled" : ""} onclick="movePriority(${index}, 1)">▼</button>
            </div>
        `;
        container.appendChild(div);
    });
}

window.movePriority = function(index, direction) {
    const targetIndex = index + direction;
    if (targetIndex < 0 || targetIndex >= researchPriorityList.length) return;

    // Intercambiar elementos
    const temp = researchPriorityList[index];
    researchPriorityList[index] = researchPriorityList[targetIndex];
    researchPriorityList[targetIndex] = temp;

    renderResearchPriorityList();
};

// --------------------------------------------------------------------------
// Carga y Renderizado de Estadísticas
// --------------------------------------------------------------------------
function loadStats() {
    fetch(api("/api/stats"))
        .then(res => res.json())
        .then(data => {
            statsCache = data || {};
            const farm = data.total_farming || { metal: 0, crystal: 0, deut: 0 };
            const rec = data.total_recycling || { metal: 0, crystal: 0, deut: 0 };
            const exp = data.total_expeditions || { metal: 0, crystal: 0, deut: 0, dark_matter: 0, ships_found: {} };

            // Calcular totales
            const totalMetal = (farm.metal || 0) + (rec.metal || 0) + (exp.metal || 0);
            const totalCrystal = (farm.crystal || 0) + (rec.crystal || 0) + (exp.crystal || 0);
            const totalDeut = (farm.deut || 0) + (rec.deut || 0) + (exp.deut || 0);

            // Poblar elementos DOM
            setText("stats_total_metal", formatNumber(totalMetal));
            setText("stats_total_crystal", formatNumber(totalCrystal));
            setText("stats_total_deut", formatNumber(totalDeut));

            setText("stats_farm_metal", formatNumber(farm.metal || 0));
            setText("stats_farm_crystal", formatNumber(farm.crystal || 0));
            setText("stats_farm_deut", formatNumber(farm.deut || 0));

            setText("stats_recycle_metal", formatNumber(rec.metal || 0));
            setText("stats_recycle_crystal", formatNumber(rec.crystal || 0));
            setText("stats_recycle_deut", formatNumber(rec.deut || 0));

            setText("stats_exp_metal", formatNumber(exp.metal || 0));
            setText("stats_exp_crystal", formatNumber(exp.crystal || 0));
            setText("stats_exp_deut", formatNumber(exp.deut || 0));
            setText("stats_exp_dm", formatNumber(exp.dark_matter || 0));

            // Renderizar naves encontradas
            renderShipsFound(exp.ships_found || {});

            // Renderizar acciones de sesión
            renderSessionActions(data.session_actions || null);

            // Dashboard: desglose de expediciones (C8) y KPIs
            renderExpOutcomes();
            updateDashKPIs();
        })
        .catch(err => console.error("Error al cargar estadísticas:", err));
}

// --------------------------------------------------------------------------
// Pestaña de vuelos (flotas en movimiento)
// --------------------------------------------------------------------------
let flightsCache = [];
let lastFlightsSig = "";
let flightsServerOffset = 0;   // segundos que el reloj del bot va por delante del navegador
const recalledFlights = new Set();   // vuelos cuyo regreso ya se pidió (para no reenviar)
function flightKey(fl) {
    return `${fl.mission_code}|${fl.origin}>${fl.destination}|${fl.arrival_epoch || 0}`;
}
const MISSION_COLORS = {
    "Ataque": "#ef4444", "Ataque (ACS)": "#ef4444", "Destruir luna": "#ef4444",
    "Transporte": "#38bdf8", "Despliegue": "#22c55e", "Defensa (ACS)": "#22c55e",
    "Espionaje": "#a78bfa", "Colonización": "#22c55e", "Reciclaje": "#fbbf24",
    "Expedición": "#f472b6"
};

function loadFlights() {
    fetch(api("/api/flights"))
        .then(res => res.json())
        .then(data => {
            const flights = data.flights || [];
            // Desfase ESTABLE entre el reloj del servidor y el del navegador, usando la hora
            // ACTUAL del servidor (server_now). No usar 'updated' (hora de escritura): al
            // quedar fija entre escrituras hacía que la cuenta atrás diera saltos.
            flightsServerOffset = data.server_now ? (data.server_now - Date.now() / 1000) : 0;
            // Firma por contenido VISIBLE de cada vuelo (no por 'updated', que cambia siempre):
            // repintamos cuando entra/sale un vuelo O cuando uno gana su hora de vuelta a casa,
            // su estimación de regreso, naves o carga (datos que llegan en una lectura posterior).
            const cargoSum = c => c ? ((c.metal || 0) + (c.crystal || 0) + (c.deut || 0)) : 0;
            const sig = flights.map(f =>
                `${f.mission_code}|${f.origin}>${f.destination}|${f.is_return ? "r" : "o"}` +
                `|${f.arrival_epoch || 0}|${f.departure_epoch || 0}|${f.return_arrival_epoch || 0}` +
                `|${f.departure_estimated ? 1 : 0}|${Object.keys(f.ships || {}).length}|${cargoSum(f.cargo)}`
            ).sort().join(";");
            if (sig === lastFlightsSig) return;   // sin cambios visibles: no repintamos
            lastFlightsSig = sig;
            flightsCache = flights;
            renderFlights();
            const upd = document.getElementById("flights_updated");
            if (upd) upd.textContent = data.updated ? ("Actualizado " + new Date(data.updated * 1000).toLocaleTimeString()) : "";
        })
        .catch(() => {});
}

function flightLocIcon(type) {
    return type === "moon" ? "🌙" : (type === "debris" ? "💥" : "🪐");
}

function flightCargoText(cargo) {
    if (!cargo) return "";
    const parts = [];
    if (cargo.metal) parts.push("M " + formatNumber(cargo.metal));
    if (cargo.crystal) parts.push("C " + formatNumber(cargo.crystal));
    if (cargo.deut) parts.push("D " + formatNumber(cargo.deut));
    return parts.join(" · ");
}

// Hora de un epoch; añade el día (dd/mm) si no es hoy, para que se vea cuándo llega.
function flightWhen(ep) {
    if (!ep) return "—";
    const d = new Date(ep * 1000);
    const t = d.toLocaleTimeString();
    if (d.toDateString() === new Date().toDateString()) return t;
    return d.toLocaleDateString(undefined, { day: "2-digit", month: "2-digit" }) + " " + t;
}

function flightMissionColor(fl) {
    return fl.is_hostile ? "#ef4444" : (MISSION_COLORS[fl.mission] || "var(--accent-primary, #8a2be2)");
}

function ensureFlightsTableStyle() {
    if (document.getElementById("flights-table-style")) return;
    const st = document.createElement("style");
    st.id = "flights-table-style";
    st.textContent = `
      .flights-wrap { overflow-x:auto; }
      .flights-table { width:100%; border-collapse:collapse; font-size:12px; }
      .flights-table th { text-align:left; padding:6px 8px; color:var(--text-muted,#8b949e);
          font-weight:600; border-bottom:1px solid var(--border,#30363d); white-space:nowrap; }
      .flights-table td { padding:6px 8px; vertical-align:middle; white-space:nowrap; }
      .flights-table tr.flight-main td { border-top:1px solid var(--border,#21262d); }
      .flights-table .fl-dot { display:inline-block; width:8px; height:8px; border-radius:50%;
          margin-right:6px; vertical-align:middle; }
      .flights-table .flight-eta { font-variant-numeric:tabular-nums; }
      .flights-table td.fl-detail { padding-top:0; white-space:normal;
          color:var(--text-secondary,#9aa); border-top:none; }
      .flights-table .fl-chip { display:inline-block; background:var(--bg-tertiary,#21262d);
          border-radius:6px; padding:1px 6px; margin:2px 4px 2px 0; font-size:11px; }
    `;
    document.head.appendChild(st);
}

function renderFlights() {
    const listEl = document.getElementById("flights_list");
    if (!listEl) return;
    const countEl = document.getElementById("flights_count");
    const flights = flightsCache.slice().sort((a, b) => {
        const ka = a.arrival_epoch || Infinity, kb = b.arrival_epoch || Infinity;
        return ka === kb ? 0 : ka - kb;   // evita Infinity-Infinity = NaN
    });
    if (countEl) countEl.innerText = flights.length;

    if (!flights.length) {
        listEl.innerHTML = `<div class="text-muted" style="font-size:13px; padding:4px;">No hay vuelos en curso.</div>`;
        return;
    }

    ensureFlightsTableStyle();
    listEl.innerHTML = "";
    const wrap = document.createElement("div");
    wrap.className = "flights-wrap";
    const table = document.createElement("table");
    table.className = "flights-table";
    table.innerHTML = `<thead><tr>
        <th>Misión</th><th>Ruta</th><th>Llega en</th><th>Llegada</th>
        <th>Vuelve a casa</th><th>Acción</th></tr></thead>`;
    const tbody = document.createElement("tbody");

    flights.forEach(fl => {
        const tr = document.createElement("tr");
        tr.className = "flight-main";

        const tdM = document.createElement("td");
        tdM.innerHTML = `<span class="fl-dot" style="background:${flightMissionColor(fl)}"></span>`;
        tdM.appendChild(document.createTextNode(
            (fl.is_hostile ? "⚠️ " : "") + (fl.mission || "?") + (fl.is_return ? " (vuelta)" : "")));
        tr.appendChild(tdM);

        const tdR = document.createElement("td");
        tdR.textContent = `${flightLocIcon(fl.origin_type)} [${fl.origin || "?"}] → ${flightLocIcon(fl.dest_type)} [${fl.destination || "?"}]`;
        tr.appendChild(tdR);

        const tdE = document.createElement("td");
        const eta = document.createElement("span");
        eta.className = "flight-eta";
        if (fl.arrival_epoch) eta.dataset.arrival = fl.arrival_epoch;
        eta.textContent = fl.arrival_text || "—";
        tdE.appendChild(eta);
        tr.appendChild(tdE);

        const tdA = document.createElement("td");
        tdA.textContent = flightWhen(fl.arrival_epoch);
        tr.appendChild(tdA);

        // Retorno natural del viaje de ida y vuelta (pata de vuelta agrupada).
        const tdH = document.createElement("td");
        tdH.textContent = fl.return_arrival_epoch ? "↩ " + flightWhen(fl.return_arrival_epoch) : "—";
        tr.appendChild(tdH);

        // Acción: Regresar + estimación de la hora de vuelta si se recupera AHORA.
        // Las expediciones también se pueden retornar durante la IDA (no durante la
        // exploración: arrival_epoch pasado deshabilita el botón como al resto).
        const tdAct = document.createElement("td");
        const stillFlying = !fl.arrival_epoch || fl.arrival_epoch > (Date.now() / 1000 + flightsServerOffset);
        if (!fl.is_return && stillFlying) {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "btn-secondary btn-sm flight-recall-btn";
            if (fl.arrival_epoch) btn.dataset.arrival = fl.arrival_epoch;
            btn.title = "El bot recuperará esta flota en cuanto vea la petición.";
            if (recalledFlights.has(flightKey(fl))) {
                btn.disabled = true;
                btn.textContent = "↩ Pedido";
            } else {
                btn.textContent = "↩ Regresar";
                btn.onclick = () => requestRecall(fl, btn);
            }
            tdAct.appendChild(btn);
            if (fl.departure_epoch > 0) {
                const ret = document.createElement("div");
                ret.className = "flight-return text-muted";
                ret.style.cssText = "font-size:11px; margin-top:3px;";
                ret.dataset.departure = fl.departure_epoch;
                if (fl.arrival_epoch) ret.dataset.arrival = fl.arrival_epoch;
                if (fl.departure_estimated) ret.dataset.est = "1";
                ret.textContent = "≈ —";
                tdAct.appendChild(ret);
            }
        } else {
            tdAct.textContent = "—";
        }
        tr.appendChild(tdAct);
        tbody.appendChild(tr);

        // Sub-fila de detalle a todo el ancho: carga y naves.
        const cargoTxt = flightCargoText(fl.cargo);
        const shipNames = Object.keys(fl.ships || {});
        if (cargoTxt || shipNames.length) {
            const trd = document.createElement("tr");
            const td = document.createElement("td");
            td.className = "fl-detail";
            td.colSpan = 6;
            if (cargoTxt) {
                const c = document.createElement("span");
                c.style.marginRight = "10px";
                c.textContent = "📦 " + cargoTxt;
                td.appendChild(c);
            }
            shipNames.forEach(n => {
                const chip = document.createElement("span");
                chip.className = "fl-chip";
                chip.textContent = `${n}: ${formatNumber(fl.ships[n])}`;
                td.appendChild(chip);
            });
            trd.appendChild(td);
            tbody.appendChild(trd);
        }
    });

    table.appendChild(tbody);
    wrap.appendChild(table);
    listEl.appendChild(wrap);
    updateFlightTimers();
}

function requestRecall(fl, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "↩ Enviando…"; }
    fetch(api("/api/recall"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            origin: fl.origin, destination: fl.destination,
            mission_code: fl.mission_code, arrival: fl.arrival_epoch || 0
        })
    })
        .then(r => r.json())
        .then(d => {
            if (d.error) {
                showToast("Error: " + d.error, "danger");
                if (btn) { btn.disabled = false; btn.textContent = "↩ Regresar"; }
            } else {
                recalledFlights.add(flightKey(fl));   // recordar para no reenviar al repintar
                showToast("Regreso solicitado: el bot recuperará la flota en breve.", "success");
                if (btn) btn.textContent = "↩ Regreso pedido";
            }
        })
        .catch(e => {
            showToast("Error de red: " + e, "danger");
            if (btn) { btn.disabled = false; btn.textContent = "↩ Regresar"; }
        });
}

function updateFlightTimers() {
    const nowServer = Date.now() / 1000 + flightsServerOffset;   // reloj del navegador ajustado al del bot
    document.querySelectorAll("#flights_list .flight-eta").forEach(el => {
        const arrival = parseInt(el.dataset.arrival || "0");
        if (!arrival) return;
        const rem = Math.max(0, Math.round(arrival - nowServer));
        const pad = n => String(n).padStart(2, "0");
        const days = Math.floor(rem / 86400);
        const hms = `${pad(Math.floor((rem % 86400) / 3600))}:${pad(Math.floor((rem % 3600) / 60))}:${pad(rem % 60)}`;
        el.textContent = rem <= 0 ? "Llegó" : (days > 0 ? `${days}d ${hms}` : hms);
    });
    // Hora a la que volvería la flota si se recupera AHORA (avanza 2 s por segundo real).
    document.querySelectorAll("#flights_list .flight-return").forEach(el => {
        const dep = parseInt(el.dataset.departure || "0");
        if (!dep) return;
        const arr = parseInt(el.dataset.arrival || "0");
        if (arr && arr < nowServer) { el.textContent = ""; return; }   // ya llegó: recall no aplica
        if (dep >= nowServer) { el.textContent = ""; return; }          // salida no en el pasado: aún no aplica
        const returnAt = 2 * nowServer - dep;
        el.textContent = "si vuelve ahora " + (el.dataset.est ? "≈ " : "") + flightWhen(returnAt);
        el.title = el.dataset.est
            ? "Hora estimada de vuelta si la regresas ahora (estimada por el tiempo ya volado desde que salió)."
            : "Hora exacta de vuelta si la regresas ahora (según el dato de regreso de OGame).";
    });
    // Una flota que aterriza estando en pantalla: desactivar su botón de Regresar (la cuenta
    // atrás ya marca "Llegó"); evita pedir el regreso de algo que ya llegó.
    document.querySelectorAll("#flights_list .flight-recall-btn").forEach(el => {
        const arr = parseInt(el.dataset.arrival || "0");
        if (arr && arr < nowServer && !el.disabled) { el.disabled = true; el.textContent = "Llegó"; }
    });
}

// --------------------------------------------------------------------------
// Visor de mensajes leídos por el bot
// --------------------------------------------------------------------------
let messagesCache = [];
let lastMessagesSig = "";   // evita repintar (y resetear el scroll) si no hay mensajes nuevos
const MESSAGE_CAT_COLORS = {
    "Combate": "#ef4444", "Expedición": "#a78bfa",
    "Reciclaje": "#fbbf24", "Espionaje": "#38bdf8"
};

function loadMessages() {
    fetch(api("/api/messages"))
        .then(res => res.json())
        .then(data => {
            const msgs = data.messages || [];
            const sig = msgs.length + "|" + (msgs.length ? msgs[msgs.length - 1].key : "");
            if (sig === lastMessagesSig) return;   // sin cambios: no repintamos, así no se pierde el scroll
            lastMessagesSig = sig;
            messagesCache = msgs;
            renderMessages();
        })
        .catch(() => {});
}

function renderMessages() {
    const listEl = document.getElementById("messages_list");
    if (!listEl) return;
    const countEl = document.getElementById("messages_count");
    const filterEl = document.getElementById("messages_filter");
    const filter = filterEl ? filterEl.value : "";

    let msgs = messagesCache.slice().reverse(); // el más reciente arriba
    if (filter) msgs = msgs.filter(m => m.category === filter);
    if (countEl) countEl.innerText = msgs.length;

    if (!msgs.length) {
        listEl.innerHTML = `<div class="text-muted" style="font-size:13px; padding:4px;">No hay mensajes${filter ? " de esa categoría" : ""} todavía.</div>`;
        return;
    }

    listEl.innerHTML = "";
    msgs.forEach(m => {
        const item = document.createElement("div");
        item.className = "session-list-item";
        item.style.flexDirection = "column";
        item.style.alignItems = "stretch";
        item.style.borderLeftColor = MESSAGE_CAT_COLORS[m.category] || "var(--accent-primary, #8a2be2)";

        const head = document.createElement("div");
        head.style.cssText = "display:flex; justify-content:space-between; gap:8px; margin-bottom:4px;";
        const cat = document.createElement("span");
        cat.className = "session-item-name";
        cat.textContent = m.category || ("Tab " + m.tab);
        const time = document.createElement("span");
        time.className = "session-item-time";
        time.textContent = m.ts || "";
        head.appendChild(cat);
        head.appendChild(time);
        item.appendChild(head);

        if (m.summary) {
            const summary = document.createElement("div");
            summary.style.cssText = "font-size:12px; color: var(--text-secondary, #9aa); margin-bottom:6px;";
            summary.textContent = m.summary;
            item.appendChild(summary);
        }

        const body = document.createElement("pre");
        body.className = "message-text";
        body.textContent = m.text || "";   // textContent => el texto del juego no puede inyectar HTML
        item.appendChild(body);

        listEl.appendChild(item);
    });
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.innerText = val;
}

function formatNumber(num) {
    return Number(num).toLocaleString('es-ES');
}

const SHIP_TRANSLATIONS = {
    "large_cargo": "Carguero Grande",
    "small_cargo": "Carguero Pequeño",
    "recycler": "Reciclador",
    "espionage_probe": "Sonda de Espionaje",
    "light_fighter": "Caza Ligero",
    "heavy_fighter": "Caza Pesado",
    "cruiser": "Crucero",
    "battleship": "Nave de Batalla",
    "colony_ship": "Nave de Colonización",
    "battlecruiser": "Acorazado",
    "bomber": "Bombardero",
    "destroyer": "Destructor",
    "deathstar": "Estrella de la Muerte",
    "reaper": "Segador",
    "pathfinder": "Explorador"
};

function renderShipsFound(ships) {
    const container = document.getElementById("stats_ships_found");
    if (!container) return;

    const keys = Object.keys(ships).filter(k => ships[k] > 0);
    if (keys.length === 0) {
        container.innerHTML = '<div class="text-muted" style="font-size: 13px;">Aún no se han encontrado naves.</div>';
        return;
    }

    container.innerHTML = "";
    keys.forEach(key => {
        const name = SHIP_TRANSLATIONS[key] || key;
        const count = ships[key];
        const badge = document.createElement("div");
        badge.className = "ship-found-tag";
        badge.innerHTML = `
            <span class="ship-found-name">${name}</span>
            <span class="ship-found-qty">+${count}</span>
        `;
        container.appendChild(badge);
    });
}

function updateFleetInventory() {
    // Naves paradas en planetas/lunas
    const onPlanet = {};
    planetsCache.forEach(p => {
        const ships = p.ships || {};
        Object.keys(ships).forEach(ship => {
            onPlanet[ship] = (onPlanet[ship] || 0) + (ships[ship] || 0);
        });
    });

    // Naves en vuelo (flotas en movimiento) + total combinado
    const inMotion = fleetInMotionCache || {};
    const totals = {};
    [onPlanet, inMotion].forEach(src => {
        Object.keys(src).forEach(ship => {
            totals[ship] = (totals[ship] || 0) + (src[ship] || 0);
        });
    });

    // Actualizar las etiquetas de "Actual: X" al lado de los inputs de objetivos
    const targetShips = ["large_cargo", "small_cargo", "recycler", "espionage_probe", "light_fighter", "cruiser", "battleship"];
    targetShips.forEach(ship => {
        const span = document.getElementById(`current_qty_${ship}`);
        if (span) {
            const qty = totals[ship] || 0;
            const flying = inMotion[ship] || 0;
            span.innerText = flying > 0 ? `(Actual: ${qty} · ${flying} en vuelo)` : `(Actual: ${qty})`;
        }
    });

    // Renderizar el inventario imperial completo de flotas
    const container = document.getElementById("shipsInventoryList");
    if (!container) return;

    const keys = Object.keys(totals).filter(k => totals[k] > 0);
    if (keys.length === 0) {
        container.innerHTML = '<div class="text-muted" style="font-size: 13px; padding: 10px;">No se detectaron naves. Ejecuta el bot para sincronizar.</div>';
        return;
    }

    container.innerHTML = "";
    keys.forEach(key => {
        const name = SHIP_TRANSLATIONS[key] || key;
        const count = totals[key];
        const flying = inMotion[key] || 0;
        const tag = document.createElement("div");
        tag.className = "inventory-ship-tag";
        tag.innerHTML = `
            <span class="inventory-ship-name">${name}${flying > 0 ? ` <span class="ship-flying-note" title="${flying} en vuelo">✈ ${flying}</span>` : ""}</span>
            <span class="inventory-ship-qty">${count}</span>
        `;
        container.appendChild(tag);
    });
}

// --------------------------------------------------------------------------
// Lógica de Selector de Expediciones Dinámico
// --------------------------------------------------------------------------
function initExpeditionShipsSelector() {
    const select = document.getElementById("expAddShipSelect");
    const btnAdd = document.getElementById("btnExpAddShip");
    if (!select || !btnAdd) return;

    // Poblar desplegable
    select.innerHTML = "";
    Object.keys(SHIP_TRANSLATIONS).forEach(shipKey => {
        const option = document.createElement("option");
        option.value = shipKey;
        option.innerText = SHIP_TRANSLATIONS[shipKey];
        select.appendChild(option);
    });

    btnAdd.addEventListener("click", () => {
        const shipKey = select.value;
        addExpeditionShipRow(shipKey, 1);
    });
}

function addExpeditionShipRow(shipKey, qty) {
    const container = document.getElementById("expShipsList");
    if (!container) return;

    // Evitar duplicados
    if (container.querySelector(`[data-ship-type="${shipKey}"]`)) {
        showToast("Esta nave ya está en la lista", "warning");
        return;
    }

    const row = document.createElement("div");
    row.className = "exp-ship-row";
    row.dataset.shipType = shipKey;
    row.innerHTML = `
        <span class="exp-ship-name">${SHIP_TRANSLATIONS[shipKey] || shipKey}</span>
        <div class="exp-ship-controls">
            <input type="number" class="exp-ship-qty form-control-inline" min="1" value="${qty}">
            <button type="button" class="btn-exp-remove" title="Eliminar nave">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
            </button>
        </div>
    `;

    // Botón eliminar
    row.querySelector(".btn-exp-remove").addEventListener("click", () => {
        row.remove();
    });

    container.appendChild(row);
}

function renderExpeditionShips(cfg) {
    const container = document.getElementById("expShipsList");
    if (!container) return;
    container.innerHTML = "";

    const expShips = cfg.expedition_ships || {};
    Object.keys(expShips).forEach(shipKey => {
        const qty = parseI(expShips[shipKey], 0);
        if (qty > 0) {
            addExpeditionShipRow(shipKey, qty);
        }
    });
}

// Alterna entre el editor manual de naves y las opciones de auto-cálculo
function toggleExpeditionMode() {
    const auto = document.getElementById("expedition_auto_ships");
    const manualBlock = document.getElementById("expManualBlock");
    const autoBlock = document.getElementById("expAutoBlock");
    if (!auto || !manualBlock || !autoBlock) return;
    const isAuto = auto.checked;
    manualBlock.style.display = isAuto ? "none" : "block";
    autoBlock.style.display = isAuto ? "block" : "none";
}

function expFmtETA(sec) {
    if (typeof sec !== "number" || isNaN(sec) || sec <= 0) return "ahora";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = Math.floor(sec % 60);
    const pad = n => n.toString().padStart(2, "0");
    return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

function loadExpeditionStatus() {
    fetch(api("/api/expedition"))
        .then(res => res.json())
        .then(data => renderExpeditionStatus(data))
        .catch(() => {});
}

function loadBuildStatus() {
    fetch(api("/api/buildstatus"))
        .then(res => res.json())
        .then(data => renderBuildStatus(data))
        .catch(() => {});
}

function renderBuildStatus(data) {
    buildStatusCache = data || {};
    updatePlanetQueueLines();
    const el = document.getElementById("buildStatusPanel");
    if (!el) return;
    if (!data || !data.updated_at) {
        el.textContent = "Inicia el bot para ver los tiempos restantes de construcciones e investigación.";
        return;
    }
    const now = Date.now() / 1000;
    let html = "";
    (data.planets || []).forEach(p => {
        const eta = expFmtETA((p.finish_epoch || 0) - now);
        const q = (p.queue && p.queue.length) ? p.queue.join(", ") : "construcción";
        html += `<div style="margin:4px 0;">🏗️ <b>${p.name || p.coords}</b> <span style="opacity:.7">${p.coords}</span>: ${q} — <span class="exp-eta">${eta}</span></div>`;
    });
    if (data.research && data.research.finish_epoch) {
        const eta = expFmtETA(data.research.finish_epoch - now);
        html += `<div style="margin:4px 0;">🔬 Investigación <b>${data.research.tech || ""}</b> — <span class="exp-eta">${eta}</span></div>`;
    }
    el.innerHTML = html || "Sin construcciones ni investigación en curso ahora mismo.";
}

function renderExpeditionStatus(data) {
    expeStatusCache = data || {};
    const live = document.getElementById("expLiveStatus");
    const preview = document.getElementById("expAutoPreview");
    if (!data || !data.updated_at) {
        if (live) live.textContent = "El bot no está enviando datos todavía. Inícialo para ver el estado de las expediciones.";
        return;
    }
    const now = Date.now() / 1000;
    const nextEpoch = data.next_event_epoch || 0;
    const eta = nextEpoch > 0 ? expFmtETA(nextEpoch - now) : "—";
    const inFlight = (data.returns_epochs || []).filter(e => e > now).length;

    if (live) {
        live.innerHTML =
            `<span class="exp-metric">Slots: <b>${data.active_expe_slots ?? 0}/${data.total_expe_slots ?? 0}</b></span>` +
            `<span class="exp-metric">En vuelo: <b>${inFlight}</b></span>` +
            `<span class="exp-metric">Próxima vuelta: <span class="exp-eta">${eta}</span></span>` +
            (data.rotate_systems ? `<span class="exp-metric">Rotación: <b>±${data.system_range}</b> sist. (idx ${data.rotation_index})</span>` : "");
    }
    if (preview) {
        if (data.auto_ships) {
            preview.innerHTML =
                `Top-1 universo: <b>${(data.top1_points || 0).toLocaleString("es")}</b> pts · ` +
                `Botín máx: <b>${(data.max_find_units || 0).toLocaleString("es")}</b> u · ` +
                `Óptimo: <b>${data.optimal_cargo || 0}</b> ${data.cargo_ship || ""} · ` +
                `Enviando <b>${data.cargo_per_expedition || 0}</b>/expedición`;
        } else {
            preview.textContent = "Activa el auto-cálculo para ver el dimensionado en vivo.";
        }
    }
}

// --------------------------------------------------------------------------
// Lógica Premium de Objetivos de Defensa por Planeta
// --------------------------------------------------------------------------
const DEFENSE_TRANSLATIONS = {
    "rocket_launcher": "Lanzacohetes",
    "light_laser": "Láser Ligero",
    "heavy_laser": "Láser Pesado",
    "gauss_cannon": "Cañón Gauss",
    "ion_cannon": "Cañón Iónico",
    "plasma_turret": "Torreta de Plasma",
    "small_shield_dome": "Cúpula Pequeña de Protección",
    "large_shield_dome": "Cúpula Grande de Protección"
};

const DEFENSE_KEYS = [
    "rocket_launcher",
    "light_laser",
    "heavy_laser",
    "gauss_cannon",
    "ion_cannon",
    "plasma_turret",
    "small_shield_dome",
    "large_shield_dome"
];

function populateDefensePlanetSelect() {
    const select = document.getElementById("defense_planet_select");
    if (!select) return;
    
    const currentSelected = select.value;
    
    select.innerHTML = '<option value="">-- Selecciona un planeta --</option>';
    planetsCache.forEach(p => {
        const option = document.createElement("option");
        option.value = p.coords;
        option.innerText = `${p.name} [${p.coords}]`;
        select.appendChild(option);
    });
    
    if (currentSelected && planetsCache.some(p => p.coords === currentSelected)) {
        select.value = currentSelected;
        renderDefenseTargetsList(currentSelected);
    } else {
        document.getElementById("defense_targets_panel").style.display = "none";
    }
}

function renderDefenseTargetsList(coords) {
    const container = document.getElementById("defenseTargetsList");
    if (!container) return;
    
    container.innerHTML = "";
    
    const planetData = planetsCache.find(p => p.coords === coords) || {};
    const currentDefenses = planetData.defenses || {};
    const pCfg = localPlanetsConfig[coords] || {};
    const defenseTargets = pCfg.defense_targets || {};
    
    DEFENSE_KEYS.forEach(key => {
        const name = DEFENSE_TRANSLATIONS[key] || key;
        const currentQty = currentDefenses[key] || 0;
        const targetQty = defenseTargets[key] !== undefined ? defenseTargets[key] : 0;
        const isActive = targetQty > 0;
        
        const row = document.createElement("tr");
        row.style.borderBottom = "1px solid rgba(255,255,255,0.05)";
        row.innerHTML = `
            <td style="padding: 10px 4px; vertical-align: middle;">
                <label class="switch-container-mini" style="display: inline-block; position: relative; width: 32px; height: 18px;">
                    <input type="checkbox" class="def-active-chk" ${isActive ? "checked" : ""} style="opacity: 0; width: 0; height: 0;">
                    <span class="slider-mini" style="position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: rgba(255,255,255,0.1); transition: .3s; border-radius: 9px;"></span>
                </label>
            </td>
            <td style="padding: 10px 8px; color: #fff; font-weight: 500; font-size: 13px; vertical-align: middle;">
                ${name}
            </td>
            <td style="padding: 10px 8px; text-align: right; color: var(--text-secondary); font-size: 13px; font-family: 'Fira Code', monospace; vertical-align: middle;">
                ${currentQty}
            </td>
            <td style="padding: 10px 8px; text-align: right; vertical-align: middle;">
                <input type="number" class="def-target-input form-control-inline" min="0" value="${targetQty}" style="width: 80px; text-align: right; padding: 4px 8px; font-size: 13px; background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; color: #fff;" ${!isActive ? "disabled" : ""}>
            </td>
        `;
        
        const chk = row.querySelector(".def-active-chk");
        const input = row.querySelector(".def-target-input");
        const slider = row.querySelector(".slider-mini");
        
        const updateSliderStyle = () => {
            if (chk.checked) {
                slider.style.backgroundColor = "var(--accent-primary, #8a2be2)";
            } else {
                slider.style.backgroundColor = "rgba(255,255,255,0.1)";
            }
        };
        
        updateSliderStyle();
        
        chk.addEventListener("change", () => {
            input.disabled = !chk.checked;
            if (chk.checked) {
                if (parseInt(input.value) <= 0) {
                    input.value = 1;
                }
            } else {
                input.value = 0;
            }
            updateSliderStyle();
            saveCurrentDefenseTargetsInMemory(coords);
        });
        
        input.addEventListener("input", () => {
            saveCurrentDefenseTargetsInMemory(coords);
        });
        
        container.appendChild(row);
    });
}

function saveCurrentDefenseTargetsInMemory(coords) {
    if (!coords) return;
    if (!localPlanetsConfig[coords]) {
        localPlanetsConfig[coords] = {};
    }
    
    const defenseTargets = {};
    const rows = document.querySelectorAll("#defenseTargetsList tr");
    rows.forEach((row, index) => {
        const key = DEFENSE_KEYS[index];
        const chk = row.querySelector(".def-active-chk");
        const input = row.querySelector(".def-target-input");
        
        if (chk && chk.checked) {
            const targetQty = parseI(input.value, 0);
            if (targetQty > 0) {
                defenseTargets[key] = targetQty;
            }
        }
    });
    
    localPlanetsConfig[coords].defense_targets = defenseTargets;
}

// --------------------------------------------------------------------------
// Lógica Premium de Objetivos de Instalaciones por Planeta
// --------------------------------------------------------------------------
const FACILITIES_TRANSLATIONS = {
    "robotics_factory": "Fábrica de Robots",
    "shipyard": "Hangar",
    "research_lab": "Laboratorio de Investigación",
    "nanite_factory": "Fábrica de Nanitas"
};

const FACILITIES_KEYS = [
    "robotics_factory",
    "shipyard",
    "research_lab",
    "nanite_factory"
];

function getFacilityConfigField(key) {
    if (key === "research_lab") return "target_research_lab";
    return `target_${key}`;
}

function populateFacilitiesPlanetSelect() {
    const select = document.getElementById("facilities_planet_select");
    if (!select) return;
    
    const currentSelected = select.value;
    
    select.innerHTML = '<option value="">-- Selecciona un planeta --</option>';
    planetsCache.forEach(p => {
        const option = document.createElement("option");
        option.value = p.coords;
        option.innerText = `${p.name} [${p.coords}]`;
        select.appendChild(option);
    });
    
    if (currentSelected && planetsCache.some(p => p.coords === currentSelected)) {
        select.value = currentSelected;
        renderFacilitiesTargetsList(currentSelected);
    } else {
        document.getElementById("facilities_targets_panel").style.display = "none";
    }
}

function renderFacilitiesTargetsList(coords) {
    const container = document.getElementById("facilitiesTargetsList");
    if (!container) return;
    
    container.innerHTML = "";
    
    const planetData = planetsCache.find(p => p.coords === coords) || {};
    const currentBuildings = planetData.buildings || {};
    const pCfg = localPlanetsConfig[coords] || {};
    
    FACILITIES_KEYS.forEach(key => {
        const name = FACILITIES_TRANSLATIONS[key] || key;
        const currentQty = currentBuildings[key] || 0;
        const configField = getFacilityConfigField(key);
        const targetQty = pCfg[configField] !== undefined ? pCfg[configField] : 0;
        const isActive = targetQty > 0;
        
        const row = document.createElement("tr");
        row.style.borderBottom = "1px solid rgba(255,255,255,0.05)";
        row.innerHTML = `
            <td style="padding: 10px 4px; vertical-align: middle;">
                <label class="switch-container-mini" style="display: inline-block; position: relative; width: 32px; height: 18px;">
                    <input type="checkbox" class="fac-active-chk" ${isActive ? "checked" : ""} style="opacity: 0; width: 0; height: 0;">
                    <span class="slider-mini" style="position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: rgba(255,255,255,0.1); transition: .3s; border-radius: 9px;"></span>
                </label>
            </td>
            <td style="padding: 10px 8px; color: #fff; font-weight: 500; font-size: 13px; vertical-align: middle;">
                ${name}
            </td>
            <td style="padding: 10px 8px; text-align: right; color: var(--text-secondary); font-size: 13px; font-family: 'Fira Code', monospace; vertical-align: middle;">
                ${currentQty}
            </td>
            <td style="padding: 10px 8px; text-align: right; vertical-align: middle;">
                <input type="number" class="fac-target-input form-control-inline" min="0" value="${targetQty}" style="width: 80px; text-align: right; padding: 4px 8px; font-size: 13px; background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; color: #fff;" ${!isActive ? "disabled" : ""}>
            </td>
        `;
        
        const chk = row.querySelector(".fac-active-chk");
        const input = row.querySelector(".fac-target-input");
        const slider = row.querySelector(".slider-mini");
        
        const updateSliderStyle = () => {
            if (chk.checked) {
                slider.style.backgroundColor = "var(--accent-primary, #8a2be2)";
            } else {
                slider.style.backgroundColor = "rgba(255,255,255,0.1)";
            }
        };
        
        updateSliderStyle();
        
        chk.addEventListener("change", () => {
            input.disabled = !chk.checked;
            if (chk.checked) {
                if (parseInt(input.value) <= 0) {
                    input.value = 1;
                }
            } else {
                input.value = 0;
            }
            updateSliderStyle();
            saveCurrentFacilitiesTargetsInMemory(coords);
        });
        
        input.addEventListener("input", () => {
            saveCurrentFacilitiesTargetsInMemory(coords);
        });
        
        container.appendChild(row);
    });
}

function saveCurrentFacilitiesTargetsInMemory(coords) {
    if (!coords) return;
    if (!localPlanetsConfig[coords]) {
        localPlanetsConfig[coords] = {};
    }
    
    const rows = document.querySelectorAll("#facilitiesTargetsList tr");
    rows.forEach((row, index) => {
        const key = FACILITIES_KEYS[index];
        const configField = getFacilityConfigField(key);
        const chk = row.querySelector(".fac-active-chk");
        const input = row.querySelector(".fac-target-input");
        
        if (chk && chk.checked) {
            const targetQty = parseI(input.value, 0);
            if (targetQty > 0) {
                localPlanetsConfig[coords][configField] = targetQty;
            } else {
                localPlanetsConfig[coords][configField] = 0;
            }
        } else {
            localPlanetsConfig[coords][configField] = 0;
        }
    });
}

// --------------------------------------------------------------------------
// Lógica de Búsqueda de Imperio (Localizador de Colonias)
// --------------------------------------------------------------------------
function initColonyLocator() {
    const btnRunLocator = document.getElementById("btnRunLocator");
    const locatorCoordsInput = document.getElementById("locator_coords");
    const locatorServerInput = document.getElementById("locator_server");
    const locatorConsole = document.getElementById("locatorConsole");

    if (!btnRunLocator) return;

    btnRunLocator.addEventListener("click", () => {
        const coordinate = locatorCoordsInput.value.trim();
        const server = locatorServerInput.value.trim();
        
        if (!coordinate) {
            showToast("Por favor, introduce una coordenada", "danger");
            return;
        }
        
        // Expresión regular para validar formato de coordenadas (ej: 1:23:4 o [1:23:4])
        const cleanCoords = coordinate.replace(/[\[\]]/g, '').trim();
        const coordsRegex = /^\d+:\d+:\d+$/;
        if (!coordsRegex.test(cleanCoords)) {
            showToast("Formato de coordenada incorrecto (ej: 3:125:8)", "danger");
            return;
        }

        btnRunLocator.disabled = true;
        const originalBtnText = btnRunLocator.innerHTML;
        btnRunLocator.innerHTML = `
            <svg class="spinner" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite; margin-right: 8px; vertical-align: middle;">
                <circle cx="12" cy="12" r="10" stroke-dasharray="16 14"></circle>
            </svg>
            Buscando...
        `;
        
        locatorConsole.innerHTML = `
            <div class="log-line log-info">[*] Conectando con el radar del bot...</div>
            <div class="log-line log-info">[*] Ejecutando Localizador de colonias para [${cleanCoords}]...</div>
            <div class="log-line text-muted">Aguardando respuesta de los servidores de OGame (esto puede tardar unos segundos)...</div>
        `;
        
        fetch(api("/api/locator"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ coordinate: cleanCoords, server })
        })
        .then(res => {
            if (!res.ok) {
                return res.json().then(errData => { throw new Error(errData.error || "Error en el servidor"); });
            }
            return res.json();
        })
        .then(data => {
            btnRunLocator.disabled = false;
            btnRunLocator.innerHTML = originalBtnText;
            
            if (!data.output) {
                locatorConsole.innerHTML = `
                    <div class="log-line log-error">[!] Error al ejecutar el localizador: ${data.error || "No se recibió respuesta del radar."}</div>
                `;
                showToast("Error en la búsqueda", "danger");
                return;
            }
            
            locatorConsole.innerHTML = "";
            const lines = data.output.split("\n");
            lines.forEach(line => {
                const div = document.createElement("div");
                div.className = "log-line";
                
                // Darle colores según el tipo de mensaje
                if (line.includes("[+]")) {
                    div.classList.add("log-action");
                } else if (line.includes("[!]")) {
                    div.classList.add("log-error");
                } else if (line.includes("[*]")) {
                    div.classList.add("log-info");
                } else if (line.includes("INFORME DE IMPERIO") || line.includes("===")) {
                    div.classList.add("log-info");
                    div.style.fontWeight = "bold";
                } else if (line.includes("<-- (Coordenada introducida)")) {
                    div.style.color = "var(--accent-secondary)";
                    div.style.fontWeight = "bold";
                }
                
                div.textContent = line;
                locatorConsole.appendChild(div);
            });
            
            if (data.error) {
                const errorDiv = document.createElement("div");
                errorDiv.className = "log-line log-error";
                errorDiv.textContent = `\n[Errores/Advertencias del proceso]:\n${data.error}`;
                locatorConsole.appendChild(errorDiv);
            }
            
            locatorConsole.scrollTop = locatorConsole.scrollHeight;
            showToast("Búsqueda completada", "success");
        })
        .catch(err => {
            btnRunLocator.disabled = false;
            btnRunLocator.innerHTML = originalBtnText;
            locatorConsole.innerHTML = `
                <div class="log-line log-error">[!] Error de conexión: ${err.message || err}</div>
            `;
            showToast("Error de conexión", "danger");
        });
    });
}

// --------------------------------------------------------------------------
// Traducciones y Renderizado de Resumen de Sesión
// --------------------------------------------------------------------------
const BUILDING_TRANSLATIONS = {
    "metal_mine": "Mina de Metal",
    "crystal_mine": "Mina de Cristal",
    "deut_synth": "Sintetizador de Deuterio",
    "solar_plant": "Planta de Energía Solar",
    "fusion_reactor": "Reactor de Fusión",
    "robotics_factory": "Fábrica de Robots",
    "nanite_factory": "Fábrica de Nanitas",
    "shipyard": "Hangar (Astillero)",
    "research_lab": "Laboratorio de Investigación",
    "metal_storage": "Almacén de Metal",
    "crystal_storage": "Almacén de Cristal",
    "deut_tank": "Contenedor de Deuterio"
};

function formatCountdown(sec) {
    if (typeof sec !== "number" || isNaN(sec) || sec <= 0) return "Impactado / Finalizado";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const pad = (n) => n.toString().padStart(2, "0");
    return `Impacto en ${pad(h)}:${pad(m)}:${pad(s)}`;
}

function renderSessionActions(sessionActions) {
    // 1. Edificios
    let totalBuildings = 0;
    let buildingsHtml = "";
    if (sessionActions && sessionActions.buildings) {
        const entries = [];
        Object.keys(sessionActions.buildings).forEach(coords => {
            if (Array.isArray(sessionActions.buildings[coords])) {
                sessionActions.buildings[coords].forEach(action => {
                    entries.push({ coords, ...action });
                });
            }
        });
        entries.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
        totalBuildings = entries.length;
        entries.forEach(entry => {
            const transName = BUILDING_TRANSLATIONS[entry.name] || entry.name;
            buildingsHtml += `
                <div class="session-list-item">
                    <div class="session-item-main">
                        <span class="session-item-name">${transName} (Nivel ${entry.value})</span>
                        <span class="session-item-details"><span class="session-item-planet">${entry.coords}</span></span>
                    </div>
                    <span class="session-item-time">${entry.timestamp}</span>
                </div>
            `;
        });
    }
    const buildingsListEl = document.getElementById("session_buildings_list");
    const buildingsCountEl = document.getElementById("session_buildings_count");
    if (buildingsListEl) buildingsListEl.innerHTML = buildingsHtml || `<div class="text-muted" style="font-size: 13px; padding: 4px;">Ninguna construcción realizada todavía.</div>`;
    if (buildingsCountEl) buildingsCountEl.innerText = totalBuildings;

    // 2. Investigaciones
    let totalResearch = 0;
    let researchHtml = "";
    if (sessionActions && sessionActions.research) {
        const entries = [...sessionActions.research];
        entries.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
        totalResearch = entries.length;
        entries.forEach(entry => {
            const transName = TECH_NAMES[entry.name] || entry.name;
            researchHtml += `
                <div class="session-list-item" style="border-left-color: var(--accent-secondary, #00d2ff);">
                    <div class="session-item-main">
                        <span class="session-item-name">${transName} (Nivel ${entry.value})</span>
                    </div>
                    <span class="session-item-time">${entry.timestamp}</span>
                </div>
            `;
        });
    }
    const researchListEl = document.getElementById("session_research_list");
    const researchCountEl = document.getElementById("session_research_count");
    if (researchListEl) researchListEl.innerHTML = researchHtml || `<div class="text-muted" style="font-size: 13px; padding: 4px;">Ninguna investigación realizada todavía.</div>`;
    if (researchCountEl) researchCountEl.innerText = totalResearch;

    // 3. Flota
    let totalFleet = 0;
    let fleetHtml = "";
    if (sessionActions && sessionActions.fleet) {
        const entries = [];
        Object.keys(sessionActions.fleet).forEach(coords => {
            if (Array.isArray(sessionActions.fleet[coords])) {
                sessionActions.fleet[coords].forEach(action => {
                    entries.push({ coords, ...action });
                });
            }
        });
        entries.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
        entries.forEach(entry => {
            totalFleet += entry.value;
            const transName = SHIP_TRANSLATIONS[entry.name] || entry.name;
            fleetHtml += `
                <div class="session-list-item" style="border-left-color: #a5d6a7;">
                    <div class="session-item-main">
                        <span class="session-item-name">${transName} x${entry.value}</span>
                        <span class="session-item-details"><span class="session-item-planet">${entry.coords}</span></span>
                    </div>
                    <span class="session-item-time">${entry.timestamp}</span>
                </div>
            `;
        });
    }
    const fleetListEl = document.getElementById("session_fleet_list");
    const fleetCountEl = document.getElementById("session_fleet_count");
    if (fleetListEl) fleetListEl.innerHTML = fleetHtml || `<div class="text-muted" style="font-size: 13px; padding: 4px;">Ninguna naves fabricada todavía.</div>`;
    if (fleetCountEl) fleetCountEl.innerText = totalFleet;

    // 4. Defensa
    let totalDefense = 0;
    let defenseHtml = "";
    if (sessionActions && sessionActions.defense) {
        const entries = [];
        Object.keys(sessionActions.defense).forEach(coords => {
            if (Array.isArray(sessionActions.defense[coords])) {
                sessionActions.defense[coords].forEach(action => {
                    entries.push({ coords, ...action });
                });
            }
        });
        entries.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
        entries.forEach(entry => {
            totalDefense += entry.value;
            const transName = DEFENSE_TRANSLATIONS[entry.name] || entry.name;
            defenseHtml += `
                <div class="session-list-item" style="border-left-color: #fdd835;">
                    <div class="session-item-main">
                        <span class="session-item-name">${transName} x${entry.value}</span>
                        <span class="session-item-details"><span class="session-item-planet">${entry.coords}</span></span>
                    </div>
                    <span class="session-item-time">${entry.timestamp}</span>
                </div>
            `;
        });
    }
    const defenseListEl = document.getElementById("session_defense_list");
    const defenseCountEl = document.getElementById("session_defense_count");
    if (defenseListEl) defenseListEl.innerHTML = defenseHtml || `<div class="text-muted" style="font-size: 13px; padding: 4px;">Ninguna defensa construida todavía.</div>`;
    if (defenseCountEl) defenseCountEl.innerText = totalDefense;

    // 5. Farmeo
    let totalFarming = 0;
    let farmingHtml = "";
    if (sessionActions && sessionActions.farming) {
        const entries = [];
        Object.keys(sessionActions.farming).forEach(coords => {
            if (Array.isArray(sessionActions.farming[coords])) {
                sessionActions.farming[coords].forEach(action => {
                    entries.push({ coords, ...action });
                });
            }
        });
        entries.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
        totalFarming = entries.length;
        entries.forEach(entry => {
            farmingHtml += `
                <div class="session-list-item" style="border-left-color: #8ab4f8;">
                    <div class="session-item-main">
                        <span class="session-item-name">Ataque enviado a ${entry.name}</span>
                        <span class="session-item-details"><span class="session-item-planet" style="color: #8ab4f8;">Origen: ${entry.coords}</span></span>
                    </div>
                    <span class="session-item-time">${entry.timestamp}</span>
                </div>
            `;
        });
    }
    const farmingListEl = document.getElementById("session_farming_list");
    const farmingCountEl = document.getElementById("session_farming_count");
    if (farmingListEl) farmingListEl.innerHTML = farmingHtml || `<div class="text-muted" style="font-size: 13px; padding: 4px;">Ningún ataque de farmeo enviado todavía.</div>`;
    if (farmingCountEl) farmingCountEl.innerText = totalFarming;

    // 6. Expediciones
    let totalExpeditions = 0;
    let expeditionsHtml = "";
    if (sessionActions && sessionActions.expeditions) {
        const entries = [];
        Object.keys(sessionActions.expeditions).forEach(coords => {
            if (Array.isArray(sessionActions.expeditions[coords])) {
                sessionActions.expeditions[coords].forEach(action => {
                    entries.push({ coords, ...action });
                });
            }
        });
        entries.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
        totalExpeditions = entries.length;
        entries.forEach(entry => {
            expeditionsHtml += `
                <div class="session-list-item" style="border-left-color: #ffb703;">
                    <div class="session-item-main">
                        <span class="session-item-name">Expedición a ${entry.name}</span>
                        <span class="session-item-details"><span class="session-item-planet" style="color: #ffb703;">Origen: ${entry.coords}</span></span>
                    </div>
                    <span class="session-item-time">${entry.timestamp}</span>
                </div>
            `;
        });
    }
    const expeditionsListEl = document.getElementById("session_expeditions_list");
    const expeditionsCountEl = document.getElementById("session_expeditions_count");
    if (expeditionsListEl) expeditionsListEl.innerHTML = expeditionsHtml || `<div class="text-muted" style="font-size: 13px; padding: 4px;">Ninguna expedición lanzada todavía.</div>`;
    if (expeditionsCountEl) expeditionsCountEl.innerText = totalExpeditions;

    // 7. Ataques Hostiles
    let totalHostile = 0;
    let hostileHtml = "";
    if (sessionActions && sessionActions.hostile_attacks) {
        const entries = [];
        Object.keys(sessionActions.hostile_attacks).forEach(coords => {
            if (Array.isArray(sessionActions.hostile_attacks[coords])) {
                sessionActions.hostile_attacks[coords].forEach(action => {
                    entries.push({ coords, ...action });
                });
            }
        });
        entries.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
        totalHostile = entries.length;
        entries.forEach(entry => {
            const countdownText = formatCountdown(entry.value);
            const isImpacted = entry.value <= 0;
            const colorStyle = isImpacted ? "color: var(--text-muted);" : "color: var(--accent-danger, #ff6b6b); font-weight: bold;";
            
            hostileHtml += `
                <div class="session-list-item" style="border-left-color: var(--accent-danger, #ff6b6b); background: rgba(239, 68, 68, 0.05);">
                    <div class="session-item-main">
                        <span class="session-item-name" style="color: var(--accent-danger, #ff6b6b); font-weight: 600;">${entry.name}</span>
                        <span class="session-item-details">
                            <span class="session-item-planet" style="color: var(--accent-danger, #ff6b6b); background: rgba(239, 68, 68, 0.15); margin-right: 6px;">Destino: ${entry.coords}</span>
                            <span style="${colorStyle}">${countdownText}</span>
                        </span>
                    </div>
                    <span class="session-item-time" style="color: var(--text-secondary);">${entry.timestamp}</span>
                </div>
            `;
        });
    }
    const hostileListEl = document.getElementById("session_hostile_attacks_list");
    const hostileCountEl = document.getElementById("session_hostile_attacks_count");
    if (hostileListEl) hostileListEl.innerHTML = hostileHtml || `<div class="text-muted" style="font-size: 13px; padding: 4px;">No se han recibido ataques hostiles en esta sesión. ¡Todo tranquilo!</div>`;
    if (hostileCountEl) hostileCountEl.innerText = totalHostile;

    // 8. Espionajes y Decisiones
    let totalEspionage = 0;
    let espionageHtml = "";
    if (sessionActions && sessionActions.espionage) {
        const entries = [];
        Object.keys(sessionActions.espionage).forEach(coords => {
            if (Array.isArray(sessionActions.espionage[coords])) {
                sessionActions.espionage[coords].forEach(action => {
                    entries.push({ coords, ...action });
                });
            }
        });
        entries.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
        totalEspionage = entries.length;
        entries.forEach(entry => {
            const statusVal = String(entry.value);
            let statusColor = "var(--text-secondary)";
            let borderLeftColor = "rgba(255, 255, 255, 0.1)";
            let backgroundStyle = "background: rgba(255, 255, 255, 0.01);";
            
            if (statusVal.includes("Ataque enviado")) {
                statusColor = "var(--accent-success, #10b981)";
                borderLeftColor = "var(--accent-success, #10b981)";
                backgroundStyle = "background: rgba(16, 185, 129, 0.03);";
            } else if (statusVal.includes("Apto para ataque")) {
                statusColor = "var(--accent-secondary, #00d2ff)";
                borderLeftColor = "var(--accent-secondary, #00d2ff)";
                backgroundStyle = "background: rgba(0, 210, 255, 0.03);";
            } else if (statusVal.includes("Descartado") || statusVal.includes("insuficiente") || statusVal.includes("arriesgado") || statusVal.includes("Falta")) {
                statusColor = "#ffb703";
                borderLeftColor = "#ffb703";
            }
            
            espionageHtml += `
                <div class="session-list-item" style="border-left-color: ${borderLeftColor}; ${backgroundStyle}">
                    <div class="session-item-main">
                        <span class="session-item-name" style="font-weight: 600;">Espionaje a ${entry.name}</span>
                        <span class="session-item-details">
                            <span class="session-item-planet" style="margin-right: 6px;">Origen: ${entry.coords}</span>
                            <span style="color: ${statusColor}; font-weight: 500;">${statusVal}</span>
                        </span>
                    </div>
                    <span class="session-item-time" style="color: var(--text-secondary);">${entry.timestamp}</span>
                </div>
            `;
        });
    }
    const espionageListEl = document.getElementById("session_espionage_list");
    const espionageCountEl = document.getElementById("session_espionage_count");
    if (espionageListEl) espionageListEl.innerHTML = espionageHtml || `<div class="text-muted" style="font-size: 13px; padding: 4px;">Ningún espionaje realizado en esta sesión.</div>`;
    if (espionageCountEl) espionageCountEl.innerText = totalEspionage;
}

// --------------------------------------------------------------------------
// Gestión del Navegador en Directo (Live Browser Viewer & Controller)
// --------------------------------------------------------------------------
let liveTabActive = false;
let liveImageLoading = false;

function initLiveTab() {
    const img = document.getElementById("liveScreenImage");
    const container = document.getElementById("liveViewportContainer");
    const overlay = document.getElementById("liveViewportOverlay");
    const textInput = document.getElementById("liveTextInput");
    const btnType = document.getElementById("btnLiveType");
    const btnRefresh = document.getElementById("btnLiveRefresh");
    
    // 1. Manejo de Clics sobre la pantalla del directo
    if (img && container && overlay) {
        img.setAttribute("draggable", "false");  // evitar el arrastre nativo de la imagen
        let dragStart = null;

        const scaleCoords = (clientX, clientY) => {
            const rect = img.getBoundingClientRect();
            const relX = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
            const relY = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height));
            return {
                x: Math.round(relX * 1366),
                y: Math.round(relY * 768),
                localX: clientX - rect.left,
                localY: clientY - rect.top,
            };
        };

        const sendLive = (url, body, indicators) => {
            (indicators || []).forEach(p => showClickIndicator(p.x, p.y));
            overlay.style.display = "flex";
            liveImageLoading = true;
            fetch(api(url), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body)
            })
            .then(res => res.json())
            .then(data => {
                overlay.style.display = "none";
                liveImageLoading = false;
                if (data.error) showToast("Error de interacción: " + data.error, "danger");
                else refreshLiveScreenshot();
            })
            .catch(err => {
                overlay.style.display = "none";
                liveImageLoading = false;
                showToast("Error de conexión: " + err, "danger");
            });
        };

        // Distinguir CLIC vs ARRASTRE (los captchas "soy humano" suelen ser de arrastrar)
        img.addEventListener("mousedown", (e) => {
            if (liveImageLoading) return;
            e.preventDefault();  // sin esto el navegador inicia un arrastre fantasma de la imagen
            dragStart = scaleCoords(e.clientX, e.clientY);
        });

        img.addEventListener("mouseup", (e) => {
            if (liveImageLoading || !dragStart) { dragStart = null; return; }
            const end = scaleCoords(e.clientX, e.clientY);
            const moved = Math.hypot(end.localX - dragStart.localX, end.localY - dragStart.localY);
            const start = dragStart;
            dragStart = null;
            if (moved > 6) {
                sendLive("/api/live/drag",
                    { x: start.x, y: start.y, x2: end.x, y2: end.y },
                    [{ x: start.localX, y: start.localY }, { x: end.localX, y: end.localY }]);
            } else {
                sendLive("/api/live/click", { x: end.x, y: end.y },
                    [{ x: end.localX, y: end.localY }]);
            }
        });

        img.addEventListener("mouseleave", () => { dragStart = null; });
    }

    // Generador dinámico de ripples
    function showClickIndicator(x, y) {
        if (!container) return;
        const dot = document.createElement("div");
        dot.className = "click-indicator";
        dot.style.left = `${x}px`;
        dot.style.top = `${y}px`;
        container.appendChild(dot);
        
        setTimeout(() => {
            dot.remove();
        }, 600);
    }

    // 2. Envío de Texto (Type)
    if (btnType && textInput && overlay) {
        const sendText = () => {
            const text = textInput.value;
            if (!text) return;
            
            overlay.style.display = "flex";
            liveImageLoading = true;
            
            fetch(api("/api/live/type"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text })
            })
            .then(res => res.json())
            .then(data => {
                overlay.style.display = "none";
                liveImageLoading = false;
                if (data.error) {
                    showToast("Error al escribir: " + data.error, "danger");
                } else {
                    textInput.value = "";
                    refreshLiveScreenshot();
                }
            })
            .catch(err => {
                overlay.style.display = "none";
                liveImageLoading = false;
                showToast("Error de conexión: " + err, "danger");
            });
        };

        btnType.addEventListener("click", sendText);
        textInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                sendText();
            }
        });
    }

    // 3. Simular pulsación de Teclas Especiales
    const bindSpecialKey = (btnId, keyName) => {
        const btn = document.getElementById(btnId);
        if (btn && overlay) {
            btn.addEventListener("click", () => {
                overlay.style.display = "flex";
                liveImageLoading = true;
                
                fetch(api("/api/live/press"), {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ key: keyName })
                })
                .then(res => res.json())
                .then(data => {
                    overlay.style.display = "none";
                    liveImageLoading = false;
                    if (data.error) {
                        showToast("Error de teclado: " + data.error, "danger");
                    } else {
                        refreshLiveScreenshot();
                    }
                })
                .catch(err => {
                    overlay.style.display = "none";
                    liveImageLoading = false;
                    showToast("Error de conexión: " + err, "danger");
                });
            });
        }
    };

    bindSpecialKey("btnLiveKeyEnter", "Enter");
    bindSpecialKey("btnLiveKeyBackspace", "Backspace");
    bindSpecialKey("btnLiveKeyTab", "Tab");
    bindSpecialKey("btnLiveKeyEscape", "Escape");

    // 4. Refresco Manual
    if (btnRefresh) {
        btnRefresh.addEventListener("click", () => {
            refreshLiveScreenshot();
        });
    }
}

function refreshLiveScreenshot() {
    const img = document.getElementById("liveScreenImage");
    if (img && !liveImageLoading) {
        img.src = api("/api/live/screenshot?t=" + Date.now());
    }
}

function updateLiveTab() {
    const offlineOverlay = document.getElementById("liveOfflineOverlay");
    const viewPanel = document.getElementById("liveViewPanel");
    const dot = document.getElementById("liveStatusDot");
    const text = document.getElementById("liveStatusText");
    const img = document.getElementById("liveScreenImage");
    
    fetch(api("/api/live/status"))
        .then(res => res.json())
        .then(data => {
            if (data.available) {
                if (offlineOverlay) offlineOverlay.style.display = "none";
                if (viewPanel) viewPanel.style.display = "block";
                
                if (dot) {
                    dot.className = "live-status-dot active";
                }
                if (text) {
                    text.innerText = "Conectado al navegador del bot";
                    text.style.color = "var(--accent-success)";
                }
                
                // Cargar captura en vivo si no hay carga bloqueante
                if (img && !liveImageLoading) {
                    img.src = api("/api/live/screenshot?t=" + Date.now());
                }
            } else {
                if (offlineOverlay) offlineOverlay.style.display = "flex";
                if (viewPanel) viewPanel.style.display = "none";
                
                if (dot) {
                    dot.className = "live-status-dot inactive";
                }
                if (text) {
                    text.innerText = "Desconectado";
                    text.style.color = "var(--accent-danger)";
                }
            }
        })
        .catch(err => {
            if (offlineOverlay) offlineOverlay.style.display = "flex";
            if (viewPanel) viewPanel.style.display = "none";
            if (dot) dot.className = "live-status-dot inactive";
            if (text) {
                text.innerText = "Desconectado (Error API)";
                text.style.color = "var(--accent-danger)";
            }
        });
}




// --------------------------------------------------------------------------
// Histórico diario (gráficas en canvas, sin dependencias)
// --------------------------------------------------------------------------
let historyCache = [];

function cssColor(varName, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    return v || fallback;
}

function formatShortNumber(num) {
    const abs = Math.abs(num);
    if (abs >= 1e9) return (num / 1e9).toFixed(1) + "G";
    if (abs >= 1e6) return (num / 1e6).toFixed(1) + "M";
    if (abs >= 1e3) return (num / 1e3).toFixed(1) + "k";
    return Math.round(num).toString();
}

// series: [{name, color, values: [num|null]}], labels: eje X (fechas)
function drawLineChart(canvas, series, labels) {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    const padL = 58, padR = 14, padT = 12, padB = 26;
    const plotW = W - padL - padR, plotH = H - padT - padB;

    // Escala automática
    let minV = Infinity, maxV = -Infinity;
    series.forEach(s => s.values.forEach(v => {
        if (typeof v === "number" && !isNaN(v)) {
            if (v < minV) minV = v;
            if (v > maxV) maxV = v;
        }
    }));
    if (minV === Infinity) { minV = 0; maxV = 1; }
    if (minV > 0) minV = 0;                      // anclar a 0 para no exagerar variaciones
    if (maxV === minV) maxV = minV + 1;

    const n = labels.length;
    const x = i => padL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const y = v => padT + plotH - ((v - minV) / (maxV - minV)) * plotH;

    // Rejilla horizontal + etiquetas del eje Y
    ctx.font = "10px Inter, sans-serif";
    ctx.fillStyle = cssColor("--text-secondary", "#9ca3af");
    const ticks = 4;
    for (let t = 0; t <= ticks; t++) {
        const v = minV + (maxV - minV) * t / ticks;
        const yy = y(v);
        ctx.strokeStyle = "rgba(255,255,255,0.08)";
        ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
        ctx.textAlign = "right"; ctx.textBaseline = "middle";
        ctx.fillText(formatShortNumber(v), padL - 6, yy);
    }
    // Ejes
    ctx.strokeStyle = "rgba(255,255,255,0.25)";
    ctx.beginPath();
    ctx.moveTo(padL, padT); ctx.lineTo(padL, padT + plotH); ctx.lineTo(padL + plotW, padT + plotH);
    ctx.stroke();

    // Etiquetas del eje X (primera, media, última si hay muchas)
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const xIdx = n > 6 ? [0, Math.floor((n - 1) / 2), n - 1] : labels.map((_, i) => i);
    xIdx.forEach(i => ctx.fillText(labels[i] || "", x(i), H - padB + 6));

    // Líneas de datos
    series.forEach(s => {
        ctx.strokeStyle = s.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        let started = false;
        s.values.forEach((v, i) => {
            if (typeof v !== "number" || isNaN(v)) return;
            if (!started) { ctx.moveTo(x(i), y(v)); started = true; }
            else ctx.lineTo(x(i), y(v));
        });
        ctx.stroke();
    });
}

function renderChartLegend(elId, series) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.innerHTML = series.map(s =>
        `<span class="legend-item"><span class="legend-dot" style="background:${s.color}"></span>${s.name}</span>`
    ).join("");
}

function loadHistory() {
    fetch(api("/api/history"))
        .then(r => r.json())
        .then(data => {
            historyCache = data.history || [];
            renderHistoryCharts();
        })
        .catch(() => {});
}

// Nombres legibles para los acumulados conocidos del histórico
const HISTORY_KEY_NAMES = {
    "farming_metal": "Botín metal", "farming_crystal": "Botín cristal", "farming_deut": "Botín deuterio",
    "expeditions_metal": "Exped. metal", "expeditions_crystal": "Exped. cristal", "expeditions_deut": "Exped. deuterio",
    "recycling_metal": "Reciclaje metal", "recycling_crystal": "Reciclaje cristal", "recycling_deut": "Reciclaje deuterio",
    "attacks": "Ataques", "expeditions": "Expediciones", "loot": "Botín", "finds": "Hallazgos",
    "dark_matter": "Materia oscura"
};

function renderHistoryCharts() {
    const empty = document.getElementById("history_empty");
    const wrap = document.getElementById("history_charts");
    if (!empty || !wrap) return;

    // Fuente según el rango del dashboard: 24H = stats_hourly (C2); 7D/30D = histórico diario
    let entries, labels;
    if (dashRange === "24h") {
        entries = hourlyCache.slice(-24);
        labels = entries.map(e => {
            const d = new Date((e.ts || 0) * 1000);
            return String(d.getHours()).padStart(2, "0") + "h";
        });
    } else {
        entries = dashRange === "30d" ? historyCache.slice(-30) : historyCache.slice(-7);
        labels = entries.map(e => e.date || "");
    }
    if (!entries.length) {
        empty.style.display = "block";
        wrap.style.display = "none";
        return;
    }
    empty.style.display = "none";
    wrap.style.display = "block";

    // (a) recursos totales (M/C/D) del rango elegido
    const resSeries = [
        { name: "Metal", color: "#8ab4f8", values: entries.map(e => typeof e.metal === "number" ? e.metal : null) },
        { name: "Cristal", color: cssColor("--accent-secondary", "#00d2ff"), values: entries.map(e => typeof e.crystal === "number" ? e.crystal : null) },
        { name: "Deuterio", color: cssColor("--accent-success", "#10b981"), values: entries.map(e => typeof e.deut === "number" ? e.deut : null) }
    ];
    drawLineChart(document.getElementById("chart_resources"), resSeries, labels);
    renderChartLegend("legend_resources", resSeries);

    // (b) acumulados de sesión: cualquier otra clave numérica presente en el histórico
    const skip = new Set(["ts", "date", "metal", "crystal", "deut"]);
    const extraKeys = [];
    historyCache.forEach(e => Object.keys(e).forEach(k => {
        if (!skip.has(k) && typeof e[k] === "number" && !extraKeys.includes(k)) extraKeys.push(k);
    }));
    const sessionCard = document.getElementById("history_session_card");
    const selected = extraKeys.slice(0, 6);
    if (!selected.length || !historyCache.length) {
        if (sessionCard) sessionCard.style.display = "none";
        return;
    }
    if (sessionCard) sessionCard.style.display = "";
    const palette = [
        cssColor("--accent-secondary", "#00d2ff"),
        cssColor("--accent-primary", "#8a2be2"),
        cssColor("--accent-success", "#10b981"),
        "#f59e0b",
        cssColor("--accent-danger", "#f43f5e"),
        "#8ab4f8"
    ];
    const sesSeries = selected.map((k, i) => ({
        name: HISTORY_KEY_NAMES[k] || k.replace(/_/g, " "),
        color: palette[i % palette.length],
        values: historyCache.map(e => typeof e[k] === "number" ? e[k] : null)
    }));
    // El chart de acumulados siempre es diario (histórico), aunque el rango sea 24H
    drawLineChart(document.getElementById("chart_session"), sesSeries, historyCache.map(e => e.date || ""));
    renderChartLegend("legend_session", sesSeries);
}

// --------------------------------------------------------------------------
// Simulador "¿Ataco?"
// --------------------------------------------------------------------------
const SIM_ATTACK_SHIPS = ["small_cargo", "large_cargo", "light_fighter", "cruiser",
                          "battleship", "battlecruiser", "bomber", "destroyer"];
const SIM_DEFENSES = ["rocket_launcher", "light_laser", "heavy_laser", "gauss_cannon",
                      "ion_cannon", "plasma_turret", "small_shield_dome", "large_shield_dome"];
const SIM_TECHS = [["weapons", "Armas"], ["shielding", "Escudos"], ["armor", "Blindaje"]];

function simNumInput(id, label) {
    return `<div class="sim-field">
        <label for="${id}">${label}</label>
        <input type="number" id="${id}" min="0" step="1" placeholder="0">
    </div>`;
}

function initSimulator() {
    const atkShips = document.getElementById("sim_attacker_ships");
    const atkTech = document.getElementById("sim_attacker_tech");
    const defShips = document.getElementById("sim_defender_ships");
    const defDef = document.getElementById("sim_defender_defense");
    const defTech = document.getElementById("sim_defender_tech");
    if (!atkShips) return;   // pestaña no presente
    atkShips.innerHTML = SIM_ATTACK_SHIPS.map(s => simNumInput("sim_atk_" + s, SHIP_TRANSLATIONS[s] || s)).join("");
    defShips.innerHTML = SIM_ATTACK_SHIPS.map(s => simNumInput("sim_deffleet_" + s, SHIP_TRANSLATIONS[s] || s)).join("");
    defDef.innerHTML = SIM_DEFENSES.map(d => simNumInput("sim_defdef_" + d, DEFENSE_TRANSLATIONS[d] || d)).join("");
    atkTech.innerHTML = SIM_TECHS.map(([k, name]) => simNumInput("sim_atktech_" + k, name)).join("");
    defTech.innerHTML = SIM_TECHS.map(([k, name]) => simNumInput("sim_deftech_" + k, name)).join("");
    const btn = document.getElementById("btnRunSimulation");
    if (btn) btn.addEventListener("click", runSimulation);
}

function collectSimFleet(prefix, keys) {
    const out = {};
    keys.forEach(k => {
        const n = parseI(getVal(prefix + k), 0);
        if (n > 0) out[k] = n;
    });
    return out;
}

function collectSimTech(prefix) {
    const out = {};
    SIM_TECHS.forEach(([k]) => { out[k] = parseI(getVal(prefix + k), 0); });
    return out;
}

function runSimulation() {
    const statusEl = document.getElementById("sim_status");
    const resultsEl = document.getElementById("sim_results");
    const attacker = collectSimFleet("sim_atk_", SIM_ATTACK_SHIPS);
    if (!Object.keys(attacker).length) {
        showToast("Indica al menos una nave en tu flota de ataque", "danger");
        return;
    }
    const body = {
        attacker: attacker,
        attacker_tech: collectSimTech("sim_atktech_"),
        defender_fleet: collectSimFleet("sim_deffleet_", SIM_ATTACK_SHIPS),
        defender_defense: collectSimFleet("sim_defdef_", SIM_DEFENSES),
        defender_tech: collectSimTech("sim_deftech_"),
        runs: 30
    };
    if (statusEl) statusEl.textContent = "Simulando 30 combates…";
    const btn = document.getElementById("btnRunSimulation");
    if (btn) btn.disabled = true;
    fetch(api("/api/simulate"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                if (statusEl) statusEl.textContent = "Error: " + data.error;
                showToast("Simulador: " + data.error, "danger");
                return;
            }
            if (statusEl) statusEl.textContent = "";
            if (resultsEl) resultsEl.style.display = "block";
            const winPct = Math.round((data.win_rate || 0) * 100);
            setText("sim_win_rate", winPct + " %");
            setText("sim_losses", formatNumber(Math.round(data.avg_attacker_loss_value || 0)));
            const debris = data.avg_debris || {};
            setText("sim_debris_metal", formatNumber(Math.round(debris.metal || 0)));
            setText("sim_debris_crystal", formatNumber(Math.round(debris.crystal || 0)));
            const verdictEl = document.getElementById("sim_verdict");
            if (verdictEl) {
                if (data.verdict === "attack") {
                    verdictEl.className = "sim-verdict attack";
                    verdictEl.textContent = "✅ ATACA";
                } else {
                    verdictEl.className = "sim-verdict risky";
                    verdictEl.textContent = "⛔ NO RENTABLE / ARRIESGADO";
                }
            }
        })
        .catch(err => {
            if (statusEl) statusEl.textContent = "Error de conexión: " + err;
            showToast("Error al simular: " + err, "danger");
        })
        .finally(() => { if (btn) btn.disabled = false; });
}

// --------------------------------------------------------------------------
// Perfiles de riesgo (solo rellenan el formulario; el usuario debe Guardar)
// --------------------------------------------------------------------------
const RISK_PROFILES = {
    paranoid: {
        label: "Paranoico",
        attack_check_min_mins: 8, attack_check_max_mins: 15,
        cycle_interval_min_s: 1200, cycle_interval_max_s: 2400,
        max_actions_per_hour: 20, max_attack_targets_per_cycle: 3,
        min_action_delay_s: 5, max_action_delay_s: 15,
        farming_attack_cooldown_hours: 6
    },
    normal: {
        label: "Normal",
        attack_check_min_mins: 5, attack_check_max_mins: 13,
        cycle_interval_min_s: 600, cycle_interval_max_s: 1500,
        max_actions_per_hour: 40, max_attack_targets_per_cycle: 8,
        min_action_delay_s: 3, max_action_delay_s: 11,
        farming_attack_cooldown_hours: 2
    },
    aggressive: {
        label: "Agresivo",
        attack_check_min_mins: 3, attack_check_max_mins: 7,
        cycle_interval_min_s: 420, cycle_interval_max_s: 900,
        max_actions_per_hour: 60, max_attack_targets_per_cycle: 12,
        min_action_delay_s: 2, max_action_delay_s: 6,
        farming_attack_cooldown_hours: 1
    }
};

function updateRiskProfileBadge(profile) {
    const badge = document.getElementById("riskProfileBadge");
    if (!badge) return;
    const p = RISK_PROFILES[profile];
    badge.textContent = p ? p.label : profile;
    badge.className = "risk-profile-badge " + (RISK_PROFILES[profile] ? profile : "normal");
}

function applyRiskProfile(profile) {
    const p = RISK_PROFILES[profile];
    if (!p) return;
    ["attack_check_min_mins", "attack_check_max_mins", "cycle_interval_min_s", "cycle_interval_max_s",
     "max_actions_per_hour", "max_attack_targets_per_cycle", "min_action_delay_s", "max_action_delay_s",
     "farming_attack_cooldown_hours"].forEach(id => setVal(id, p[id]));
    globalConfig.risk_profile = profile;
    updateRiskProfileBadge(profile);
    showToast("Perfil '" + p.label + "' aplicado al formulario. Pulsa Guardar Cambios para confirmarlo.", "warning");
}

function initRiskProfiles() {
    const bindings = [["btnRiskParanoid", "paranoid"], ["btnRiskNormal", "normal"], ["btnRiskAggressive", "aggressive"]];
    bindings.forEach(([id, profile]) => {
        const btn = document.getElementById(id);
        if (btn) btn.addEventListener("click", () => applyRiskProfile(profile));
    });
}

// Inicialización de las nuevas pestañas y perfiles
window.addEventListener("DOMContentLoaded", () => {
    initSimulator();
    initRiskProfiles();
    loadHistory();
    setInterval(loadHistory, 60000);
});

// ==========================================================================
// ORION·OPS — Dashboard, agenda (C3), orden del ciclo (C4), control remoto (C1),
// estado del bot (C6), actividad horaria (C2) y filtros del registro
// ==========================================================================

function fmtHM(epoch) {
    const d = new Date((epoch || 0) * 1000);
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}

function gotoTab(tabId) {
    const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
    if (btn) btn.click();
}

// ------------------------------------------------------- estado del bot (C6)
function formatUptime(startedAt) {
    if (!startedAt) return "—";
    let s = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
    const d = Math.floor(s / 86400); s %= 86400;
    return (d ? d + "d " : "") + Math.floor(s / 3600) + "h " + String(Math.floor((s % 3600) / 60)).padStart(2, "0") + "m";
}

function loadBotStatus() {
    fetch(api("/api/botstatus"))
        .then(r => r.json())
        .then(d => {
            botStatusCache = d || {};
            const paused = (d.paused_until || 0) * 1000 > Date.now();
            const bp = document.getElementById("btnPauseBot");
            const br = document.getElementById("btnResumeBot");
            if (bp) bp.style.display = (d.running && !paused) ? "" : "none";
            if (br) br.style.display = paused ? "" : "none";
            const sb = document.getElementById("sb_state");
            if (sb) {
                sb.textContent = paused ? "PAUSADO" : (d.running ? "EN MARCHA" : "PARADO");
                sb.style.color = paused ? "var(--warn)" : (d.running ? "" : "var(--danger)");
            }
            setText("sb_session_time", d.running ? formatUptime(d.started_at) : "—");
            const player = document.getElementById("sb_player");
            if (player) player.textContent = d.player_name ? "· " + d.player_name : "";
        })
        .catch(() => {});
}

// -------------------------------------------------- control remoto (C1)
function postControl(cmd, arg) {
    return fetch(api("/api/control"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cmd, arg: arg === undefined ? null : arg })
    }).then(r => r.json());
}

function pollControlResult(id, cb, tries = 45) {
    fetch(api("/api/controlresult"))
        .then(r => r.json())
        .then(d => {
            const last = d && d.last;
            if (last && String(last.id) === String(id)) return cb(last);
            if (tries <= 0) return cb(null);
            setTimeout(() => pollControlResult(id, cb, tries - 1), 2000);
        })
        .catch(() => {
            if (tries <= 0) return cb(null);
            setTimeout(() => pollControlResult(id, cb, tries - 1), 2000);
        });
}

function manualStatus(msg, ok) {
    const el = document.getElementById("manualCmdStatus");
    if (!el) return;
    el.textContent = msg || "";
    el.style.color = ok === undefined ? "" : (ok ? "var(--ok)" : "var(--danger)");
}

function sendManualCommand(cmd, arg, pendingMsg) {
    manualStatus(pendingMsg || `Enviando «${cmd}»…`);
    postControl(cmd, arg)
        .then(d => {
            if (d.error) { manualStatus("Error: " + d.error, false); showToast(d.error, "danger"); return; }
            showToast("Comando encolado: el bot lo ejecutará en su próximo despertar", "success");
            pollControlResult(d.id, last => {
                if (!last) { manualStatus("El bot aún no ha respondido (el comando sigue encolado).", false); return; }
                manualStatus((last.ok ? "✅ " : "⚠ ") + (last.detail || cmd), !!last.ok);
                if (cmd === "screenshot" && last.ok && last.file) {
                    const url = api("/" + String(last.file).replace(/\\/g, "/").replace(/^\/+/, ""));
                    const el = document.getElementById("manualCmdStatus");
                    if (el) {
                        const a = document.createElement("a");
                        a.href = url; a.target = "_blank"; a.textContent = " abrir captura ↗";
                        a.style.color = "var(--cyan)";
                        el.appendChild(a);
                    }
                    window.open(url, "_blank");   // puede bloquearlo el navegador: queda el enlace
                }
                if (cmd === "pause" || cmd === "resume" || cmd === "close_browser") loadBotStatus();
            });
        })
        .catch(e => manualStatus("Error de red: " + e, false));
}

function initManualControls() {
    const bind = (id, fn) => { const b = document.getElementById(id); if (b) b.addEventListener("click", fn); };
    bind("btnTakeControl", () => {
        gotoTab("tab-live");
        const cont = document.getElementById("liveViewportContainer");
        if (cont) cont.scrollIntoView({ behavior: "smooth", block: "center" });
        refreshLiveScreenshot();
        showToast("Control manual: interactúa con el visor (clics y teclas reales al navegador del bot).", "warning");
    });
    bind("btnCmdScreenshot", () => sendManualCommand("screenshot", null, "Solicitando captura PNG…"));
    bind("btnCmdRestartSession", () => {
        if (!confirm("¿Reiniciar la sesión del bot? (cierra y reabre el navegador y vuelve a hacer login)")) return;
        sendManualCommand("restart_session", null, "Reiniciando sesión…");
    });
    bind("btnCmdCloseBrowser", () => {
        if (!confirm("¿Cerrar el navegador del bot? Quedará en pausa (12 h) hasta que pulses REANUDAR.")) return;
        sendManualCommand("close_browser", null, "Cerrando navegador…");
    });
    bind("btnPauseBot", () => sendManualCommand("pause", 60, "Pausando el bot (60 min)…"));
    bind("btnResumeBot", () => sendManualCommand("resume", null, "Reanudando el bot…"));
}

// -------------------------------------------------------- agenda del bot (C3)
const TASK_KINDS = ["farming", "espionaje", "expedicion", "construir", "investigacion",
    "transporte", "fleetsave", "reciclaje", "economia", "sistema"];
const TASK_STATUS_LABELS = { en_curso: "EN CURSO", pendiente: "PENDIENTE", programado: "PROGRAMADO" };

function loadAgenda() {
    fetch(api("/api/agenda"))
        .then(r => r.json())
        .then(d => {
            agendaCache = (d && Array.isArray(d.tasks)) ? d : { tasks: [] };
            renderAgenda();
            renderDashModules();
        })
        .catch(() => {});
}

function renderAgenda() {
    const list = document.getElementById("taskAgendaList");
    if (!list) return;
    const tasks = (agendaCache.tasks || []).slice().sort((a, b) => (a.when || 0) - (b.when || 0));
    const summary = document.getElementById("taskAgendaSummary");
    if (summary) {
        summary.textContent = tasks.length
            ? `${tasks.length} tareas · publicado ${agendaCache.generated_at ? fmtHM(agendaCache.generated_at) : "—"}`
            : "";
    }
    if (!tasks.length) {
        list.innerHTML = '<div class="text-muted empty-note">El bot aún no ha publicado su agenda (task_agenda.json). Inícialo para ver las próximas tareas.</div>';
        return;
    }
    list.innerHTML = "";
    tasks.forEach(t => {
        const kind = TASK_KINDS.includes(t.kind) ? t.kind : "sistema";
        const row = document.createElement("div");
        row.className = "task-row";
        const time = document.createElement("span");
        time.className = "task-time";
        time.textContent = t.when ? fmtHM(t.when) : "—";
        const bar = document.createElement("span");
        bar.className = "task-bar k-" + kind;
        const kindEl = document.createElement("span");
        kindEl.className = "task-kind k-" + kind;
        kindEl.textContent = kind;
        const main = document.createElement("div");
        main.className = "task-main";
        const title = document.createElement("div");
        title.className = "task-title";
        title.textContent = t.title || kind;
        const detail = document.createElement("div");
        detail.className = "task-detail";
        detail.textContent = (t.detail || "") + (t.loc ? ` · [${t.loc}]` : "");
        main.appendChild(title);
        main.appendChild(detail);
        const status = document.createElement("span");
        status.className = "task-status" + (t.status === "en_curso" ? " en_curso" : "");
        status.textContent = TASK_STATUS_LABELS[t.status] || (t.status || "—").toUpperCase();
        row.appendChild(time); row.appendChild(bar); row.appendChild(kindEl);
        row.appendChild(main); row.appendChild(status);
        list.appendChild(row);
    });
}

// -------------------------------------------------- plan del modo automático
function loadAutoPlan() {
    fetch(api("/api/autoplan"))
        .then(r => r.json())
        .then(d => renderAutoPlan(d || {}))
        .catch(() => {});
}

function renderAutoPlan(plan) {
    const list = document.getElementById("autoPlanList");
    if (!list) return;
    const summary = document.getElementById("autoPlanSummary");
    const planets = plan.planets || [];
    const research = plan.research || [];
    const fleet = plan.fleet || [];
    if (summary) {
        summary.textContent = plan.generated_at ? `publicado ${fmtHM(plan.generated_at)}` : "";
    }
    if (!planets.length && !research.length && !fleet.length) {
        list.innerHTML = '<div class="text-muted empty-note">El bot aún no ha publicado su plan (auto_plan.json). Inícialo para verlo.</div>';
        return;
    }
    list.innerHTML = "";
    const card = (title, items, emptyText) => {
        const c = document.createElement("div");
        c.className = "card cfg-card";
        const head = document.createElement("div");
        head.className = "cfg-head";
        head.innerHTML = `<span>${title}</span>`;
        c.appendChild(head);
        if (!items.length) {
            const em = document.createElement("div");
            em.className = "text-muted sm";
            em.textContent = emptyText;
            c.appendChild(em);
        } else {
            const ol = document.createElement("ol");
            ol.className = "auto-plan-steps";
            items.forEach(txt => {
                const li = document.createElement("li");
                li.textContent = txt;
                ol.appendChild(li);
            });
            c.appendChild(ol);
        }
        list.appendChild(c);
    };
    planets.forEach(p => {
        const steps = (p.steps || []).map(s =>
            `Subir ${BUILDING_TRANSLATIONS[s.action] || s.action} al ${s.level}`);
        card(`${p.name || "Planeta"} [${p.coords}]`, steps,
             "Nada rentable que construir con la lógica actual (objetivos alcanzados o amortización alta).");
    });
    const rSteps = research.map(r => {
        const name = (TECH_NAMES[r.tech] || r.tech).replace(/\s*\(.*\)$/, "");
        return r.blocked_lab !== undefined
            ? `Investigar ${name} al ${r.level} (esperando laboratorio ${r.blocked_lab})`
            : `Investigar ${name} al ${r.level}`;
    });
    card("Investigación", rSteps, "Sin investigaciones pendientes según la lógica actual.");
    if (fleet.length) {
        card("Flota (auto-gestión)", fleet.map(f =>
            `Fabricar ${SHIP_TRANSLATIONS[f.ship] || f.ship}: ${f.have} de ${f.target}`),
            "");
    }
}

// -------------------------------------------------- orden del ciclo (C4)
const DEFAULT_CYCLE_ORDER = ["economy", "recycling", "expeditions", "farming", "feed"];

function currentCycleOrder() {
    return Array.from(document.querySelectorAll("#cycleOrderList .cycle-chip"))
        .map(li => li.dataset.cycle)
        .filter(k => DEFAULT_CYCLE_ORDER.includes(k));
}

function applyCycleOrderToChips(order) {
    const list = document.getElementById("cycleOrderList");
    if (!list) return;
    const seq = (Array.isArray(order) && order.length ? order : DEFAULT_CYCLE_ORDER)
        .filter(k => DEFAULT_CYCLE_ORDER.includes(k));
    DEFAULT_CYCLE_ORDER.forEach(k => { if (!seq.includes(k)) seq.push(k); });
    seq.forEach(k => {
        const li = list.querySelector(`.cycle-chip[data-cycle="${k}"]`);
        if (li) list.appendChild(li);
    });
}

let cycleDragEl = null;
function initCycleOrder() {
    const list = document.getElementById("cycleOrderList");
    if (!list) return;
    list.querySelectorAll(".cycle-chip").forEach(chip => {
        chip.addEventListener("dragstart", e => {
            cycleDragEl = chip;
            chip.classList.add("dragging");
            e.dataTransfer.effectAllowed = "move";
            try { e.dataTransfer.setData("text/plain", chip.dataset.cycle); } catch (_) { /* IE */ }
        });
        chip.addEventListener("dragend", () => {
            chip.classList.remove("dragging");
            list.querySelectorAll(".cycle-chip").forEach(c => c.classList.remove("drag-over"));
        });
        chip.addEventListener("dragover", e => {
            e.preventDefault();
            if (chip !== cycleDragEl) chip.classList.add("drag-over");
        });
        chip.addEventListener("dragleave", () => chip.classList.remove("drag-over"));
        chip.addEventListener("drop", e => {
            e.preventDefault();
            chip.classList.remove("drag-over");
            if (!cycleDragEl || cycleDragEl === chip) return;
            const chips = Array.from(list.children);
            if (chips.indexOf(cycleDragEl) < chips.indexOf(chip)) chip.after(cycleDragEl);
            else chip.before(cycleDragEl);
            saveCycleOrder();
        });
    });
}

function saveCycleOrder() {
    const order = currentCycleOrder();
    if (order.length !== DEFAULT_CYCLE_ORDER.length) return;
    const status = document.getElementById("cycleOrderStatus");
    if (status) status.textContent = "guardando…";
    fetch(api("/api/config"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cycle_order: order })
    })
        .then(r => r.json())
        .then(d => {
            if (d.status === "success") {
                globalConfig.cycle_order = order.slice();
                if (configSnapshot) configSnapshot.cycle_order = order.slice();
                if (uiBaseline) uiBaseline.cycle_order = order.slice();
                if (status) {
                    status.textContent = "✔ guardado";
                    setTimeout(() => { if (status.textContent === "✔ guardado") status.textContent = ""; }, 3000);
                }
                showToast("Orden del ciclo guardado", "success");
            } else {
                if (status) status.textContent = "error al guardar";
                showToast("Error al guardar el orden: " + (d.error || "?"), "danger");
            }
        })
        .catch(e => {
            if (status) status.textContent = "error de red";
            showToast("Error de red: " + e, "danger");
        });
}

// ------------------------------------------- actividad por hora (C2) + KPIs
function loadHourly() {
    fetch(api("/api/hourly"))
        .then(r => r.json())
        .then(d => {
            hourlyCache = (d && Array.isArray(d.hourly)) ? d.hourly : [];
            drawActivityChart();
            if (dashRange === "24h") renderHistoryCharts();
            updateDashKPIs();
        })
        .catch(() => {});
}

function drawActivityChart() {
    const canvas = document.getElementById("chart_activity");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    const entries = hourlyCache.slice(-24);
    const caption = document.getElementById("chart_activity_caption");
    if (!entries.length) {
        if (caption) caption.textContent = "sin datos todavía";
        return;
    }
    const padB = 16, padT = 6;
    const maxV = Math.max(1, ...entries.map(e => e.actions || 0));
    const maxIdx = entries.reduce((mi, e, i) => (e.actions || 0) > (entries[mi].actions || 0) ? i : mi, 0);
    const n = entries.length;
    const slot = W / n;
    const barW = Math.max(3, slot * 0.62);
    entries.forEach((e, i) => {
        const v = e.actions || 0;
        const h = Math.round((v / maxV) * (H - padT - padB));
        ctx.fillStyle = i === maxIdx && v > 0 ? cssColor("--warn", "#ffb547") : "rgba(76,217,255,0.55)";
        ctx.fillRect(i * slot + (slot - barW) / 2, H - padB - Math.max(h, 1), barW, Math.max(h, 1));
    });
    const hourOf = e => String(new Date((e.ts || 0) * 1000).getHours()).padStart(2, "0") + "h";
    ctx.font = "9px 'IBM Plex Mono', monospace";
    ctx.fillStyle = cssColor("--tx-3", "#5b6b8c");
    ctx.textAlign = "left"; ctx.fillText(hourOf(entries[0]), 2, H - 4);
    ctx.textAlign = "right"; ctx.fillText(hourOf(entries[n - 1]), W - 2, H - 4);
    if (caption) caption.textContent = `máx ${entries[maxIdx].actions || 0} acciones a las ${hourOf(entries[maxIdx])} · últimas ${n} h`;
}

// ------------------------------------------------ módulos del bot (dashboard)
const DASH_MODULES = [
    ["enable_farming", "Farmeo de inactivos", "farming"],
    ["enable_expeditions", "Expediciones", "expedicion"],
    ["enable_economy", "Economía · construcción", "economia"],
    ["enable_research", "Investigación", "investigacion"],
    ["enable_defense", "Defensa", null],
    ["enable_recycling", "Reciclaje", "reciclaje"],
    ["enable_fleetsave", "Fleetsave", "fleetsave"],
    ["enable_spy_watch", "Vigilancia de espionaje", "espionaje"]
];

function moduleNextDetail(kind) {
    if (!kind) return "";
    const now = Date.now() / 1000;
    const t = (agendaCache.tasks || [])
        .filter(x => x.kind === kind && (x.when || 0) >= now - 60)
        .sort((a, b) => (a.when || 0) - (b.when || 0))[0];
    if (!t) return "";
    return t.status === "en_curso" ? "en curso · " + (t.title || "") : `próx. ${fmtHM(t.when)} · ${t.title || ""}`;
}

function renderDashModules() {
    const c = document.getElementById("dashModulesList");
    if (!c) return;
    if (!configLoaded) {
        c.innerHTML = '<div class="text-muted empty-note">Cargando módulos…</div>';
        return;
    }
    c.innerHTML = "";
    let active = 0;
    DASH_MODULES.forEach(([key, title, kind]) => {
        // enable_spy_watch es "activo por defecto" (mismo criterio que mapConfigToUI)
        const on = key === "enable_spy_watch" ? globalConfig[key] !== false : !!globalConfig[key];
        if (on) active++;
        const row = document.createElement("div");
        row.className = "dash-module-row" + (on ? "" : " off");
        const main = document.createElement("div");
        main.className = "dm-main";
        const t = document.createElement("div");
        t.className = "dm-title";
        t.textContent = title;
        const d = document.createElement("div");
        d.className = "dm-detail";
        d.textContent = on ? (moduleNextDetail(kind) || "activo") : "desactivado";
        main.appendChild(t); main.appendChild(d);
        const sw = document.createElement("label");
        sw.className = "switch-container";
        const chk = document.createElement("input");
        chk.type = "checkbox";
        chk.checked = on;
        const slider = document.createElement("span");
        slider.className = "slider";
        sw.appendChild(chk); sw.appendChild(slider);
        chk.addEventListener("change", () => saveModuleToggle(key, chk.checked, title));
        row.appendChild(main); row.appendChild(sw);
        c.appendChild(row);
    });
    const act = document.getElementById("dashModulesActive");
    if (act) act.textContent = `${active}/${DASH_MODULES.length} activos`;
}

// POST /api/config SOLO con esa clave (el backend hace merge)
function saveModuleToggle(key, value, title) {
    fetch(api("/api/config"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: value })
    })
        .then(r => r.json())
        .then(d => {
            if (d.status === "success") {
                globalConfig[key] = value;
                if (configSnapshot) configSnapshot[key] = value;
                if (uiBaseline) uiBaseline[key] = value;
                setCheck(key, value);   // sincroniza el checkbox de la pantalla Configuración
                showToast(`${title}: ${value ? "activado" : "desactivado"}`, "success");
            } else {
                showToast("Error al guardar: " + (d.error || "?"), "danger");
            }
            renderDashModules();
        })
        .catch(e => { showToast("Error de red: " + e, "danger"); renderDashModules(); });
}

// ------------------------------------------- desglose de expediciones (C8)
const EXP_OUTCOME_LABELS = {
    resources: "Recursos", ships: "Naves", items: "Objetos",
    dark_matter: "Materia oscura", nothing: "Nada"
};

function renderExpOutcomes() {
    const barEl = document.getElementById("expOutcomeBar");
    const legEl = document.getElementById("expOutcomeLegend");
    const totEl = document.getElementById("expOutcomeTotal");
    if (!barEl || !legEl || !totEl) return;
    const eo = statsCache.expe_outcomes || {};
    const keys = Object.keys(EXP_OUTCOME_LABELS);
    const total = keys.reduce((s, k) => s + (eo[k] || 0), 0);
    totEl.textContent = total ? `${formatNumber(total)} exped.` : "—";
    if (!total) {
        barEl.innerHTML = "";
        legEl.innerHTML = '<div class="text-muted empty-note">Sin expediciones registradas.</div>';
        return;
    }
    barEl.innerHTML = "";
    legEl.innerHTML = "";
    keys.forEach(k => {
        const v = eo[k] || 0;
        if (v > 0) {
            const seg = document.createElement("div");
            seg.className = "seg-" + k;
            seg.style.width = (v / total * 100) + "%";
            seg.title = `${EXP_OUTCOME_LABELS[k]}: ${v}`;
            barEl.appendChild(seg);
        }
        const row = document.createElement("div");
        const left = document.createElement("span");
        const dot = document.createElement("span");
        dot.className = "seg-" + k;
        dot.style.cssText = "display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;";
        left.appendChild(dot);
        left.appendChild(document.createTextNode(EXP_OUTCOME_LABELS[k]));
        const right = document.createElement("span");
        right.textContent = `${v} · ${Math.round(v / total * 100)}%`;
        row.appendChild(left); row.appendChild(right);
        legEl.appendChild(row);
    });
}

// --------------------------------------------------------- KPIs del dashboard
function updateDashKPIs() {
    // Recursos totales: suma de /api/planets si algún día trae recursos;
    // hoy no los trae, así que se usa el último snapshot horario del imperio (C2).
    let m = null, c = null, d = null;
    if (planetsCache.some(p => p.resources && typeof p.resources.metal === "number")) {
        m = c = d = 0;
        planetsCache.forEach(p => {
            const r = p.resources || {};
            m += r.metal || 0; c += r.crystal || 0; d += r.deut || 0;
        });
    } else if (hourlyCache.length) {
        const last = hourlyCache[hourlyCache.length - 1];
        m = last.metal; c = last.crystal; d = last.deut;
    }
    setText("kpi_metal", typeof m === "number" ? formatNumber(m) : "—");
    setText("kpi_crystal", typeof c === "number" ? formatNumber(c) : "—");
    setText("kpi_deut", typeof d === "number" ? formatNumber(d) : "—");

    // Puntos · ranking (C5) + variación semanal contra la línea de hace 7 días
    const pts = statsCache.player_points || 0;
    const rank = statsCache.player_rank || 0;
    setText("kpi_points", pts ? formatNumber(pts) : "—");
    let rankTxt = rank ? "#" + formatNumber(rank) : "—";
    if (pts) {
        const weekAgo = Date.now() / 1000 - 7 * 86400;
        const ref = historyCache.filter(e => typeof e.points === "number" && (e.ts || 0) <= weekAgo).pop();
        if (ref) {
            const delta = pts - ref.points;
            rankTxt += ` · ${delta >= 0 ? "+" : "−"}${formatShortNumber(Math.abs(delta))} pts/7d`;
        }
    }
    setText("kpi_rank", rankTxt);

    // Botín 24h: farmeo de sesión menos el corte del último punto diario del histórico;
    // si no hay histórico (o la sesión se reinició), totales de la sesión.
    const farm = statsCache.total_farming || {};
    const lastHist = historyCache[historyCache.length - 1];
    const per = ["metal", "crystal", "deut"].map(k => {
        const total = farm[k] || 0;
        const v = total - (lastHist ? (lastHist["farm_" + k] || 0) : 0);
        return (lastHist && v >= 0) ? v : total;
    });
    setText("kpi_loot24", formatNumber(per[0] + per[1] + per[2]));
    setText("kpi_loot24_detail", `M ${formatShortNumber(per[0])} · C ${formatShortNumber(per[1])} · D ${formatShortNumber(per[2])}`);

    // Flotas en vuelo (propias); como total se usan los slots de expedición si el bot los publica
    const own = flightsCache.filter(f => !f.is_hostile).length;
    setText("kpi_fleet_flying", String(own));
    const slots = expeStatusCache.total_expe_slots || 0;
    const slotsEl = document.getElementById("kpi_fleet_slots");
    if (slotsEl) slotsEl.textContent = slots ? `/${slots}` : "";
    const bar = document.getElementById("kpi_fleet_bar");
    if (bar) bar.style.width = Math.min(100, slots ? Math.round(own / slots * 100) : own * 10) + "%";
}

// ------------------------------------------------ banner de ataque (dashboard)
function updateDashAlert() {
    const banner = document.getElementById("dashAlertBanner");
    if (!banner) return;
    const nowServer = Date.now() / 1000 + flightsServerOffset;
    const hostiles = flightsCache
        .filter(f => f.is_hostile && (!f.arrival_epoch || f.arrival_epoch > nowServer))
        .sort((a, b) => (a.arrival_epoch || Infinity) - (b.arrival_epoch || Infinity));
    if (!hostiles.length) { banner.style.display = "none"; return; }
    banner.style.display = "";
    const f = hostiles[0];
    const eta = f.arrival_epoch ? expFmtETA(f.arrival_epoch - nowServer) : "—";
    setText("dashAlertText",
        `ATAQUE ENTRANTE → [${f.destination || "?"}] · impacto en ${eta}` +
        (hostiles.length > 1 ? ` (+${hostiles.length - 1} más)` : ""));
}

// ----------------------------------------------------- pantalla Planetas
function updatePlanetsSummary() {
    const el = document.getElementById("planetsSummary");
    if (!el) return;
    const moons = planetsCache.filter(p => p.has_moon || (p.moon && p.moon.coords)).length;
    el.textContent = planetsCache.length ? `${planetsCache.length} planetas · ${moons} lunas` : "";
}

// Línea de cola por tarjeta desde /api/buildstatus
function updatePlanetQueueLines() {
    const lines = document.querySelectorAll(".planet-queue-line");
    if (!lines.length) return;
    const byCoords = {};
    ((buildStatusCache && buildStatusCache.planets) || []).forEach(p => { byCoords[p.coords] = p; });
    const now = Date.now() / 1000;
    lines.forEach(el => {
        const p = byCoords[el.dataset.coords];
        if (!p || !p.finish_epoch || p.finish_epoch <= now) {
            el.textContent = "⏳ cola libre";
            return;
        }
        const q = (p.queue && p.queue.length) ? p.queue.join(", ") : "construcción";
        el.textContent = `⏳ ${q} — ${expFmtETA(p.finish_epoch - now)}`;
    });
}

// Producción/h del imperio ESTIMADA desde los niveles de minas registrados
// (/api/planets no publica producción real) × velocidad del universo.
function renderEmpireProduction() {
    if (!document.getElementById("prod_metal_h") || !planetsCache.length) return;
    const speed = parseF(globalConfig.universe_speed, 1) || 1;
    let pm = 0, pc = 0, pd = 0, eProd = 0, eCons = 0;
    planetsCache.forEach(p => {
        const b = p.buildings || {};
        const lm = b.metal_mine || 0, lc = b.crystal_mine || 0, ld = b.deut_synth || 0;
        const ls = b.solar_plant || 0, lf = b.fusion_reactor || 0;
        pm += (30 * lm * Math.pow(1.1, lm) + 30) * speed;
        pc += (20 * lc * Math.pow(1.1, lc) + 15) * speed;
        pd += 10 * ld * Math.pow(1.1, ld) * 1.36 * speed;
        eProd += 20 * ls * Math.pow(1.1, ls) + 30 * lf * Math.pow(1.05, lf);
        eCons += 10 * lm * Math.pow(1.1, lm) + 10 * lc * Math.pow(1.1, lc) + 20 * ld * Math.pow(1.1, ld);
    });
    setText("prod_metal_h", "≈" + formatShortNumber(pm));
    setText("prod_crystal_h", "≈" + formatShortNumber(pc));
    setText("prod_deut_h", "≈" + formatShortNumber(pd));
    const bal = Math.round(eProd - eCons);
    const bar = document.getElementById("prod_energy_bar");
    if (bar) {
        bar.style.width = ((eProd + eCons) > 0 ? Math.round(eProd / (eProd + eCons) * 100) : 0) + "%";
        bar.className = "meter-fill " + (bal >= 0 ? "ok" : "warn");
    }
    const lbl = document.getElementById("prod_energy_label");
    if (lbl) {
        lbl.textContent = (bal >= 0 ? "+" : "") + formatShortNumber(bal);
        lbl.style.color = bal >= 0 ? "" : "var(--warn)";
    }
}

// ------------------------------------- contador de "cambios sin guardar"
let dirtyTimer = null;

function configDiffCount() {
    if (!configLoaded || !configSnapshot) return 0;
    collectUIIntoConfig();
    const base = uiBaseline || configSnapshot;
    let n = 0;
    Object.keys(globalConfig).forEach(k => {
        if (JSON.stringify(globalConfig[k]) !== JSON.stringify(base[k])) n++;
    });
    return n;
}

function updateDirtyBadge() {
    const badge = document.getElementById("cfgDirtyBadge");
    if (!badge) return;
    const n = configDiffCount();
    badge.style.display = n ? "" : "none";
    badge.textContent = n === 1 ? "1 cambio sin guardar" : `${n} cambios sin guardar`;
}

function scheduleDirtyCheck() {
    clearTimeout(dirtyTimer);
    dirtyTimer = setTimeout(updateDirtyBadge, 500);
}

function initDirtyTracking() {
    ["tab-config", "tab-planets"].forEach(id => {
        const pane = document.getElementById(id);
        if (!pane) return;
        pane.addEventListener("input", scheduleDirtyCheck);
        pane.addEventListener("change", scheduleDirtyCheck);
    });
    const restore = document.getElementById("btnRestoreCfg");
    if (restore) restore.addEventListener("click", () => {
        queueDrafts = {};
        configLoaded = false;   // fuerza re-render de tarjetas con la config del servidor
        loadConfig();
        showToast("Configuración recargada desde el servidor (cambios descartados)", "success");
    });
}

// --------------------------------------------------- filtros del registro
function initLogFilters() {
    const bar = document.getElementById("logFilterBar");
    if (bar) {
        bar.querySelectorAll(".log-filter").forEach(btn => {
            btn.addEventListener("click", () => {
                bar.querySelectorAll(".log-filter").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                logLevelFilter = btn.dataset.level || "all";
                applyLogFilters();
            });
        });
    }
    const modSel = document.getElementById("logModuleFilter");
    if (modSel) {
        LOG_MODULES.forEach(mod => {
            const o = document.createElement("option");
            o.value = mod;
            o.textContent = "Módulo: " + mod;
            modSel.appendChild(o);
        });
        modSel.addEventListener("change", applyLogFilters);
    }
    const search = document.getElementById("logSearch");
    if (search) {
        let searchTimer = null;
        search.addEventListener("input", () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(applyLogFilters, 250);
        });
    }
    const exp = document.getElementById("btnExportLog");
    if (exp) exp.addEventListener("click", exportLogCSV);
}

// ------------------------------------------------- cableado del dashboard
function initDashboardExtras() {
    document.querySelectorAll(".range-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".range-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            dashRange = btn.dataset.range || "7d";
            renderHistoryCharts();
        });
    });
    document.querySelectorAll(".link-goto").forEach(btn => {
        btn.addEventListener("click", () => gotoTab(btn.dataset.goto));
    });
    const alertLink = document.getElementById("dashAlertLink");
    if (alertLink) alertLink.addEventListener("click", e => { e.preventDefault(); gotoTab("tab-flights"); });
    const savePlanets = document.getElementById("btnSavePlanets");
    if (savePlanets) savePlanets.addEventListener("click", () => btnSave.click());
    // Redibujar las gráficas al entrar en el Dashboard (antes se hacía en tab-history)
    const dashBtn = document.querySelector('.tab-btn[data-tab="tab-dashboard"]');
    if (dashBtn) dashBtn.addEventListener("click", () => { renderHistoryCharts(); drawActivityChart(); });
}

window.addEventListener("DOMContentLoaded", () => {
    initDashboardExtras();
    initManualControls();
    initCycleOrder();
    initLogFilters();
    initDirtyTracking();
    loadAgenda();
    loadAutoPlan();
    loadHourly();
    loadBotStatus();
    setInterval(loadBotStatus, 10000);   // poll del estado (C6)
    setInterval(loadAgenda, 15000);      // agenda del bot (C3)
    setInterval(loadAutoPlan, 15000);    // plan del modo automático
    setInterval(loadHourly, 60000);      // actividad/recursos por hora (C2)
    setInterval(updateDashAlert, 1000);  // cuenta atrás del banner de ataque
    setInterval(updateDashKPIs, 5000);   // KPIs (vuelos aterrizando, etc.)
});
