// ==========================================================================
// OGBot Dashboard — Logic & Frontend API client
// ==========================================================================

let globalConfig = {};
let planetsCache = [];
let fleetInMotionCache = {};   // naves en vuelo (flotas en movimiento)
let localPlanetsConfig = {};
let researchPriorityList = [];
let configLoaded = false;
let currentAccount = localStorage.getItem("ogbot_account") || "";
let accountsCache = [];

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
    messagesCache = [];
    lastMessagesSig = "";
    renderAccountSelect();
    renderAccountsTab();
    loadConfig();
    loadPlanets();
    loadStats();
    loadMessages();
    loadLogs();
    checkBotStatus();
    loadExpeditionStatus();
    loadBuildStatus();
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
        loadExpeditionStatus();
        loadBuildStatus();
    });
    const msgFilter = document.getElementById("messages_filter");
    if (msgFilter) msgFilter.addEventListener("change", renderMessages);
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
            localPlanetsConfig = data.planets_config || {};
            mapConfigToUI(data);
            if (!configLoaded) {
                configLoaded = true;
                forceRerenderPlanets();
            }
        })
        .catch(err => showToast("Error de conexión: " + err, "danger"));
}

function mapConfigToUI(cfg) {
    // Globales
    setVal("universe", cfg.universe);
    setVal("country", cfg.country);
    setVal("server_url", cfg.server_url);
    setVal("username", cfg.username || "");
    setVal("password", cfg.password || "");
    
    if (cfg.active_hours && cfg.active_hours.length === 2) {
        setVal("active_hours_start", cfg.active_hours[0]);
        setVal("active_hours_end", cfg.active_hours[1]);
    }

    setCheck("dry_run", cfg.dry_run);
    setCheck("headless", cfg.headless);
    setCheck("enable_attack_escape", cfg.enable_attack_escape);
    setVal("attack_check_min_mins", Math.round((cfg.attack_check_interval_min_s !== undefined ? cfg.attack_check_interval_min_s : 300) / 60));
    setVal("attack_check_max_mins", Math.round((cfg.attack_check_interval_max_s !== undefined ? cfg.attack_check_interval_max_s : 780) / 60));
    setCheck("enable_spy_watch", cfg.enable_spy_watch !== false);
    setVal("spy_watch_cooldown_mins", cfg.spy_watch_cooldown_mins !== undefined ? cfg.spy_watch_cooldown_mins : 30);
    setCheck("spy_watch_messages", cfg.spy_watch_messages !== false);
    setCheck("enable_fleetsave", cfg.enable_fleetsave);
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
    setCheck("enable_expeditions", cfg.enable_expeditions);
    setVal("expeditions_run_interval_mins", cfg.expeditions_run_interval_mins !== undefined ? cfg.expeditions_run_interval_mins : 0);
    setCheck("expedition_auto_ships", cfg.expedition_auto_ships);
    setCheck("expedition_smart_schedule", cfg.expedition_smart_schedule !== false);
    setCheck("expedition_rotate_systems", cfg.expedition_rotate_systems !== false);
    setCheck("expedition_use_pathfinder", cfg.expedition_use_pathfinder);
    setCheck("expedition_send_probe", cfg.expedition_send_probe);
    setVal("expedition_probe_count", cfg.expedition_probe_count !== undefined ? cfg.expedition_probe_count : 1);
    setCheck("expedition_discoverer_class", cfg.expedition_discoverer_class);
    setVal("expedition_cargo_ship", cfg.expedition_cargo_ship || "large_cargo");
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
}

function saveChanges() {
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
    globalConfig.enable_attack_escape = getCheck("enable_attack_escape");
    globalConfig.attack_check_interval_min_s = (parseInt(getVal("attack_check_min_mins")) || 5) * 60;
    globalConfig.attack_check_interval_max_s = (parseInt(getVal("attack_check_max_mins")) || 13) * 60;
    globalConfig.enable_spy_watch = getCheck("enable_spy_watch");
    globalConfig.spy_watch_cooldown_mins = parseInt(getVal("spy_watch_cooldown_mins")) || 30;
    globalConfig.spy_watch_messages = getCheck("spy_watch_messages");
    globalConfig.enable_fleetsave = getCheck("enable_fleetsave");
    globalConfig.fleetsave_mission = getVal("fleetsave_mission");
    globalConfig.fleetsave_carry_resources = getCheck("fleetsave_carry_resources");
    globalConfig.fleetsave_recall_halfway = getCheck("fleetsave_recall_halfway");
    globalConfig.fleetsave_prefer_moon = getCheck("fleetsave_prefer_moon");
    globalConfig.enable_night_sweep = getCheck("enable_night_sweep");
    let _nsi = parseFloat(getVal("night_sweep_interval_hours"));
    globalConfig.night_sweep_interval_hours = (isNaN(_nsi) || _nsi <= 0) ? 2.0 : _nsi;
    globalConfig.telegram_token = getVal("telegram_token");
    globalConfig.telegram_chat_id = getVal("telegram_chat_id");

    globalConfig.enable_economy = getCheck("enable_economy");
    globalConfig.economy_run_interval_mins = parseInt(getVal("economy_run_interval_mins")) || 0;
    globalConfig.enable_research = getCheck("enable_research");
    globalConfig.enable_facilities = getCheck("enable_facilities");
    globalConfig.enable_farming = getCheck("enable_farming");
    globalConfig.farming_run_interval_mins = parseInt(getVal("farming_run_interval_mins")) || 0;
    globalConfig.enable_fleet_building = getCheck("enable_fleet_building");
    globalConfig.enable_expeditions = getCheck("enable_expeditions");
    globalConfig.expeditions_run_interval_mins = parseInt(getVal("expeditions_run_interval_mins")) || 0;
    globalConfig.expedition_auto_ships = getCheck("expedition_auto_ships");
    globalConfig.expedition_smart_schedule = getCheck("expedition_smart_schedule");
    globalConfig.expedition_rotate_systems = getCheck("expedition_rotate_systems");
    globalConfig.expedition_use_pathfinder = getCheck("expedition_use_pathfinder");
    globalConfig.expedition_send_probe = getCheck("expedition_send_probe");
    globalConfig.expedition_probe_count = parseInt(getVal("expedition_probe_count")) || 1;
    globalConfig.expedition_discoverer_class = getCheck("expedition_discoverer_class");
    globalConfig.expedition_cargo_ship = getVal("expedition_cargo_ship") || "large_cargo";
    globalConfig.expedition_top1_points = parseInt(getVal("expedition_top1_points")) || 0;
    globalConfig.expedition_find_safety = parseFloat(getVal("expedition_find_safety")) || 1.0;
    globalConfig.expedition_min_cargo = parseInt(getVal("expedition_min_cargo")) || 1;
    globalConfig.expedition_max_cargo = parseInt(getVal("expedition_max_cargo")) || 0;
    let _expHold = parseFloat(getVal("expedition_hold_hours"));
    globalConfig.expedition_hold_hours = isNaN(_expHold) ? 1.0 : _expHold;
    let _expRange = parseInt(getVal("expedition_system_range"));
    globalConfig.expedition_system_range = isNaN(_expRange) ? 15 : _expRange;
    globalConfig.expedition_position = parseInt(getVal("expedition_position")) || 16;
    globalConfig.enable_recycling = getCheck("enable_recycling");
    globalConfig.recycling_run_interval_mins = parseInt(getVal("recycling_run_interval_mins")) || 0;
    globalConfig.enable_defense = getCheck("enable_defense");
    globalConfig.enable_lifeforms = getCheck("enable_lifeforms");
    globalConfig.enable_colonization = getCheck("enable_colonization");
    globalConfig.only_inactive_targets = getCheck("only_inactive_targets");
    globalConfig.max_attack_targets_per_cycle = parseInt(getVal("max_attack_targets_per_cycle")) || 8;
    globalConfig.min_loot_value = parseInt(getVal("min_loot_value")) || 50000;

    // Guardar objetivos de flota
    globalConfig.fleet_targets = {
        large_cargo: parseInt(getVal("target_large_cargo")) || 0,
        small_cargo: parseInt(getVal("target_small_cargo")) || 0,
        recycler: parseInt(getVal("target_recycler")) || 0,
        espionage_probe: parseInt(getVal("target_espionage_probe")) || 0,
        light_fighter: parseInt(getVal("target_light_fighter")) || 0,
        cruiser: parseInt(getVal("target_cruiser")) || 0,
        battleship: parseInt(getVal("target_battleship")) || 0
    };

    // Guardar plantilla de flota de ataque (farmeo)
    globalConfig.attacker_fleet_template = {
        small_cargo: parseInt(getVal("temp_small_cargo")) || 0,
        large_cargo: parseInt(getVal("temp_large_cargo")) || 0,
        light_fighter: parseInt(getVal("temp_light_fighter")) || 0,
        cruiser: parseInt(getVal("temp_cruiser")) || 0
    };

    // Guardar límites de investigación
    globalConfig.research_caps = {
        energy_tech: parseInt(getVal("cap_energy_tech")) || 0,
        laser_tech: parseInt(getVal("cap_laser_tech")) || 0,
        ion_tech: parseInt(getVal("cap_ion_tech")) || 0,
        hyperspace_tech: parseInt(getVal("cap_hyperspace_tech")) || 0
    };

    // Guardar flota de expedición (dinámico)
    const expShips = {};
    const rows = document.querySelectorAll(".exp-ship-row");
    rows.forEach(row => {
        const shipType = row.dataset.shipType;
        const qtyInput = row.querySelector(".exp-ship-qty");
        const qty = parseInt(qtyInput.value) || 0;
        if (qty > 0) {
            expShips[shipType] = qty;
        }
    });
    globalConfig.expedition_ships = expShips;

    // Guardar objetivos de minas
    globalConfig.target_metal_mine = parseInt(getVal("target_metal_mine")) || 99;
    globalConfig.target_crystal_mine = parseInt(getVal("target_crystal_mine")) || 99;
    globalConfig.target_deut_synth = parseInt(getVal("target_deut_synth")) || 99;
    globalConfig.target_mine_ratio_payback_hours = parseInt(getVal("target_mine_ratio_payback_hours")) || 30;

    // Guardar objetivos de defensas
    const parseI = (val, def) => {
        const parsed = parseInt(val);
        return isNaN(parsed) ? def : parsed;
    };

    globalConfig.defense_batch_size = parseI(getVal("defense_batch_size"), 25);
    globalConfig.recycling_min_debris = parseInt(getVal("recycling_min_debris")) || 8000;

    // Guardar prioridades de investigación
    globalConfig.research_priority = researchPriorityList;

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

    // Guardar
    fetch(api("/api/config"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(globalConfig)
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === "success") {
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

        const card = document.createElement("div");
        card.className = "planet-card";
        card.dataset.coords = coords;
        
        card.innerHTML = `
            <div class="planet-card-header">
                <div class="planet-name-wrapper">
                    <span class="planet-icon">🪐</span>
                    <span class="planet-title">${p.name}</span>
                    <span class="planet-coords-tag">[${coords}]</span>
                </div>
            </div>
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
                <label class="planet-toggle">
                    <input type="checkbox" class="planet-expeditions" ${isExpeditions ? "checked" : ""}>
                    <span>Expediciones</span>
                </label>
                <label class="planet-toggle">
                    <input type="checkbox" class="planet-fleet-building" ${isFleet ? "checked" : ""}>
                    <span>Crear Flota</span>
                </label>
                <label class="planet-toggle">
                    <input type="checkbox" class="planet-farming" ${isFarming ? "checked" : ""}>
                    <span>Farmeo</span>
                </label>
                <label class="planet-toggle">
                    <input type="checkbox" class="planet-recycling" ${isRecycling ? "checked" : ""}>
                    <span>Reciclaje</span>
                </label>
                <label class="planet-toggle">
                    <input type="checkbox" class="planet-night-sweep" ${isNightSweep ? "checked" : ""}>
                    <span>Barrido nocturno</span>
                </label>
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
        
        planetsListContainer.appendChild(card);
    });
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
                statusText.innerText = "En ejecución";
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

function renderLogs(lines) {
    terminalConsole.innerHTML = "";
    
    lines.forEach(line => {
        const div = document.createElement("div");
        div.className = "log-line";
        
        // Formateo y Colores
        if (line.includes("ERROR") || line.includes("Exception") || line.includes("fallido")) {
            div.classList.add("log-error");
        } else if (line.includes("WARNING") || line.includes("omitido")) {
            div.classList.add("log-warn");
        } else if (line.includes("[DRY-RUN]")) {
            div.classList.add("log-dryrun");
        } else if (line.includes("[ACCION]") || line.includes("Ataque desde") || line.includes("Fleetsave")) {
            div.classList.add("log-action");
        } else if (line.includes("INFO") || line.includes("--- Nuevo ciclo ---")) {
            div.classList.add("log-info");
        }
        
        div.textContent = line.trim();
        terminalConsole.appendChild(div);
    });

    // Auto-scroll
    if (autoScrollCheck.checked) {
        terminalConsole.scrollTop = terminalConsole.scrollHeight;
    }
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

function setCheck(id, val) {
    const el = document.getElementById(id);
    if (el) el.checked = !!val;
}

function getCheck(id) {
    const el = document.getElementById(id);
    return el ? el.checked : false;
}

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
    
    setTimeout(() => {
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
        })
        .catch(err => console.error("Error al cargar estadísticas:", err));
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
        const qty = parseInt(expShips[shipKey]) || 0;
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
            const targetQty = parseInt(input.value) || 0;
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
            const targetQty = parseInt(input.value) || 0;
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



