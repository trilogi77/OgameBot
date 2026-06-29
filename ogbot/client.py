"""
client.py — Única capa que toca el juego en vivo (Playwright).
"""
from __future__ import annotations
import os
import time
from typing import Dict, List, Optional
from .config import Config
from .models import Planet, Coords, Resources, EspionageReport
from . import utils

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    sync_playwright = None
    PWTimeout = Exception


# ---------------------------------------------------------------------------
SEL = {
    "lobby_login_tab": "li:has-text('Log in'), li:has-text('Iniciar sesión'), li:has-text('Login'), li:has-text('Anmelden'), li:has-text('Se connecter'), li:has-text('Zaloguj'), li.tabs-navigation__item:nth-child(2)",
    "lobby_email":     "input[name='email']",
    "lobby_pass":      "input[name='password']",
    "lobby_login_btn": "#loginForm button[type=submit]",
    "play_button":     "button.btn-primary, button.btn-success, button.button-default, button",
    "accounts_url":    "https://lobby.ogame.gameforge.com/en_GB/accounts",
    "resource_metal":  "#resources_metal",
    "resource_crystal":"#resources_crystal",
    "resource_deut":   "#resources_deuterium",
    "resource_energy": "#resources_energy",
    "planet_list":     "#planetList .smallplanet",
    "captcha":         "#captcha, .challenge, iframe[src*='challenge']",
}

# URL real del juego: index.php?page=ingame&component=X
PAGE = {
    "overview":   "index.php?page=ingame&component=overview",
    "supplies":   "index.php?page=ingame&component=supplies",
    "facilities": "index.php?page=ingame&component=facilities",
    "research":   "index.php?page=ingame&component=research",
    "shipyard":   "index.php?page=ingame&component=shipyard",
    "defenses":   "index.php?page=ingame&component=defenses",
    "lfbuildings": "index.php?page=ingame&component=lfbuildings",
    "lfresearch":  "index.php?page=ingame&component=lfresearch",
    "fleet":      "index.php?page=ingame&component=fleetdispatch",
    "galaxy":     "index.php?page=ingame&component=galaxy",
    "movement":   "index.php?page=ingame&component=movement",
    "messages":   "index.php?page=ingame&component=messages",
    "event_list": "index.php?page=componentOnly&component=eventList",
}

# Nombre interno → ID de tecnología de OGame
TECH_IDS: Dict[str, int] = {
    # Supplies
    "metal_mine": 1,       "crystal_mine": 2,     "deut_synth": 3,
    "solar_plant": 4,      "fusion_reactor": 12,  "metal_storage": 22,
    "crystal_storage": 23, "deut_tank": 24,
    # Facilities
    "robotics_factory": 14, "shipyard": 21,        "research_lab": 31,
    "nanite_factory": 15,
    # Research
    "energy_tech": 113,    "laser_tech": 120,     "ion_tech": 121,
    "hyperspace_tech": 114,"plasma_tech": 122,    "combustion_drive": 115,
    "impulse_drive": 117,  "hyperspace_drive": 118,"espionage_tech": 106,
    "computer_tech": 108,  "astrophysics": 124,   "weapons_tech": 109,
    "shielding_tech": 110, "armor_tech": 111,
    # Ships
    "small_cargo": 202,    "large_cargo": 203,    "light_fighter": 204,
    "heavy_fighter": 205,  "cruiser": 206,        "battleship": 207,
    "colony_ship": 208,    "recycler": 209,       "espionage_probe": 210,
    "solar_satellite": 212,"bomber": 211,         "destroyer": 213,
    "deathstar": 214,      "battlecruiser": 215,  "reaper": 218,
    "pathfinder": 219,
    # Defenses
    "rocket_launcher": 401, "light_laser": 402,   "heavy_laser": 403,
    "gauss_cannon": 404,    "ion_cannon": 405,    "plasma_turret": 406,
    "small_shield_dome": 407, "large_shield_dome": 408,
}
# Inverso: tech_id str → name
_ID_TO_NAME = {str(v): k for k, v in TECH_IDS.items()}

# Nombres de naves tal como salen en los tooltips de movimientos del juego → clave
# interna. Solo naves que vuelan (los recursos de la carga se descartan al no estar aquí).
# ponytail: tabla ES+EN; si tus tooltips salen en otro idioma, añade sus nombres aquí.
_SHIP_NAME_ALIASES = {
    # Español
    "nave pequena de carga": "small_cargo",
    "nave grande de carga": "large_cargo",
    "gran nave de carga": "large_cargo",
    "cazador ligero": "light_fighter",
    "cazador pesado": "heavy_fighter",
    "crucero": "cruiser",
    "nave de batalla": "battleship",
    "nave colonizadora": "colony_ship",
    "reciclador": "recycler",
    "sonda de espionaje": "espionage_probe",
    "bombardero": "bomber",
    "satelite solar": "solar_satellite",
    "destructor": "destroyer",
    "estrella de la muerte": "deathstar",
    "acorazado": "battlecruiser",
    "segador": "reaper",
    "explorador": "pathfinder",
    # English
    "small cargo": "small_cargo",
    "large cargo": "large_cargo",
    "light fighter": "light_fighter",
    "heavy fighter": "heavy_fighter",
    "cruiser": "cruiser",
    "battleship": "battleship",
    "colony ship": "colony_ship",
    "recycler": "recycler",
    "espionage probe": "espionage_probe",
    "bomber": "bomber",
    "solar satellite": "solar_satellite",
    "destroyer": "destroyer",
    "deathstar": "deathstar",
    "death star": "deathstar",
    "battlecruiser": "battlecruiser",
    "reaper": "reaper",
    "pathfinder": "pathfinder",
}

_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def _ship_name_to_key(name: str) -> Optional[str]:
    """Normaliza el nombre de una nave del juego (ES/EN) a su clave interna, o None."""
    if not name:
        return None
    norm = " ".join(name.strip().lower().translate(_ACCENTS).split())
    return _SHIP_NAME_ALIASES.get(norm)

# Misiones OGame: nombre interno → código numérico
MISSION_CODES: Dict[str, int] = {
    "attack": 1, "group_attack": 2, "transport": 3, "deploy": 4,
    "hold": 5, "espionage": 6, "colonize": 7, "harvest": 8,
    "destroy": 9, "expedition": 15,
}
# Tipo de destino: nombre → código
TYPE_CODES: Dict[str, int] = {"planet": 1, "debris": 2, "moon": 3}

# JS que lee {tech_id_str: level/cantidad} de cualquier página de componente.
# Defensas usan .amount en vez de .level; probamos ambos.
_JS_READ_TECH = """() => {
    const r = {};
    document.querySelectorAll('li[data-technology]').forEach(li => {
        const tech = li.getAttribute('data-technology');
        const el = li.querySelector('.level[data-value], .amount[data-value]');
        if (el) r[tech] = parseInt(el.getAttribute('data-value') || '0');
    });
    return r;
}"""

# True si hay algo en la cola de construcción de suministros/instalaciones.
# NOTA: data-status="on" significa "disponible para construir", NO "construyendo".
# Indicadores reales de cola activa: timer en #build_list o elemento countdown.
_JS_BUILD_QUEUE_ACTIVE = """() => {
    // Items con timer en la lista de cola de construcción
    if (document.querySelector('#build_list .timer, #build_list .build_list_timer, #build_list [data-remaining]')) return true;
    // Countdown de construcción activo
    if (document.querySelector('.build-it_countdown, .ctn .cnt span')) return true;
    // Lista de cola no vacía (nodos li/item directos)
    const bl = document.querySelector('#build_list');
    if (bl && bl.querySelectorAll(':scope > li, :scope > .item').length > 0) return true;
    return false;
}"""

# True si la cola está activa según la página de overview.
_JS_BUILD_QUEUE_OVERVIEW = """() => {
    // td.idle = nada construyendo; su ausencia dentro de .construction.active = cola ocupada
    const idle = document.querySelector('.construction td.idle');
    if (idle !== null) return false;
    return !!document.querySelector('.construction.active');
}"""

_JS_BUILD_QUEUE = """() => {
    const queue = [];
    document.querySelectorAll('#build_list li[data-technology], .construction [data-technology]').forEach(el => {
        const tid = el.getAttribute('data-technology');
        if (tid) {
            queue.push(parseInt(tid));
        }
    });
    return queue;
}"""

_JS_BUILD_QUEUE_REMAINING = """() => {
    const el = document.querySelector('#build_list [data-remaining], .construction [data-remaining], [data-remaining]');
    if (el) {
        const val = parseInt(el.getAttribute('data-remaining') || '0');
        if (val > 0) return val;
    }
    const timer = document.querySelector('#build_list .timer, #build_list .build_list_timer, .construction .timer, .countdown');
    if (timer) {
        const txt = timer.textContent.trim();
        const parts = txt.split(':');
        if (parts.length === 3) {
            return parseInt(parts[0]) * 3600 + parseInt(parts[1]) * 60 + parseInt(parts[2]);
        }
        if (parts.length === 2) {
            return parseInt(parts[0]) * 60 + parseInt(parts[1]);
        }
        let secs = 0;
        const hM = txt.match(/(\\d+)h/);
        const mM = txt.match(/(\\d+)m/);
        const sM = txt.match(/(\\d+)s/);
        if (hM) secs += parseInt(hM[1]) * 3600;
        if (mM) secs += parseInt(mM[1]) * 60;
        if (sM) secs += parseInt(sM[1]);
        if (secs > 0) return secs;
    }
    return 0;
}"""

# True si la cola de Formas de vida está ocupada.
_JS_LF_QUEUE_ACTIVE = """() => {
    return !!(document.querySelector('.lifeformItemWrapper .on') ||
              document.querySelector('.lf-buildlist .item') ||
              document.querySelector('#lf_build_list .item'));
}"""

# Lee movimientos de flota activos desde la página de movimientos.
_JS_READ_MOVEMENTS = """() => {
    const results = [];
    const rows = document.querySelectorAll(
        '.eventFleet, .fleetDetails, .fleet_row, tr.flightEventRow'
    );
    rows.forEach(row => {
        try {
            // Las filas .fleetDetails son el PANEL de detalle de la flota de arriba, NO una
            // flota: emitirlas duplica cada movimiento (el usuario veía "2 despliegues"). Sus
            // naves se rescatan más abajo desde la fila principal hermana.
            if (row.classList && row.classList.contains('fleetDetails')) return;
            const mv = {};

            // 1. Mission
            let missionVal = row.getAttribute('data-mission-type');
            if (!missionVal) {
                const mEl = row.querySelector('[data-mission], [data-mission-type], .missionIcon, .icon_movement');
                if (mEl) {
                    missionVal = mEl.getAttribute('data-mission') ||
                                 mEl.getAttribute('data-mission-type') ||
                                 (mEl.className.match(/mission(\\d+)/) || [])[1] || '';
                }
            }
            mv.mission = missionVal || '';

            // 2. Origin Coordinates
            const origEl = row.querySelector(
                '.originCoords a, .originCoords .coords, .coordsOrigin a, .coordsOrigin, ' +
                '.originFleet a, [class*="origin"] a, [class*="orig"] a'
            );
            mv.origin = origEl ? origEl.textContent.replace(/[\\[\\]\\s]/g, '') : '';

            // 3. Origin Type (Planet / Moon / Debris)
            const origCell = row.querySelector(
                '.originCoords, .coordsOrigin, .originFleet, [class*="origin"], [class*="orig"]'
            );
            let origType = 'planet';
            if (origCell) {
                const icon = origCell.querySelector('.planetIcon, [class*="Icon"], [class*="icon"], figure, img');
                if (icon) {
                    const cls = (icon.className || '').toLowerCase() + ' ' + 
                                (icon.getAttribute('src') || '').toLowerCase() + ' ' +
                                (icon.getAttribute('title') || '').toLowerCase() + ' ' +
                                (icon.getAttribute('class') || '').toLowerCase();
                    if (cls.includes('moon') || cls.includes('luna')) origType = 'moon';
                    else if (cls.includes('tf') || cls.includes('debris') || cls.includes('escombros') || cls.includes('escombro')) origType = 'debris';
                }
            }
            mv.origin_type = origType;

            // 4. Destination Coordinates
            const destEl = row.querySelector(
                '.destinationCoords a, .destinationCoords .coords, .destCoords a, .destCoords .coords, ' +
                '.coordsDest a, .coordsDest, .destFleet a, [class*="destination"] a, [class*="dest"] a'
            );
            mv.destination = destEl ? destEl.textContent.replace(/[\\[\\]\\s]/g, '') : '';

            // 5. Destination Type (Planet / Moon / Debris)
            const destCell = row.querySelector(
                '.destinationCoords, .destCoords, .coordsDest, .destFleet, [class*="destination"], [class*="dest"]'
            );
            let destType = 'planet';
            if (destCell) {
                const icon = destCell.querySelector('.planetIcon, [class*="Icon"], [class*="icon"], figure, img');
                if (icon) {
                    const cls = (icon.className || '').toLowerCase() + ' ' + 
                                (icon.getAttribute('src') || '').toLowerCase() + ' ' +
                                (icon.getAttribute('title') || '').toLowerCase() + ' ' +
                                (icon.getAttribute('class') || '').toLowerCase();
                    if (cls.includes('moon') || cls.includes('luna')) destType = 'moon';
                    else if (cls.includes('tf') || cls.includes('debris') || cls.includes('escombros') || cls.includes('escombro')) destType = 'debris';
                }
            }
            mv.dest_type = destType;

            // 6. Arrival text. IMPORTANTE: excluir el temporizador de 'reversal' (regreso): la
            // fila trae a la vez el contador de LLEGADA y el de la vuelta si se recupera; coger
            // el de reversal hacía que un despliegue saliera con una hora de llegada errónea.
            const notReversal = el => !(el.closest && el.closest('[class*="reversal"]'));
            let arrEl = null;
            for (const el of row.querySelectorAll(
                    '.arrivalTime .value, .timer .value, .timeTillArrival, .countDown, .timer, [class*="timer"], [class*="countdown"]')) {
                if (notReversal(el)) { arrEl = el; break; }
            }
            mv.arrival_text = arrEl ? arrEl.textContent.trim() : '';

            // 6b. Epoch ABSOLUTO de llegada si el DOM lo expone (unix, segundos): es más
            // fiable que parsear el contador y evita confundir una hora absoluta H:MM:SS
            // con una cuenta atrás. También aquí se excluye el reversal.
            let absEp = parseInt(row.getAttribute('data-arrival-time') || '0') || 0;
            if (!absEp) {
                for (const ae of row.querySelectorAll('[data-arrival-time]')) {
                    if (!notReversal(ae)) continue;
                    absEp = parseInt(ae.getAttribute('data-arrival-time') || '0') || 0;
                    if (absEp) break;
                }
            }
            mv.arrival_epoch = absEp;

            // 6c. Reversal: el ancla de regreso (a.recallFleet) lleva en title/data-tooltip-title
            // la FECHA y HORA de vuelta si se recupera ahora, en hora del SERVIDOR de OGame
            // (p.ej. "Retirar:| 28.06.2026<br>23:04:56"). La convertimos a epoch UTC usando el
            // desfase horario del servidor, derivado de data-arrival-time (epoch UTC fiable) vs
            // la hora local mostrada en la celda arrivalTime. Así NO depende de la zona horaria
            // del contenedor (Docker suele ir en UTC y el servidor en CEST -> daba +2 h).
            let revEp = 0, revTxt = '';
            const recallA = row.querySelector('a.recallFleet, a[onclick*="sendRecall"], a.reversal');
            if (recallA) revTxt = (recallA.getAttribute('data-tooltip-title')
                                   || recallA.getAttribute('title') || '').trim();
            if (revTxt && absEp) {
                const dm = revTxt.match(/(\\d{1,2})[.\\-\\/](\\d{1,2})[.\\-\\/](\\d{2,4})\\D+(\\d{1,2}):(\\d{2}):(\\d{2})/);
                const arrCell = row.querySelector('.arrivalTime');
                const am = arrCell ? (arrCell.textContent || '').match(/(\\d{1,2}):(\\d{2}):(\\d{2})/) : null;
                if (dm && am) {
                    const arrSec = (+am[1]) * 3600 + (+am[2]) * 60 + (+am[3]);  // hora servidor de la llegada
                    let off = arrSec - (absEp % 86400);                         // desfase servidor - UTC (s)
                    off = ((off % 86400) + 86400) % 86400;
                    if (off > 43200) off -= 86400;                              // normalizar a [-12h, +12h]
                    let y = +dm[3]; if (y < 100) y += 2000;
                    const utc = Date.UTC(y, (+dm[2]) - 1, +dm[1], +dm[4], +dm[5], +dm[6]) / 1000;
                    revEp = Math.round(utc - off);
                }
            }
            mv.reversal_epoch = revEp;
            mv.reversal_text = revTxt;

            // 7. Flags
            const rf = row.getAttribute('data-return-flight');
            mv.is_return = row.classList.contains('is_return') ||
                           rf === 'true' || rf === '1' ||
                           !!row.querySelector('.return_flight, .returnflight');

            mv.is_hostile = row.classList.contains('hostile') ||
                            !!row.querySelector('.hostile') ||
                            (row.className && row.className.includes('hostile'));

            // 8. Composición de naves (tabla de detalles en línea o tooltip)
            mv.ships = {};
            (function(){
                let table = row.querySelector('.fleetinfos table, table.fleetinfo, .fleetinfo');
                if (!table) {
                    const tip = row.querySelector('[title*="fleetinfo"], [title*="<table"], .tooltip[title], .detailsFleet [title]');
                    if (tip) {
                        const html = tip.getAttribute('title') || '';
                        if (html.indexOf('<') !== -1) {
                            const tmp = document.createElement('div');
                            tmp.innerHTML = html;
                            table = tmp.querySelector('table');
                        }
                    }
                }
                if (!table) {
                    // Desglose por nave en la fila de DETALLE hermana (.fleetDetails), ahora
                    // que esa fila ya no se emite como flota aparte.
                    const sib = row.nextElementSibling;
                    if (sib && sib.classList && sib.classList.contains('fleetDetails')) {
                        table = sib.querySelector('.fleetinfos table, table.fleetinfo, .fleetinfo, table');
                    }
                }
                if (!table) return;
                table.querySelectorAll('tr').forEach(tr => {
                    const tds = tr.querySelectorAll('td');
                    if (tds.length < 2) return;
                    const a = tds[0].textContent.trim();
                    const b = tds[1].textContent.trim();
                    let name, valStr;
                    if (/^[\\d.,\\s]+$/.test(a)) { valStr = a; name = b; }
                    else { name = a; valStr = b; }
                    name = name.replace(/:$/, '').trim();
                    const val = parseInt(valStr.replace(/[^0-9]/g, '')) || 0;
                    if (name && val > 0) mv.ships[name] = (mv.ships[name] || 0) + val;
                });
            })();

            if (mv.origin || mv.destination) results.push(mv);
        } catch(e) {}
    });
    return results;
}"""

# Diagnóstico para el regreso de flota: por cada fila de movimiento devuelve lo que ven los
# selectores del matcher (origen/destino/misión/retorno) y las clases de los enlaces, para
# poder ajustar los selectores del botón de regreso cuando no se encuentra.
_JS_RECALL_DIAG = """() => {
    const norm = s => String(s || '').replace(/[\\[\\]\\s]/g, '');
    const rows = document.querySelectorAll('.eventFleet, .fleetDetails, .fleet_row, tr.flightEventRow');
    const out = [];
    rows.forEach(row => {
        const oEl = row.querySelector('.originCoords a, .originCoords .coords, .coordsOrigin a, .coordsOrigin, .originFleet a, [class*="origin"] a, [class*="orig"] a');
        const dEl = row.querySelector('.destinationCoords a, .destinationCoords .coords, .destCoords a, .destCoords .coords, .coordsDest a, .coordsDest, .destFleet a, [class*="destination"] a, [class*="dest"] a');
        const recall = row.querySelector('a.recallFleet, a[class*="recall"], a[onclick*="sendRecall"], a.reversal, .reversal_flight a, a.reversal_flight, a[class*="reversal"]');
        const anchors = [];
        row.querySelectorAll('a').forEach(a => {
            const c = a.className || '';
            const oc = a.getAttribute('onclick') || '';
            if (c || oc) anchors.push((c || oc).toString().slice(0, 50));
        });
        out.push({
            o: oEl ? norm(oEl.textContent) : null,
            d: dEl ? norm(dEl.textContent) : null,
            m: row.getAttribute('data-mission-type') || '',
            ret_cls: row.classList.contains('is_return'),
            rrf: row.getAttribute('data-return-flight'),
            ret_sel: !!row.querySelector('.return_flight, .returnflight'),
            hasRecall: !!recall,
            recallCls: recall ? (recall.className || recall.getAttribute('onclick') || '') : null,
            anchors: anchors.slice(0, 12),
            rowcls: (row.className || '').slice(0, 80)
        });
    });
    return out;
}"""

# Extrae todos los informes de espionaje de la página de mensajes.
_JS_ALL_SPY_REPORTS = """() => {
    const results = [];
    document.querySelectorAll('.rawMessageData').forEach(el => {
        try {
            const coordsStr = el.getAttribute('data-raw-coordinates');
            if (!coordsStr) return;
            const parts = coordsStr.split(':');
            if (parts.length < 3) return;
            
            const metal = parseInt(el.getAttribute('data-raw-metal') || '0');
            const crystal = parseInt(el.getAttribute('data-raw-crystal') || '0');
            const deut = parseInt(el.getAttribute('data-raw-deuterium') || '0');
            
            const rawFleetStr = el.getAttribute('data-raw-fleet') || '[]';
            const rawDefStr = el.getAttribute('data-raw-defense') || '[]';
            const rawStatusStr = el.getAttribute('data-raw-playerstatus') || '[]';
            
            if (isNaN(metal) && isNaN(crystal) && isNaN(deut) && rawFleetStr === '[]' && rawDefStr === '[]') {
                return;
            }
            
            const r = {
                galaxy: parseInt(parts[0]),
                system: parseInt(parts[1]),
                position: parseInt(parts[2]),
                resources: {
                    metal: isNaN(metal) ? 0 : metal,
                    crystal: isNaN(crystal) ? 0 : crystal,
                    deut: isNaN(deut) ? 0 : deut
                },
                fleet: {},
                defense: {},
                player_name: el.getAttribute('data-raw-playername') || '',
                is_inactive: rawStatusStr.includes('inactive')
            };
            
            const SHIP_MAP = {
                202:'small_cargo', 203:'large_cargo', 204:'light_fighter',
                205:'heavy_fighter', 206:'cruiser', 207:'battleship',
                208:'colony_ship', 209:'recycler', 210:'espionage_probe',
                211:'bomber', 212:'solar_satellite', 213:'destroyer',
                214:'deathstar', 215:'battlecruiser'
            };
            const DEF_MAP = {
                401:'rocket_launcher', 402:'light_laser', 403:'heavy_laser',
                404:'gauss_cannon', 405:'ion_cannon', 406:'plasma_turret',
                407:'small_shield_dome', 408:'large_shield_dome'
            };
            
            try {
                const fleetObj = JSON.parse(rawFleetStr);
                if (fleetObj && typeof fleetObj === 'object' && !Array.isArray(fleetObj)) {
                    for (const [tidStr, count] of Object.entries(fleetObj)) {
                        const tid = parseInt(tidStr);
                        if (SHIP_MAP[tid]) {
                            r.fleet[SHIP_MAP[tid]] = parseInt(count);
                        }
                    }
                }
            } catch(e) {}
            
            try {
                const defObj = JSON.parse(rawDefStr);
                if (defObj && typeof defObj === 'object' && !Array.isArray(defObj)) {
                    for (const [tidStr, count] of Object.entries(defObj)) {
                        const tid = parseInt(tidStr);
                        if (DEF_MAP[tid]) {
                            r.defense[DEF_MAP[tid]] = parseInt(count);
                        }
                    }
                }
            } catch(e) {}
            
            results.push(r);
        } catch(e) {}
    });
    return results;
}"""

# Establece el slider o selector de velocidad de flota (compatible con OGame Redesign y React).
_JS_SET_SPEED = """(val) => {
    let stepNum = parseInt(val);
    if (stepNum > 10 && stepNum <= 100) {
        stepNum = stepNum / 10;
    }
    
    // 1. Intentar por data-step en el contenedor .steps (específico para OGame Redesign)
    if (stepNum >= 1 && stepNum <= 10) {
        const stepEl = document.querySelector(`.steps .step[data-step="${stepNum}"], .steps [data-step="${stepNum}"], [data-step="${stepNum}"].step`);
        if (stepEl) {
            stepEl.click();
            return 'clicked_data_step:' + stepNum;
        }
    }
    
    // 2. Buscar por texto dentro del contenedor .steps
    const stepsContainer = document.querySelector('.steps');
    if (stepsContainer && stepNum >= 1 && stepNum <= 10) {
        const targetPct = String(stepNum * 10);
        for (const el of stepsContainer.querySelectorAll('.step, div')) {
            const txt = el.textContent.trim();
            if (txt === targetPct || txt === targetPct + '%') {
                el.click();
                return 'clicked_steps_by_text:' + txt;
            }
        }
    }

    const pct = val * 10;
    
    // 3. Fallback de selectores tradicionales
    const selectors = [
        `ul.speed li.step${pct}`,
        `ul.speed li[data-value="${pct}"]`,
        `.speedLinks a.step${pct}`,
        `.speedLinks .step${pct}`,
        `#speedLinks a.step${pct}`,
        `#speedLinks .step${pct}`,
        `a[onclick*="selectSpeed(${pct})"]`,
        `a[onclick*="selectSpeed(${val})"]`,
        `#speedLinks a`,
        `.speedLinks a`
    ];
    
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) {
            el.click();
            return 'clicked_selector:' + sel;
        }
    }
    
    // Buscar por texto en elementos de velocidad específicos (excluyendo .step genérico que choca con paginadores)
    const elements = document.querySelectorAll('.speedLinks a, #speedLinks a, a.speedLink, .speed_percent, ul.speed li');
    for (const el of elements) {
        const txt = el.textContent.trim();
        if (txt === pct + '%' || txt === String(pct)) {
            el.click();
            return 'clicked_by_text:' + txt;
        }
    }
    
    // Buscar por el atributo onclick
    for (const el of document.querySelectorAll('a, span, div, li')) {
        const oc = el.getAttribute('onclick');
        if (oc && (oc.includes(`selectSpeed(${pct})`) || oc.includes(`selectSpeed(${val})`))) {
            el.click();
            return 'clicked_by_onclick';
        }
    }

    // 4. Fallback: slider de rango estándar (input range)
    const slider = document.querySelector('#speedPercent, input[name="speed"], input[type="range"]');
    if (slider) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(slider, String(val));
        slider.dispatchEvent(new Event('input', {bubbles: true}));
        slider.dispatchEvent(new Event('change', {bubbles: true}));
        return 'slider_set';
    }
    
    return false;
}"""


class GameClient:
    def __init__(self, cfg: Config, logger):
        self.cfg = cfg
        self.log = logger
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.lobby_url = "https://lobby.ogame.gameforge.com/"
        self._planet_cache: List[Planet] = []

    # ------------------------------------------------------------------
    def start(self):
        if sync_playwright is None:
            raise RuntimeError("pip install playwright && playwright install chromium")
        self._pw = sync_playwright().start()
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            f"--remote-debugging-port={getattr(self.cfg, 'cdp_port', 9222)}",
        ]
        # En Docker (Chromium como root) hacen falta estos flags
        if os.environ.get("OGBOT_CHROMIUM_NO_SANDBOX"):
            launch_args += ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        launch_kwargs = {"headless": self.cfg.headless, "args": launch_args}
        # Proxy opcional (p.ej. residencial) para evitar el bloqueo de login por IP de VPS
        proxy_server = getattr(self.cfg, "proxy_server", "") or os.environ.get("OGBOT_PROXY", "")
        if proxy_server:
            proxy = {"server": proxy_server}
            if getattr(self.cfg, "proxy_username", ""):
                proxy["username"] = self.cfg.proxy_username
                proxy["password"] = getattr(self.cfg, "proxy_password", "")
            launch_kwargs["proxy"] = proxy
            self.log.info("Usando proxy para el navegador: %s", proxy_server)
        self.browser = self._pw.chromium.launch(**launch_kwargs)
        state = "ogame_session.json"
        self.context = self.browser.new_context(
            storage_state=state if os.path.exists(state) else None,
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"),
            viewport={"width": 1366, "height": 768},
        )
        self.page = self.context.new_page()

    def stop(self):
        try:
            if self.context:
                self.context.storage_state(path="ogame_session.json")
        except Exception:
            pass
        for obj in (self.browser, self._pw):
            try:
                obj.close() if hasattr(obj, "close") else obj.stop()
            except Exception:
                pass

    def _delay(self):
        utils.human_delay(self.cfg.min_action_delay_s, self.cfg.max_action_delay_s)

    def _has_captcha(self) -> bool:
        try:
            return self.page.locator(SEL["captcha"]).count() > 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    #  Login
    # ------------------------------------------------------------------
    def _is_game_url(self, url: str) -> bool:
        return ("index.php" in url or "/game/" in url) and "lobby" not in url

    def _all_pages(self):
        """
        TODAS las páginas del navegador (de todos los contextos), no solo self.context.
        Al pulsar Jugar, OGame puede abrir el juego en otra ventana/contexto; si solo
        miramos self.context.pages no lo vemos y creemos que no entró.
        """
        pages = []
        try:
            for ctx in self.browser.contexts:
                try:
                    pages.extend(ctx.pages)
                except Exception:
                    continue
        except Exception:
            pass
        if not pages:
            try:
                pages = list(self.context.pages)
            except Exception:
                pages = []
        return pages

    def _dismiss_login_error(self) -> bool:
        """
        Cierra el diálogo de GameForge 'Ha ocurrido un error al iniciar sesión.
        Inténtalo de nuevo' si aparece. Devuelve True si detectó/cerró el error.
        """
        for pg in self._all_pages():
            try:
                if pg.is_closed():
                    continue
                err = pg.locator(
                    "text=/error al iniciar sesión|error occurred while logging|"
                    "Inténtalo de nuevo|try again/i"
                ).first
                if err.count() > 0 and err.is_visible():
                    for sel in ["button:has-text('OK')", "button:has-text('Aceptar')",
                                "a:has-text('OK')", "a:has-text('Aceptar')",
                                "button:has-text('Cerrar')", ".btn-primary", "button.close"]:
                        try:
                            b = pg.locator(sel).first
                            if b.count() > 0 and b.is_visible():
                                b.click()
                                self.log.info("Cerrado diálogo de error de login de GameForge.")
                                return True
                        except Exception:
                            continue
                    return True  # error detectado aunque no se pudiera pulsar OK
            except Exception:
                continue
        return False

    def _enter_game_via_play(self, play_locator) -> bool:
        """
        Pulsa Jugar y entra al juego (headless/servidor). Escucha todas las pestañas,
        tolera que la de /loading se cierre, y REINTENTA si GameForge devuelve el
        error 'Ha ocurrido un error al iniciar sesión. Inténtalo de nuevo'.
        """
        self.log.info("Entrando al juego (modo robusto v4)...")
        new_pages = []

        def _on_page(pg):
            try:
                new_pages.append(pg)
            except Exception:
                pass

        try:
            self.context.on("page", _on_page)
        except Exception:
            pass

        try:
            for attempt in range(1, 4):
                # Click normal y, si no dispara en headless, click por JS de respaldo
                try:
                    play_locator.first.click(timeout=8000)
                except Exception as e:
                    self.log.debug("Click en Jugar falló (%s); probando por JS.", e)
                    try:
                        play_locator.first.evaluate("el => el.click()")
                    except Exception:
                        pass

                # Esperar (hasta 45s) a que alguna pestaña llegue al juego, o detectar el error
                deadline = time.time() + 45
                target = None
                login_error = False
                while time.time() < deadline and target is None:
                    for pg in self._all_pages() + list(new_pages):
                        try:
                            if pg.is_closed():
                                continue
                            if self._is_game_url(pg.url):
                                target = pg
                                break
                        except Exception:
                            continue
                    if target is None:
                        if self._dismiss_login_error():
                            login_error = True
                            break  # GameForge falló: salir a reintentar
                        time.sleep(1.0)

                if target is not None:
                    try:
                        target.wait_for_load_state("domcontentloaded", timeout=20000)
                    except Exception:
                        pass
                    self.page = target
                    try:
                        self.context = target.context  # el juego puede estar en otro contexto
                    except Exception:
                        pass
                    self.log.info("Entrado al juego: %s", self.page.url)
                    for pg in self._all_pages():
                        try:
                            if pg is not self.page and not pg.is_closed():
                                pg.close()
                        except Exception:
                            pass
                    if "/game/" in self.page.url:
                        actual_base = self.page.url.split("/game/")[0] + "/"
                        if self.cfg.server_url.rstrip("/") != actual_base.rstrip("/"):
                            self.log.warning(
                                "Servidor real detectado en vivo: %s (configurado: %s). Actualizando server_url.",
                                actual_base, self.cfg.server_url)
                            self.cfg.server_url = actual_base
                    return True

                self.log.warning("Play intento %d/3: %s. Reintentando...", attempt,
                                 "GameForge devolvió error de login" if login_error else "no se entró al juego")
                time.sleep(3)
                fresh = self._find_play_button()
                if fresh is not None:
                    play_locator = fresh

            urls = []
            for pg in self._all_pages():
                try:
                    if not pg.is_closed():
                        urls.append(pg.url)
                except Exception:
                    pass
            self.log.warning(
                "Play: no se entró al juego tras 3 intentos (pestañas: %s). Si corres en un "
                "servidor/VPS, GameForge suele bloquear el login por IP; configura un proxy "
                "residencial (proxy_server / OGBOT_PROXY).", urls)
            return False
        except Exception as e:
            self.log.warning("Error en _enter_game_via_play: %s", e)
            return False
        finally:
            try:
                self.context.remove_listener("page", _on_page)
            except Exception:
                pass

    def _find_play_button(self):
        """
        Busca el botón de 'Play'/'Jugar' correcto.
        Primero intenta buscarlo específicamente para el universo configurado.
        Si no, busca por palabras clave comunes o cualquier botón de acción visible.
        """
        # 1. Por universo específico
        if self.cfg.universe:
            # Buscar en contenedores del lobby (cards, rows, celdas)
            for container in [".accountCard", "tr", ".account-row", ".account-item", "div.account-list-item", "div"]:
                try:
                    locator = self.page.locator(container, has_text=self.cfg.universe)
                    if locator.count() > 0:
                        for btn_sel in ["button.btn-primary", "button.btn-success", "button.button-default", "button", "a.btn"]:
                            btn = locator.locator(btn_sel).first
                            if btn.count() > 0 and btn.is_visible():
                                self.log.info("Encontrado botón Jugar para el universo '%s' (%s > %s)", self.cfg.universe, container, btn_sel)
                                return btn
                except Exception:
                    continue

        # 2. Por texto (Play, Jugar, Spielen, Jouer...)
        for text in ["Play", "Jugar", "Spielen", "Jouer", "Grać", "Gioca"]:
            for btn_sel in [f"button:has-text('{text}')", f"a:has-text('{text}')"]:
                try:
                    btn = self.page.locator(btn_sel).first
                    if btn.count() > 0 and btn.is_visible():
                        self.log.info("Encontrado botón de entrada general por texto '%s'", text)
                        return btn
                except Exception:
                    continue

        # 3. Fallback: cualquier botón primario o similar
        for btn_sel in [SEL["play_button"], "button.btn-primary", "button.btn-success", "button.button-default", "button"]:
            try:
                btn = self.page.locator(btn_sel).first
                if btn.count() > 0 and btn.is_visible():
                    self.log.info("Encontrado botón de entrada por fallback de selector '%s'", btn_sel)
                    return btn
            except Exception:
                continue

        return None

    def _wait_for_human_check(self, where: str) -> bool:
        """
        Verificación humana (CAPTCHA) en el login. En modo headless/Docker no hay
        navegador visible, PERO el visor 'Bot en Directo' del panel web sí permite
        verla y resolverla (clic/teclado vía CDP). Espera hasta
        login_human_check_timeout_s sondeando si ya se resolvió, y avisa al usuario.
        """
        timeout_s = float(getattr(self.cfg, "login_human_check_timeout_s", 300) or 300)
        self.log.warning(
            "Verificación humana (CAPTCHA) %s. Abre el panel web -> pestaña "
            "'Bot en Directo' y resuélvela. Esperando hasta %.0f min...",
            where, timeout_s / 60.0)
        try:
            from . import utils
            if getattr(self.cfg, "telegram_token", "") and getattr(self.cfg, "telegram_chat_id", ""):
                utils.send_telegram_message(
                    self.cfg.telegram_token, self.cfg.telegram_chat_id,
                    "🤖 OGBot: verificación humana en el login. Resuélvela en el visor "
                    "'Bot en Directo' del panel web.", logger=self.log)
        except Exception:
            pass
        start = time.time()
        while time.time() - start < timeout_s:
            time.sleep(5)
            try:
                if not self._has_captcha():
                    self.log.info("Verificación humana resuelta; continúo con el login.")
                    return True
            except Exception:
                pass
        self.log.warning("Se agotó la espera de verificación humana (%s).", where)
        return False

    def login(self) -> bool:
        # Determinar URL de cuentas localizada según país
        country = (self.cfg.country or "en").lower()
        locales = {
            "es": "es_ES", "de": "de_DE", "en": "en_GB", "fr": "fr_FR",
            "it": "it_IT", "pl": "pl_PL", "ru": "ru_RU", "tr": "tr_TR"
        }
        locale = locales.get(country, f"{country}_{country.upper()}")
        accounts_url = f"https://lobby.ogame.gameforge.com/{locale}/accounts"

        # 1) Intentar sesión guardada directamente en el servidor del juego
        if self.cfg.server_url:
            try:
                self.page.goto(self.cfg.server_url, wait_until="domcontentloaded", timeout=20000)
                self._delay()
                if self._is_game_url(self.page.url):
                    self.log.info("Sesión restaurada (sin login).")
                    return True
            except Exception:
                pass

        # 2) Ir a la página de cuentas del lobby
        self.log.info("Navegando a cuentas del lobby...")
        self.page.goto(accounts_url, wait_until="domcontentloaded", timeout=30000)
        self._delay()

        if self._is_game_url(self.page.url):
            self.log.info("Sesión restaurada vía lobby.")
            return True

        # 3) Si hay token Gameforge activo, Play/Jugar aparece → abre nueva pestaña
        play = self._find_play_button()
        if play:
            self.log.info("Sesión activa. Entrando al universo...")
            if self._enter_game_via_play(play):
                return True
            self.log.warning("Play no llevó al juego; intentando login completo.")

        # 4) Login completo con credenciales
        if self._has_captcha():
            self._wait_for_human_check("antes del login")

        try:
            self.page.goto(self.lobby_url, wait_until="domcontentloaded", timeout=30000)
            self.page.wait_for_selector(SEL["lobby_login_tab"], state="visible", timeout=15000)
            self.page.click(SEL["lobby_login_tab"])
            self.page.wait_for_selector("#loginForm", state="visible", timeout=10000)
            self.page.fill(SEL["lobby_email"], self.cfg.username)
            self.page.fill(SEL["lobby_pass"], self.cfg.password)
            self._delay()
            self.page.click(SEL["lobby_login_btn"])
            self.page.wait_for_load_state("domcontentloaded", timeout=30000)
            self._delay()

            if self._has_captcha():
                self._wait_for_human_check("tras introducir credenciales")

            play2 = self._find_play_button()
            if play2:
                if self._enter_game_via_play(play2):
                    return True
            self.log.error("Login completo: no se alcanzó la URL del juego.")
            return False
        except PWTimeout:
            self.log.error("Timeout en login (URL: %s).", self.page.url)
            return False

    # ------------------------------------------------------------------
    #  Navegación interna
    # ------------------------------------------------------------------
    def _goto(self, page_key: str, planet: "Planet | None" = None):
        url = f"{self.cfg.server_url.rstrip('/')}/game/{PAGE[page_key]}"
        if planet is not None:
            pid = planet.id.replace("planet-", "")
            url += f"&cp={pid}"
        for attempt in range(3):
            try:
                self.page.goto(url, wait_until="domcontentloaded")
                # Si OGame nos ha sacado al lobby, reloginear automáticamente
                if not self._is_game_url(self.page.url):
                    self.log.warning("Sesión caducada (URL=%s); reloginando...", self.page.url)
                    if self.login():
                        self.page.goto(url, wait_until="domcontentloaded")
                    else:
                        raise RuntimeError("Re-login fallido tras sesión caducada.")
                break
            except Exception as e:
                err_str = str(e)
                self.log.warning("Error en navegación _goto (intento %d/3): %s", attempt + 1, err_str)
                if ("Target page" in err_str or "context or browser has been closed" in err_str
                        or "TargetClosedError" in type(e).__name__) and attempt < 2:
                    self.log.warning("Página cerrada (intento %d/2); recuperando sesión...", attempt + 1)
                    try:
                        self.page = self.context.new_page()
                    except Exception:
                        pass
                    if self.login():
                        continue  # reintenta el goto con la sesión restaurada
                    raise
                elif attempt < 2:
                    # Otros errores temporales (como navegación interrumpida, ERR_ABORTED, etc.): esperar y reintentar
                    time.sleep(2)
                    continue
                else:
                    raise
        self._delay()

    def _wait_tech(self, timeout: int = 8000):
        try:
            self.page.wait_for_selector("li[data-technology]", timeout=timeout)
        except Exception:
            pass

    def _is_error_page(self) -> bool:
        """Devuelve True si OGame muestra 'An error has occured' en la página actual."""
        try:
            text = self.page.locator("body").inner_text(timeout=2000)
            return "An error has occured" in text or "An error has occurred" in text
        except Exception:
            return False

    def _get_page_token(self) -> Optional[str]:
        """Extrae el token CSRF de la página actual (necesario para builds via URL)."""
        try:
            return self.page.evaluate("""() => {
                if (window.token) return String(window.token);
                const m = document.querySelector('meta[name="token"]');
                if (m) return m.getAttribute('content');
                const i = document.querySelector('input[name="token"]');
                if (i) return i.value;
                for (const s of document.querySelectorAll('script')) {
                    const txt = s.textContent || '';
                    const t = txt.match(/"token"\s*:\s*"(\w{16,})"|var token\s*=\s*"(\w{16,})"/);
                    if (t) return t[1] || t[2];
                }
                return null;
            }""")
        except Exception:
            return None

    def _is_build_queue_active(self) -> bool:
        try:
            return bool(self.page.evaluate(_JS_BUILD_QUEUE_ACTIVE))
        except Exception:
            return False

    def _is_build_queue_active_from_overview(self) -> bool:
        """Comprueba la cola desde la página de overview (más fiable). Debe llamarse en overview."""
        try:
            return bool(self.page.evaluate(_JS_BUILD_QUEUE_OVERVIEW))
        except Exception:
            return False

    def _get_build_queue_remaining_seconds(self) -> int:
        try:
            return int(self.page.evaluate(_JS_BUILD_QUEUE_REMAINING) or 0)
        except Exception:
            return 0

    def _get_build_queue(self) -> List[str]:
        try:
            tids = self.page.evaluate(_JS_BUILD_QUEUE) or []
            return [_ID_TO_NAME[str(tid)] for tid in tids if str(tid) in _ID_TO_NAME]
        except Exception:
            return []

    def _is_lf_queue_active(self) -> bool:
        try:
            return bool(self.page.evaluate(_JS_LF_QUEUE_ACTIVE))
        except Exception:
            return False

    def _click_upgrade(self, tech_id: int) -> bool:
        """Intenta hacer clic en el botón de upgrade/build de tech_id."""
        btn = self._find_upgrade_locator(tech_id)
        if btn is not None:
            try:
                btn.click()
                return True
            except Exception:
                pass
        # Fallback JS: click en cualquier botón no deshabilitado dentro del li
        try:
            js = f"""() => {{
                const li = document.querySelector("li[data-technology='{tech_id}']");
                if (!li) return null;
                const btn = li.querySelector(
                    'button:not([disabled]):not(.disabled):not(.tpl_busy),' +
                    'a.btn_blue:not(.disabled),.btnBuy:not([disabled])'
                );
                if (btn && btn.offsetParent !== null) {{ btn.click(); return btn.className; }}
                // último recurso: primer botón visible
                for (const el of li.querySelectorAll('button, a[href="#"]')) {{
                    if (el.offsetParent !== null && !el.disabled) {{ el.click(); return 'any:' + el.className; }}
                }}
                return null;
            }}"""
            result = self.page.evaluate(js)
            if result:
                self.log.debug("JS click tech_id=%d: %s", tech_id, result)
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    #  Lectura de estado
    # ------------------------------------------------------------------
    def read_resources(self) -> Resources:
        def num(sel):
            try:
                txt = (self.page.locator(sel).first.get_attribute("data-raw")
                       or self.page.locator(sel).first.inner_text())
                return float(str(txt).replace(".", "").replace(",", "").strip() or 0)
            except Exception:
                return 0.0
        return Resources(num(SEL["resource_metal"]),
                         num(SEL["resource_crystal"]),
                         num(SEL["resource_deut"]),
                         num(SEL["resource_energy"]))

    def read_planets(self) -> List[Planet]:
        self._goto("overview")
        planets: List[Planet] = []
        try:
            js = r"""() => {
                const results = [];
                document.querySelectorAll('#planetList .smallplanet').forEach(el => {
                    const id = el.getAttribute('id') || '';
                    const coordsEl = el.querySelector('.planet-koords');
                    const coordsTxt = coordsEl ? coordsEl.textContent.trim() : '';
                    const nameEl = el.querySelector('.planet-name');
                    const name = nameEl ? nameEl.textContent.trim() : '';
                    
                    const moonEl = el.querySelector('a.moonlink');
                    let moonId = null;
                    if (moonEl) {
                        const href = moonEl.getAttribute('href') || '';
                        const match = href.match(/cp=(\d+)/);
                        if (match) {
                            moonId = "planet-" + match[1];
                        }
                    }
                    results.push({ id, name, coordsTxt, moonId });
                });
                return results;
            }"""
            rows = self.page.evaluate(js) or []
            for row in rows:
                coords_txt = row["coordsTxt"].strip("[] ")
                if not coords_txt or ":" not in coords_txt:
                    continue
                g, s, p = [int(x) for x in coords_txt.split(":")]
                planet_obj = Planet(id=row["id"], name=row["name"], coords=Coords(g, s, p, type="planet"))
                if row["moonId"]:
                    planet_obj.has_moon = True
                    planet_obj.moon = Planet(
                        id=row["moonId"],
                        name=f"{row['name']} (Luna)",
                        coords=Coords(g, s, p, type="moon")
                    )
                planets.append(planet_obj)
        except Exception as e:
            self.log.warning("No pude leer planetas: %s", e)
        self._planet_cache = planets
        return planets

    def _planet_by_coords(self, coords: Coords) -> "Optional[Planet]":
        """Busca el planeta o luna en caché por coordenadas y tipo; devuelve el primero si no hay match."""
        for p in self._planet_cache:
            if p.coords.tuple() == coords.tuple():
                if getattr(coords, "type", "planet") == "moon":
                    if p.has_moon and p.moon:
                        return p.moon
                return p
        return self._planet_cache[0] if self._planet_cache else None

    def _click_first(self, selectors: list, timeout_ms: int = 3000) -> bool:
        """Hace clic en el primer selector visible de la lista."""
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0:
                    loc.wait_for(state="visible", timeout=timeout_ms)
                    loc.click()
                    return True
            except Exception:
                continue
        return False

    def _fill_first(self, selectors: list, value: str) -> bool:
        """Rellena el primer input visible de la lista y dispara eventos."""
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.fill(value)
                    loc.press("Tab")
                    return True
            except Exception:
                continue
        return False

    def _read_tech_page(self, component: str, planet: "Planet | None" = None) -> Dict[str, int]:
        """Lee {name: level} de una página de componente usando data-technology."""
        self._goto(component, planet)
        self._wait_tech()
        try:
            raw = self.page.evaluate(_JS_READ_TECH)  # {tech_id_str: level}
            return {_ID_TO_NAME[k]: v for k, v in raw.items() if k in _ID_TO_NAME}
        except Exception as e:
            self.log.warning("Error leyendo %s: %s", component, e)
            return {}

    def read_planet_state(self, planet: Planet) -> None:
        """Lee recursos, edificios, naves, defensas y cola de construcción."""
        self._goto("overview", planet)
        planet.resources = self.read_resources()
        # Cola de construcción: lo más fiable es leerla desde overview
        planet.building_in_progress = self._is_build_queue_active_from_overview()
        planet.building_remaining_seconds = self._get_build_queue_remaining_seconds() if planet.building_in_progress else 0
        planet.building_queue = self._get_build_queue() if planet.building_in_progress else []

        # Para lunas, saltar "supplies", solo leer "facilities"
        components = ["facilities"]
        if planet.coords.type == "planet":
            components.append("supplies")

        for comp in components:
            data = self._read_tech_page(comp, planet)
            planet.buildings.update(data)

        ships_data = self._read_tech_page("shipyard", planet)
        planet.ships.update(ships_data)

        defense_data = self._read_tech_page("defenses", planet)
        planet.defenses.update(defense_data)

        # Detectar cola de Formas de vida (solo en planetas)
        if planet.coords.type == "planet" and planet.lifeform_available:
            self._goto("lfbuildings", planet)
            if self._is_error_page():
                self.log.info("Formas de vida no disponibles en %s (universo sin LF o no desbloqueado).", planet.coords)
                planet.lifeform_available = False
                planet.lifeform_in_progress = False
            else:
                try:
                    self._wait_tech(timeout=5000)
                    planet.lifeform_in_progress = self._is_lf_queue_active()
                except Exception:
                    planet.lifeform_in_progress = False
        else:
            planet.lifeform_available = False
            planet.lifeform_in_progress = False

        self.log.info(
            "Ubicación %s: M=%.0f C=%.0f D=%.0f | minas M%d/C%d/D%d | "
            "def RL=%d | cola=%s lf=%s",
            planet.coords,
            planet.resources.metal, planet.resources.crystal, planet.resources.deut,
            planet.lvl("metal_mine"), planet.lvl("crystal_mine"), planet.lvl("deut_synth"),
            planet.defenses.get("rocket_launcher", 0),
            f"ocupada ({', '.join(planet.building_queue)} - {planet.building_remaining_seconds}s)" if planet.building_in_progress else "libre",
            "ocupada" if planet.lifeform_in_progress else "libre",
        )

    def read_planet_light(self, planet: Planet) -> None:
        """
        Lectura LIGERA: solo lo que cambia en vivo cada ciclo -> recursos, cola de
        construcción (con su tiempo restante) y naves. NO lee edificios/defensas:
        esos los aporta la caché de niveles del cerebro. Mucho menos navegación.
        """
        self._goto("overview", planet)
        planet.resources = self.read_resources()
        planet.building_in_progress = self._is_build_queue_active_from_overview()
        planet.building_remaining_seconds = self._get_build_queue_remaining_seconds() if planet.building_in_progress else 0
        planet.building_queue = self._get_build_queue() if planet.building_in_progress else []

        ships_data = self._read_tech_page("shipyard", planet)
        planet.ships.update(ships_data)

        self.log.info(
            "Ubicación %s (ligero): M=%.0f C=%.0f D=%.0f | cola=%s",
            planet.coords,
            planet.resources.metal, planet.resources.crystal, planet.resources.deut,
            f"ocupada ({', '.join(planet.building_queue)} - {planet.building_remaining_seconds}s)" if planet.building_in_progress else "libre",
        )

    def read_research(self) -> Dict[str, int]:
        """Lee niveles de investigación actuales."""
        return self._read_tech_page("research")

    # ------------------------------------------------------------------
    #  Acciones (respetan dry_run)
    # ------------------------------------------------------------------
    def _act(self, description: str) -> bool:
        if self.cfg.dry_run:
            self.log.info("[DRY-RUN] %s", description)
            return True
        self.log.info("[ACCION] %s", description)
        return False

    def _find_upgrade_locator(self, tech_id: int):
        """Devuelve el primer locator visible del botón de upgrade, o None."""
        for sel in [
            f"li[data-technology='{tech_id}'] button.upgrade",
            f"li[data-technology='{tech_id}'] button[data-action='build']",
            f"li[data-technology='{tech_id}'] .btn_action_row.upgrade",
            f"li[data-technology='{tech_id}'] .btnBuy",
            f"li[data-technology='{tech_id}'] a.btn_blue",
            f"li[data-technology='{tech_id}'] button:not([disabled])",
        ]:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    def build(self, planet: Planet, component: str, what: str) -> bool:
        if self._act(f"Construir {what} en {planet.coords}"):
            return True
        tech_id = TECH_IDS.get(what)
        if tech_id is None:
            self.log.warning("Sin tech_id para edificio: %s", what)
            return False
        self._goto(component, planet)
        self._wait_tech()
        if self._is_build_queue_active():
            self.log.info("Cola de construcción ocupada en %s; saltando.", planet.coords)
            planet.building_in_progress = True
            return False
        btn = self._find_upgrade_locator(tech_id)
        if btn is None:
            self.log.warning("No pude construir %s en %s: botón no encontrado.", what, planet.coords)
            return False
        btn.click()
        # Esperar a que el servidor procese la petición AJAX
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            time.sleep(2)
        # Verificar que OGame no devolvió una página de error
        if self._is_error_page():
            self.log.warning("OGame reportó error al construir %s en %s.", what, planet.coords)
            return False
        self.log.info("Construcción iniciada: %s lv%d en %s",
                      what, planet.lvl(what) + 1, planet.coords)
        planet.building_in_progress = True
        return True

    def research(self, tech: str, planet: "Planet | None" = None) -> bool:
        if self._act(f"Investigar {tech}" + (f" desde {planet.coords}" if planet else "")):
            return True
        tech_id = TECH_IDS.get(tech)
        if tech_id is None:
            self.log.warning("Sin tech_id para investigación: %s", tech)
            return False
        self._goto("research", planet)
        self._wait_tech()
        if self._click_upgrade(tech_id):
            self.log.info("Investigación iniciada: %s", tech)
            return True
        else:
            self.log.warning("No pude investigar %s: botón no encontrado.", tech)
            return False

    def _build_units_ui(self, tech_id: int, amount: int) -> bool:
        """
        Construye barcos o defensas usando la UI estándar de OGame (3 pasos):
        1. Clic en la tarjeta del item (li[data-technology=X]).
        2. Clic y rellenado en el input de cantidad del panel de detalle.
        3. Clic en el botón verde "Construir".
        """
        try:
            # 1. Clic en la tarjeta del item
            card = self.page.locator(f"li[data-technology='{tech_id}']").first
            if card.count() == 0:
                self.log.warning("No se encontró la tarjeta del elemento con ID=%d", tech_id)
                return False
            
            card.click()
            time.sleep(0.5)  # Esperar a que se abra la sección de detalles
            
            # 2. Localizar el input de cantidad
            input_selectors = [
                "#technologydetails input#number",
                "#technologydetails input[name='kolonne']",
                ".technology-details input#number",
                ".technology-details input[name='kolonne']",
                "input#number",
                "input[name='kolonne']",
                "input#build_amount",
                ".technology-details input.amount",
                "input#amount",
                ".detail-overlay input",
                "input[name='amount']",
                "input.amount"
            ]
            
            qty_input = None
            for sel in input_selectors:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    qty_input = loc
                    break
            
            if not qty_input:
                self.log.warning("No se encontró el campo de entrada para cantidad.")
                return False
            
            # Clicar en el input para enfocarlo
            qty_input.click()
            # Rellenar la cantidad (escribir)
            qty_input.fill(str(amount))
            time.sleep(0.3)
            
            # 3. Localizar y clicar el botón de construir
            btn_selectors = [
                ".technology-details button.upgrade",
                "button.upgrade",
                "button.build",
                "#build_button",
                ".build_button",
                "button:has-text('Construir')",
                "button.btnBuy"
            ]
            
            build_btn = None
            for sel in btn_selectors:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible() and not loc.is_disabled():
                    build_btn = loc
                    break
            
            if not build_btn:
                self.log.warning("No se encontró el botón de construir activo.")
                return False
                
            build_btn.click()
            
            # Esperar a que se procese
            try:
                self.page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                time.sleep(1)
                
            return not self._is_error_page()
            
        except Exception as e:
            self.log.warning("Excepción durante la fabricación en interfaz: %s", e)
            return False

    def build_defense(self, planet: Planet, what: str, amount: int) -> bool:
        if self._act(f"Construir defensa {amount}x {what} en {planet.coords}"):
            return True
        tech_id = TECH_IDS.get(what)
        if tech_id is None:
            self.log.warning("Sin tech_id para defensa: %s", what)
            return False
        self._goto("defenses", planet)
        self._wait_tech()

        # Usar nuevo método UI unificado
        if self._build_units_ui(tech_id, amount):
            self.log.info("Defensa iniciada: %dx %s en %s", amount, what, planet.coords)
            return True
        else:
            self.log.warning("No pude construir defensa %s en %s.", what, planet.coords)
            return False

    def build_lifeform(self, planet: Planet) -> bool:
        """
        Navega a la página de Formas de vida e intenta construir el primer edificio
        disponible (asequible y sin cola activa). Devuelve True si inició algo.
        """
        if self._act(f"Forma de vida en {planet.coords}"):
            return True
        if not planet.lifeform_available:
            return False
        self._goto("lfbuildings", planet)
        if self._is_error_page():
            self.log.info("Formas de vida no disponibles en %s; desactivando.", planet.coords)
            planet.lifeform_available = False
            return False
        try:
            self._wait_tech(timeout=6000)
        except Exception:
            self.log.debug("Página de Forma de vida no disponible en %s.", planet.coords)
            return False
        if self._is_lf_queue_active():
            self.log.debug("Cola Forma de vida ocupada en %s.", planet.coords)
            planet.lifeform_in_progress = True
            return False
        # Buscar el primer botón de upgrade activo (no deshabilitado)
        js = """() => {
            for (const li of document.querySelectorAll('li[data-technology]')) {
                const btn = li.querySelector('button.upgrade:not([disabled]):not(.disabled)');
                if (btn) return li.getAttribute('data-technology');
            }
            return null;
        }"""
        try:
            tid = self.page.evaluate(js)
        except Exception:
            return False
        if not tid:
            self.log.debug("Sin edificios de Forma de vida disponibles en %s.", planet.coords)
            return False
        if self._click_upgrade(int(tid)):
            try:
                self.page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                time.sleep(2)
            if self._is_error_page():
                self.log.warning("OGame reportó error al construir forma de vida en %s.", planet.coords)
                return False
            self.log.info("Forma de vida: construcción iniciada (tech=%s) en %s", tid, planet.coords)
            planet.lifeform_in_progress = True
            return True
        return False

    def build_ships(self, planet: Planet, ship: str, amount: int) -> bool:
        if self._act(f"Fabricar {amount}x {ship} en {planet.coords}"):
            return True
        tech_id = TECH_IDS.get(ship)
        if tech_id is None:
            self.log.warning("Sin tech_id para nave: %s", ship)
            return False
        self._goto("shipyard", planet)
        self._wait_tech()
        
        # Usar nuevo método UI unificado
        if self._build_units_ui(tech_id, amount):
            self.log.info("Fabricación iniciada: %dx %s en %s", amount, ship, planet.coords)
            return True
        else:
            self.log.warning("No pude fabricar %s en %s.", ship, planet.coords)
            return False

    # ------------------------------------------------------------------
    #  Lectura de informes de espionaje
    # ------------------------------------------------------------------
    def read_all_spy_reports(self) -> Dict[str, EspionageReport]:
        """
        Navega a mensajes y extrae todos los informes de espionaje visibles.
        Devuelve {coords_str: EspionageReport} donde coords_str = "G:S:P".
        """
        for url_suffix in ["&tab=20", ""]:
            try:
                self.page.goto(
                    f"{self.cfg.server_url.rstrip('/')}/game/"
                    f"index.php?page=ingame&component=messages{url_suffix}",
                    wait_until="domcontentloaded", timeout=15000,
                )
                self._delay()
                break
            except Exception:
                continue
        # Intentar hacer clic en el tab de espionaje si existe
        self._click_first([
            "a[href*='tab=20']", "li[data-tab='20'] a",
            ".tabLabel:has-text('Espionaje') a",
            ".tabLabel:has-text('Espionage') a",
        ], timeout_ms=2000)
        time.sleep(1)
        try:
            raw_list = self.page.evaluate(_JS_ALL_SPY_REPORTS) or []
        except Exception as e:
            self.log.debug("Error leyendo informes espionaje: %s", e)
            return {}
        reports: Dict[str, EspionageReport] = {}
        for raw in raw_list:
            try:
                g, s, p = raw["galaxy"], raw["system"], raw["position"]
                key = f"{g}:{s}:{p}"
                if key in reports:
                    continue  # el primero en la lista es el más reciente
                res = raw.get("resources", {})
                reports[key] = EspionageReport(
                    coords=Coords(g, s, p),
                    player_name=raw.get("player_name", ""),
                    is_inactive=raw.get("is_inactive", False),
                    resources=Resources(
                        metal=float(res.get("metal", 0)),
                        crystal=float(res.get("crystal", 0)),
                        deut=float(res.get("deut", 0)),
                    ),
                    fleet={k: int(v) for k, v in raw.get("fleet", {}).items()},
                    defense={k: int(v) for k, v in raw.get("defense", {}).items()},
                    timestamp=time.time(),
                )
            except Exception as e:
                self.log.debug("Error parseando informe espionaje: %s", e)
        self.log.info("Informes de espionaje leídos: %d", len(reports))
        return reports

    def read_spy_report(self, target: Coords) -> Optional[EspionageReport]:
        """Lee el informe de espionaje más reciente para las coordenadas dadas."""
        reports = self.read_all_spy_reports()
        return reports.get(f"{target.galaxy}:{target.system}:{target.position}")

    # ------------------------------------------------------------------
    #  Espionaje
    # ------------------------------------------------------------------
    def espionage(self, target: Coords, probes: int = 4) -> Optional[EspionageReport]:
        """Envía sondas, espera retorno y devuelve el informe parseado."""
        if self.cfg.dry_run:
            self.log.info("[DRY-RUN] Espiar %s con %d sondas", target, probes)
            return None
        # Reutilizar informe reciente (< 30 min)
        report = self.read_spy_report(target)
        if report and (time.time() - report.timestamp) < 1800:
            self.log.info("Informe reciente para %s (reutilizando).", target)
            return report
        # Enviar sondas
        origin = self._planet_cache[0].coords if self._planet_cache else None
        if not origin:
            self.log.warning("espionage: sin planeta origen en caché.")
            return None
        if not self.send_fleet(origin, target, {"espionage_probe": probes},
                               mission="espionage"):
            self.log.warning("espionage: no se pudieron enviar sondas a %s.", target)
            return None
        # Esperar retorno (máx 3 min)
        self.log.info("Sondas enviadas -> %s. Esperando retorno (máx 3 min)...", target)
        coord_str = f"{target.galaxy}:{target.system}:{target.position}"
        deadline = time.time() + 180
        while time.time() < deadline:
            time.sleep(10)
            mvs = self.read_movements()
            in_flight = any(
                m.get("mission") in ("6", "espionage", "Espionage") and
                coord_str in m.get("destination", "")
                for m in mvs
            )
            if not in_flight:
                time.sleep(3)
                break
        return self.read_spy_report(target)

    # ------------------------------------------------------------------
    #  Envío de flota (OGame Redesign: 2 pasos — naves → destino+misión)
    # ------------------------------------------------------------------
    def _set_speed(self, sp_val: int) -> bool:
        """
        Establece la velocidad de la flota (1 a 10, que corresponde a 10% a 100%).
        Utiliza clics reales de Playwright para asegurar la compatibilidad con los frameworks SPA de OGame.
        """
        if sp_val > 10 and sp_val <= 100:
            sp_val = int(sp_val / 10)
            
        selectors = [
            f".steps .step[data-step='{sp_val}']",
            f".steps [data-step='{sp_val}']",
            f"[data-step='{sp_val}'].step",
            f"ul.speed li.step{sp_val * 10}",
            f"ul.speed li[data-value='{sp_val * 10}']",
            f".speedLinks a.step{sp_val * 10}",
            f"a[onclick*='selectSpeed({sp_val * 10})']",
            f"a[onclick*='selectSpeed({sp_val})']",
        ]
        
        for sel in selectors:
            try:
                locator = self.page.locator(sel).first
                if locator.count() > 0:
                    locator.click(timeout=500)
                    self.log.debug("Velocidad %d%% establecida con éxito usando: %s", sp_val * 10, sel)
                    return True
            except Exception:
                pass
                
        try:
            res = self.page.evaluate(_JS_SET_SPEED, str(sp_val))
            if res:
                self.log.debug("Velocidad %d%% establecida con JS: %s", sp_val * 10, res)
                return True
        except Exception:
            pass
            
        try:
            slider = self.page.locator("#speedPercent, input[name='speed'], input[type='range']").first
            if slider.count() > 0:
                slider.fill(str(sp_val))
                slider.dispatch_event("input")
                slider.dispatch_event("change")
                self.log.debug("Velocidad %d%% establecida vía input range fill", sp_val * 10)
                return True
        except Exception:
            pass
            
        return False

    def send_fleet(self, origin: Coords, destination: Coords, ships: Dict[str, int],
                   mission: str, resources = None,
                   speed_percent: float = 1.0, hold_hours: float = 0.0,
                   target_duration_s: Optional[float] = None,
                   max_round_trip_s: Optional[float] = None) -> bool:
        desc = (f"Flota {ships} {origin}->{destination} mision={mission} "
                f"vel={int(speed_percent * 100)}%")
        if self._act(desc):
            return True

        self.last_flight_seconds = None  # tiempo de vuelo real (un sentido) leído del juego
        origin_planet = self._planet_by_coords(origin)
        self._goto("fleet", origin_planet)
        try:
            self.page.wait_for_selector(
                "li[data-technology], #fleetdispatchscreen, .fleet-dispatch-ships",
                timeout=10000,
            )
        except Exception:
            pass
        self._delay()

        # Si resources es "all", leer los recursos del planeta desde la página actual
        resources_to_carry = None
        if resources == "all":
            resources_to_carry = self.read_resources()
            self.log.info("send_fleet: leídos recursos para fleetsave: %s", resources_to_carry)
        elif resources is not None:
            resources_to_carry = resources

        # ── Paso 1: seleccionar naves ──────────────────────────────────
        any_selected = False
        if not ships:
            # Si ships está vacío, intentar seleccionar todas las naves del planeta (ej: para fleetsave)
            self.log.info("send_fleet: seleccionando todas las naves del planeta.")
            clicked_all = self._click_first([
                "#sendall", "a#sendall", "a.allShips", "#sendallBtn", "a#sendallBtn",
                "a:has-text('Todas las naves')", "a:has-text('All ships')",
                "a:has-text('Enviar todas')", "a.allShips:not(.disabled)",
                "a#sendallships"
            ])
            if clicked_all:
                any_selected = True
                self.log.debug("send_fleet: clicado botón 'seleccionar todas las naves'.")
            else:
                self.log.warning("send_fleet: no encontré botón de 'seleccionar todas', intentando por JS...")
                try:
                    js = """() => {
                        let clicked = false;
                        document.querySelectorAll('li[data-technology]').forEach(li => {
                            const inp = li.querySelector('input');
                            if (inp) {
                                const maxVal = li.querySelector('.amount')?.textContent.trim().replace(/\./g, '');
                                if (maxVal && parseInt(maxVal) > 0) {
                                    const nativeSetter = Object.getOwnPropertyDescriptor(
                                        window.HTMLInputElement.prototype, 'value'
                                    ).set;
                                    nativeSetter.call(inp, maxVal);
                                    inp.dispatchEvent(new Event('input', {bubbles:true}));
                                    inp.dispatchEvent(new Event('change', {bubbles:true}));
                                    clicked = true;
                                }
                            }
                        });
                        return clicked;
                    }"""
                    any_selected = self.page.evaluate(js)
                except Exception as e:
                    self.log.warning("send_fleet: error al seleccionar todas las naves via JS: %s", e)
        else:
            # Usamos el setter nativo de HTMLInputElement para compatibilidad React/SPA
            for ship_name, amount in ships.items():
                if amount <= 0:
                    continue
                tech_id = TECH_IDS.get(ship_name)
                if not tech_id:
                    self.log.warning("send_fleet: tech_id desconocido para %s.", ship_name)
                    continue
                # Intento 1: Playwright fill (requiere is_visible)
                filled = self._fill_first([
                    f"li[data-technology='{tech_id}'] input.amount",
                    f"li[data-technology='{tech_id}'] input[name='amount']",
                    f"#ship_{tech_id}",
                    f"input[data-ship='{tech_id}']",
                    f"li[data-technology='{tech_id}'] input",
                ], str(amount))
                # Intento 2: JS con setter nativo React-compatible
                if not filled:
                    try:
                        js = f"""() => {{
                            const tid = {tech_id};
                            const val = '{amount}';
                            const nativeSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value'
                            ).set;
                            const setVal = (el) => {{
                                nativeSetter.call(el, val);
                                el.dispatchEvent(new Event('input', {{bubbles:true}}));
                                el.dispatchEvent(new Event('change', {{bubbles:true}}));
                                el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles:true}}));
                                el.blur();
                            }};
                            for (const id of ['ship_' + tid, 'ship' + tid]) {{
                                const el = document.getElementById(id);
                                if (el) {{ setVal(el); return 'id:' + id; }}
                            }}
                            const li = document.querySelector('li[data-technology="' + tid + '"]');
                            if (!li) return null;
                            const inp = li.querySelector('input');
                            if (inp) {{ setVal(inp); return 'li'; }}
                            return null;
                        }}"""
                        result = self.page.evaluate(js)
                        filled = result is not None
                        if filled:
                            self.log.debug("send_fleet: nave %s seleccionada via JS (%s).", ship_name, result)
                    except Exception:
                        pass
                if filled:
                    try:
                        self.page.locator(f"li[data-technology='{tech_id}'] input").first.press("Tab")
                    except Exception:
                        pass
                    any_selected = True
                else:
                    self.log.warning(
                        "send_fleet: no encontré input para %s (id=%d). "
                        "¿Tienes esa nave en el planeta?", ship_name, tech_id
                    )

        if not any_selected:
            try:
                techs = self.page.evaluate(
                    "() => [...document.querySelectorAll('li[data-technology]')]"
                    ".map(l => l.getAttribute('data-technology'))"
                )
                self.log.warning("send_fleet: sin naves; techs en página=%s", techs)
            except Exception:
                pass
            return False

        # Botón paso 1→2
        # Esperar a que el botón de continuar esté listo (no tenga la clase 'off')
        try:
            self.page.wait_for_selector("#continueToFleet2:not(.off)", timeout=4000)
        except Exception:
            if self.page.locator("#continueToFleet2").count() > 0:
                self.log.warning("send_fleet: el botón continuar #continueToFleet2 está desactivado (clase 'off').")
                return False

        if not self._click_first([
            "#continueToFleet2", "a#continueToFleet2", "input#continueToFleet2",
            "a:has-text('Siguiente')", "button:has-text('Continue')",
            ".btn_blue_middle", "a.btn_blue_small",
        ]):
            self.log.warning("send_fleet: no encontré botón continuar (paso 1→2).")
            return False

        # ── Paso 2: destino + misión + envío ──────────────────────────
        # En OGame Redesign el paso 2 es la pantalla final (no hay paso 3 separado).
        # Esperamos cualquier indicador de la pantalla de destino o de envío.
        try:
            self.page.wait_for_selector(
                "input#galaxy, input[name='galaxy'], #sendFleet, a:has-text('Enviar')",
                timeout=8000,
            )
        except Exception:
            time.sleep(2)
        self._delay()

        # Destino
        try:
            ok = self.page.evaluate(f"""() => {{
                const setVal = (id, val) => {{
                    const el = document.getElementById(id) || document.querySelector('input[name="' + id + '"]');
                    if (el) {{
                        el.focus();
                        const nativeSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        nativeSetter.call(el, String(val));
                        el.dispatchEvent(new Event('input', {{bubbles: true}}));
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                        el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles: true, keyCode: 13, which: 13}}));
                        el.blur();
                        return true;
                    }}
                    return false;
                }};
                const g = setVal("galaxy", "{destination.galaxy}");
                const s = setVal("system", "{destination.system}");
                const p = setVal("position", "{destination.position}");
                return g && s && p;
            }}""")
            if not ok:
                raise RuntimeError("No se encontraron todos los campos de coordenadas en el DOM")
            self.log.info("Coordenadas establecidas vía JS: %s", destination)
        except Exception as e:
            self.log.warning("Fallo al rellenar coordenadas con JS: %s. Reintentando con Playwright...", e)
            self._fill_first(["input#galaxy", "input[name='galaxy']"], str(destination.galaxy))
            self._fill_first(["input#system", "input[name='system']"], str(destination.system))
            self._fill_first(["input#position", "input[name='position']"], str(destination.position))

        # Esperar a que los inputs de coordenadas se procesen y OGame termine su validación AJAX
        time.sleep(1.5)

        dest_type = TYPE_CODES.get(destination.type, 1)
        btn_id = ""
        if dest_type == 1:
            btn_id = "#pbutton"
        elif dest_type == 2:
            btn_id = "#dbutton"
        elif dest_type == 3:
            btn_id = "#mbutton"

        selectors = []
        if btn_id:
            selectors.extend([btn_id, f"a{btn_id}", f"div{btn_id}", f"button{btn_id}"])
        selectors.extend([
            f"input[name='type'][value='{dest_type}']",
            f"a[data-type='{dest_type}']",
            f"label[for='type{dest_type}']",
        ])

        clicked = False
        for attempt in range(3):
            if self._click_first(selectors):
                # Verificar si el botón tiene la clase 'selected' (e.g. debris_selected)
                time.sleep(0.5)
                if btn_id:
                    try:
                        classes = self.page.locator(btn_id).first.get_attribute("class") or ""
                        if "selected" in classes:
                            clicked = True
                            self.log.info("Tipo de destino %s seleccionado con éxito.", destination.type)
                            break
                    except Exception:
                        pass
                else:
                    clicked = True
                    break
            self.log.warning("Intento %d: Fallo al seleccionar tipo de destino %s. Reintentando...", attempt + 1, destination.type)
            time.sleep(1.0)

        if not clicked:
            self.log.warning("Fallo definitivo al seleccionar tipo de destino %s", destination.type)

        # Misión (radio button o enlace) - Seleccionada PRIMERO para habilitar el cálculo de velocidad y duración
        mission_code = MISSION_CODES.get(mission, 1)
        self._click_first([
            f"#missionButton{mission_code}",
            f"a#missionButton{mission_code}",
            f"[data-mission='{mission_code}']",
            f"label[for='mission{mission_code}']",
            f"input[name='mission'][value='{mission_code}']",
            f"#mission{mission_code}",
            f"a.mission{mission_code}",
            f"a[onclick*='mission={mission_code}']",
        ])
        time.sleep(1.0)  # Espera corta para que OGame cargue la duración inicial tras seleccionar misión

        # Velocidad
        try:
            debug_dom = self.page.evaluate("""() => {
                const results = [];
                document.querySelectorAll('*').forEach(el => {
                    const text = el.textContent ? el.textContent.trim() : '';
                    if (text === '10%' || text === '40%') {
                        results.push({
                            tagName: el.tagName,
                            id: el.id,
                            className: el.className,
                            text: text,
                            html: el.outerHTML.substring(0, 150)
                        });
                    }
                });
                return results;
            }""")
            self.log.info("DEBUG DOM speed elements: %s", debug_dom)
            
            debug_dur = self.page.evaluate("""() => {
                const results = [];
                document.querySelectorAll('*').forEach(el => {
                    const id = el.id ? String(el.id).toLowerCase() : '';
                    const cls = el.className ? String(el.className).toLowerCase() : '';
                    if (id.includes('dur') || cls.includes('dur') || id.includes('time') || cls.includes('time')) {
                        results.push({
                            tagName: el.tagName,
                            id: el.id,
                            className: el.className,
                            text: el.textContent ? el.textContent.trim() : ''
                        });
                    }
                });
                return results;
            }""")
            self.log.info("DEBUG DOM duration/time elements: %s", debug_dur)
        except Exception as e:
            self.log.warning("DEBUG DOM failed: %s", e)

        if target_duration_s is not None and target_duration_s > 0:
            best_sp = 1  # 10% por defecto
            durations = {}
            
            # Esperar a que la duración del vuelo se calcule (sea distinta de 0:00:00)
            start_wait_dur = time.time()
            initial_dur = "0:00:00 h"
            while time.time() - start_wait_dur < 4.0:
                val = self.page.evaluate("""() => {
                    const el = document.getElementById('duration') || document.querySelector('.duration') || document.querySelector('#duration .value') || document.querySelector('.flightTime');
                    return el ? el.textContent.trim() : null;
                }""")
                if val and val not in ("0:00:00", "0:00:00 h", "00:00:00", "00:00:00 h", "0:00:00h", "00:00:00h"):
                    initial_dur = val
                    break
                time.sleep(0.2)
            
            last_duration = initial_dur
            
            for sp in range(1, 11):
                try:
                    self._set_speed(sp)
                    
                    # Esperar hasta 600ms a que la duración cambie respecto a last_duration
                    start_w = time.time()
                    current_dur = None
                    while time.time() - start_w < 0.6:
                        dur_str = self.page.evaluate("""() => {
                            const el = document.getElementById('duration') || document.querySelector('.duration') || document.querySelector('#duration .value') || document.querySelector('.flightTime');
                            return el ? el.textContent.trim() : null;
                        }""")
                        if dur_str and dur_str != last_duration:
                            current_dur = dur_str
                            break
                        time.sleep(0.05)
                    
                    if not current_dur:
                        # Fallback a leer lo que haya
                        current_dur = dur_str
                        
                    if current_dur:
                        last_duration = current_dur
                        seconds = self._parse_duration_to_seconds(current_dur)
                        if seconds:
                            durations[sp] = seconds
                except Exception as e:
                    self.log.debug("Error leyendo duración para velocidad %d: %s", sp, e)
            
            self.log.info("Duraciones leídas en pantalla por velocidad: %s (objetivo: %s s)", 
                           {f"{k*10}%": v for k, v in durations.items()}, int(target_duration_s))
            
            # Buscar el porcentaje más alto donde la duración sea >= target_duration_s
            valid_speeds = [sp for sp, sec in durations.items() if sec >= target_duration_s]
            if valid_speeds:
                best_sp = max(valid_speeds)
            elif durations:
                best_sp = min(durations.keys())
                
            self.log.info("Seleccionando velocidad dinámica optimizada: %d%%", best_sp * 10)
            self._set_speed(best_sp)
        else:
            sp_val = max(1, min(10, round(speed_percent * 10)))
            self._set_speed(sp_val)

        time.sleep(0.5)

        # Leer la duración REAL de vuelo (un sentido) directamente de la página de envío
        try:
            dur_txt = self.page.evaluate("""() => {
                const el = document.getElementById('duration') || document.querySelector('#duration .value') || document.querySelector('.duration') || document.querySelector('.flightTime');
                return el ? el.textContent.trim() : null;
            }""")
            secs = self._parse_duration_to_seconds(dur_txt) if dur_txt else None
            if secs and secs > 0:
                self.last_flight_seconds = secs
        except Exception:
            pass

        # Decidir con el tiempo REAL (no estimado): si no vuelve antes del límite
        # (p.ej. el descanso nocturno), cancelar el envío en vez de adivinar.
        if max_round_trip_s is not None and self.last_flight_seconds:
            hold_s = hold_hours * 3600 if mission == "expedition" else 0
            real_round = 2 * self.last_flight_seconds + hold_s
            if real_round > max_round_trip_s:
                self.log.info("Envío %s->%s cancelado: vuelo real %.1f min (ida+vuelta+permanencia) > %.1f min disponibles antes del descanso.",
                              origin, destination, real_round / 60.0, max_round_trip_s / 60.0)
                return False

        # Recursos (transporte/deploy)
        if resources_to_carry and mission in ("transport", "deploy"):
            self._fill_first(["#deuterium", "input[name='deuterium']"],
                             str(int(resources_to_carry.deut)))
            self._fill_first(["#crystal", "input[name='crystal']"],
                             str(int(resources_to_carry.crystal)))
            self._fill_first(["#metal", "input[name='metal']"],
                             str(int(resources_to_carry.metal)))

        # Tiempo de estancia (expedición)
        if hold_hours > 0 and mission == "expedition":
            hold_min = max(1, min(32767, int(hold_hours * 60)))
            self._fill_first(["#holdingtime", "input[name='holdingtime']"], str(hold_min))

        time.sleep(0.5)

        # Enviar flota
        try:
            self.page.wait_for_selector("#sendFleet:not(.off)", timeout=5000)
        except Exception:
            if self.page.locator("#sendFleet").count() > 0:
                self.log.warning("send_fleet: el botón de envío #sendFleet está visible pero desactivado (clase 'off').")
                try:
                    os.makedirs("errors", exist_ok=True)
                    self.page.screenshot(path=f"errors/fleet_send_disabled_{int(time.time())}.png")
                except Exception:
                    pass
                return False

        if not self._click_first([
            "#sendFleet", "input#sendFleet", "button#sendFleet",
            "a:has-text('Enviar Flota')", "a:has-text('Enviar')",
            "button:has-text('Send Fleet')", "button:has-text('Send')",
            ".btn_blue_large", "input[value='Send Fleet']",
        ]):
            self.log.warning("send_fleet: no encontré botón de envío.")
            try:
                os.makedirs("errors", exist_ok=True)
                self.page.screenshot(path=f"errors/fleet_send_{int(time.time())}.png")
                btns = self.page.evaluate(
                    "() => [...document.querySelectorAll('a,button,input[type=submit]')]"
                    ".filter(e=>e.offsetParent!==null)"
                    ".map(e=>e.id+':'+e.className+':'+e.textContent.trim().slice(0,20))"
                )
                self.log.warning("send_fleet: botones visibles=%s", btns[:15])
            except Exception:
                pass
            return False

        try:
            self.page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            time.sleep(2)

        if self._is_error_page():
            self.log.warning("OGame reportó error enviando flota %s->%s.", origin, destination)
            return False

        # Confirmar que la flota SALIÓ de verdad. Un envío correcto abandona la pantalla
        # de despacho (OGame navega a 'movement'); si seguimos viendo #sendFleet visible,
        # no salió (slot lleno, combustible, recálculo o error inline) aunque el clic
        # "funcionara". Evita el falso "Flota enviada".
        time.sleep(1.0)
        try:
            still_on_dispatch = self.page.evaluate("""() => {
                const btn = document.querySelector('#sendFleet');
                return !!(btn && btn.offsetParent !== null);
            }""")
        except Exception:
            still_on_dispatch = False

        if still_on_dispatch:
            err_txt = ""
            try:
                err_txt = self.page.evaluate("""() => {
                    const sel = '#fleetStatusBar, .fleetStatusBar, .error_text, .fleetError, ' +
                                '.error_box, #errorBoxDecision, .alert_box, .noships, .fleet_error';
                    for (const e of document.querySelectorAll(sel)) {
                        const t = (e.textContent || '').trim();
                        if (t) return t.slice(0, 200);
                    }
                    return '';
                }""") or ""
            except Exception:
                pass
            self.log.warning("send_fleet: se hizo clic en enviar pero la flota %s->%s NO salió "
                             "(seguimos en la pantalla de despacho). %s",
                             origin, destination,
                             ("Motivo: " + err_txt) if err_txt else "(sin mensaje inline visible)")
            try:
                os.makedirs("errors", exist_ok=True)
                self.page.screenshot(path=f"errors/fleet_not_sent_{int(time.time())}.png")
            except Exception:
                pass
            return False

        self.log.info("Flota enviada: %s -> %s (%s)", origin, destination, mission)
        return True

    def read_message_reports(self, tab_id: int) -> List[dict]:
        """
        Navega a la pestaña indicada de mensajes y devuelve, por cada mensaje:
            { "id": str, "text": str, "raw": dict }
        donde `raw` contiene todos los atributos data-raw-* hallados en el mensaje
        (cifras fiables, independientes del idioma). `text` es el cuerpo completo
        como respaldo.
        tab_id: 21 (Combates), 22 (Expediciones), 24 (Otros/Reciclaje).
        """
        try:
            self.page.goto(
                f"{self.cfg.server_url.rstrip('/')}/game/"
                f"index.php?page=ingame&component=messages&tab={tab_id}",
                wait_until="domcontentloaded", timeout=15000,
            )
            self._delay()
        except Exception:
            return []

        # Comprobar si la pestaña ya está activa en el DOM
        already_active = self.page.evaluate(f"""() => {{
            const tab = document.querySelector("li[data-tab='{tab_id}'], [data-subtab-id='{tab_id}'], .tabLabel[data-tab='{tab_id}']");
            return tab ? (tab.classList.contains('active') || tab.classList.contains('selected')) : false;
        }}""")

        # Si no está activa, registramos el primer mensaje visible actual para detectar el refresco
        first_id_before = ""
        if not already_active:
            first_id_before = self.page.evaluate("""() => {
                const first = document.querySelector('.msg, li.msg, .message_element, tr.message');
                return first ? (first.getAttribute('data-msg-id') || first.id || '') : '';
            }""")

            # OGame es una SPA: la pestaña no siempre cambia solo con la URL. Hacemos
            # clic explícito en el tab.
            self._click_first([
                f"a[href*='tab={tab_id}']",
                f"li[data-tab='{tab_id}'] a",
                f"li[data-tab='{tab_id}']",
                f"[data-subtab-id='{tab_id}']",
                f".tabLabel[data-tab='{tab_id}']",
            ], timeout_ms=3000)

            # Esperar hasta que el primer mensaje cambie o la lista se vacíe (máximo 4.5 segundos)
            start_time = time.time()
            while time.time() - start_time < 4.5:
                first_id_now = self.page.evaluate("""() => {
                    const first = document.querySelector('.msg, li.msg, .message_element, tr.message');
                    return first ? (first.getAttribute('data-msg-id') || first.id || '') : '';
                }""")
                if first_id_now != first_id_before:
                    break
                time.sleep(0.3)
        
        try:
            self.page.wait_for_selector(
                ".msg, li.msg, .message_element, tr.message", timeout=4000)
        except Exception:
            pass
        time.sleep(1)

        js = """() => {
            const results = [];
            document.querySelectorAll('.msg, li.msg, .message_element, tr.message').forEach(el => {
                const id = el.getAttribute('data-msg-id') || el.id || '';
                if (!id) return;
                const text = el.innerText || '';
                // Recoge todos los atributos data-raw-* del nodo y sus descendientes.
                const raw = {};
                const collect = node => {
                    if (!node.attributes) return;
                    for (const a of node.attributes) {
                        if (a.name.indexOf('data-raw-') === 0) {
                            raw[a.name.slice(9)] = a.value;
                        }
                    }
                };
                collect(el);
                el.querySelectorAll('*').forEach(collect);
                results.push({ id, text, raw });
            });
            return results;
        }"""
        try:
            return self.page.evaluate(js) or []
        except Exception as e:
            self.log.debug("Error leyendo mensajes tab=%d: %s", tab_id, e)
            return []

    def read_message_full(self, raw_id: str) -> str:
        """Abre (expande) un mensaje ya visible en la pestaña de mensajes actual y devuelve su
        texto completo. La fila de la lista solo trae un resumen; el cuerpo (coords del origen,
        % de contra-espionaje, etc.) se carga al hacer clic. Devuelve '' si no se puede abrir.
        Debe llamarse con la pestaña de mensajes ya cargada (p.ej. tras read_message_reports)."""
        sel = f"[data-msg-id='{raw_id}'], [id='{raw_id}']"
        try:
            el = self.page.query_selector(sel)
            if not el:
                return ""
            before = el.inner_text() or ""
            el.click()
            start = time.time()
            while time.time() - start < 3:
                time.sleep(0.3)
                cur = self.page.query_selector(sel)
                txt = (cur.inner_text() if cur else "") or ""
                if len(txt) > len(before) + 15:  # el cuerpo ya cargó
                    return txt
            cur = self.page.query_selector(sel)
            return (cur.inner_text() if cur else before) or before
        except Exception as e:
            self.log.debug("No se pudo abrir el mensaje %s: %s", raw_id, e)
            return ""

    # ------------------------------------------------------------------
    #  Movimientos y escombros
    # ------------------------------------------------------------------
    def read_movements(self, detailed: bool = False) -> List[dict]:
        """Lee movimientos de flota activos.

        detailed=True usa la página de movimientos (component=movement), que SÍ trae el
        desglose por nave de cada flota propia en su tooltip — imprescindible para sumar las
        naves en vuelo (sobre todo expediciones, miles de cargueros). event_list es más
        ligero y muestra los ataques entrantes (lo usamos para el escape), pero no incluye
        esa composición, así que con él las expediciones cuentan 0 naves.
        """
        self._goto("movement" if detailed else "event_list")
        try:
            self.page.wait_for_selector(
                ".eventFleet, tr.flightEventRow, .fleetDetails, .fleet_row",
                timeout=5000,
            )
        except Exception:
            pass
        try:
            data = self.page.evaluate(_JS_READ_MOVEMENTS) or []
        except Exception as e:
            self.log.debug("Error leyendo movimientos: %s", e)
            return []
        # Diagnóstico opcional: vuelca el HTML crudo de las filas de flota para depurar el
        # parseo (tipo luna/planeta, reversal/hora de vuelta). Se activa con la variable de
        # entorno OGBOT_DUMP_MOVEMENTS=1 o creando un fichero 'dump_movements.txt' junto al bot
        # (cómodo en Docker); el fichero se borra tras volcar una vez.
        flag_file = "dump_movements.txt"
        if os.environ.get("OGBOT_DUMP_MOVEMENTS") or os.path.exists(flag_file):
            try:
                html = self.page.evaluate(
                    "() => Array.from(document.querySelectorAll("
                    "'.eventFleet, .fleetDetails, .fleet_row, tr.flightEventRow'))"
                    ".map(r => r.outerHTML).join('\\n\\n<!-- ===== fila ===== -->\\n\\n')"
                )
                with open("movement_debug.html", "w", encoding="utf-8") as f:
                    f.write(html or "")
                rows_dumped = (html.count("===== fila =====") + 1) if html else 0
                self.log.info("Volcado de movimientos en movement_debug.html (%d filas, %d parseadas).",
                              rows_dumped, len(data))
                try:
                    if os.path.exists(flag_file):
                        os.remove(flag_file)
                except OSError:
                    pass
            except Exception as e:
                self.log.debug("No se pudo volcar movement_debug.html: %s", e)
        return data

    def read_fleet_slots(self) -> Optional[Dict[str, int]]:
        """Lee el indicador real Flotas: X/Y y Expediciones: X/Y del juego."""
        self._goto("fleet")
        try:
            self.page.wait_for_selector("#fleetdispatchcomponent", timeout=5000)
        except Exception:
            pass
        try:
            data = self.page.evaluate("""() => {
                const r = {};
                const slotsEl = document.querySelector('#slots .fleetSlots .value, #slotValue, .fleetStatus .slot_count');
                if (slotsEl) {
                    const m = slotsEl.textContent.match(/(\\d+)\\s*\\/\\s*(\\d+)/);
                    if (m) { r.fleet_used = parseInt(m[1]); r.fleet_total = parseInt(m[2]); }
                }
                const expeEl = document.querySelector('#slots .expSlots .value, #expeValue, .fleetStatus .expe_count');
                if (expeEl) {
                    const m = expeEl.textContent.match(/(\\d+)\\s*\\/\\s*(\\d+)/);
                    if (m) { r.expe_used = parseInt(m[1]); r.expe_total = parseInt(m[2]); }
                }
                if (!r.fleet_used && r.fleet_used !== 0) {
                    const text = document.body.innerText;
                    const fm = text.match(/Flotas\\s*:?\\s*(\\d+)\\s*\\/\\s*(\\d+)/i) ||
                               text.match(/Fleets\\s*:?\\s*(\\d+)\\s*\\/\\s*(\\d+)/i);
                    if (fm) { r.fleet_used = parseInt(fm[1]); r.fleet_total = parseInt(fm[2]); }
                    const em = text.match(/Expediciones\\s*:?\\s*(\\d+)\\s*\\/\\s*(\\d+)/i) ||
                               text.match(/Expeditions\\s*:?\\s*(\\d+)\\s*\\/\\s*(\\d+)/i);
                    if (em) { r.expe_used = parseInt(em[1]); r.expe_total = parseInt(em[2]); }
                }
                return (r.fleet_used !== undefined || r.expe_used !== undefined) ? r : null;
            }""")
            if data:
                self.log.info("Fleet slots del juego: Flotas %d/%d, Expediciones %d/%d",
                              data.get("fleet_used", 0), data.get("fleet_total", 0),
                              data.get("expe_used", 0), data.get("expe_total", 0))
            return data
        except Exception as e:
            self.log.debug("Error leyendo fleet slots: %s", e)
            return None

    def read_hourly_production(self, planet) -> Optional[Dict[str, int]]:
        """Lee la producción REAL por hora (metal/cristal/deut) de la página de ajustes de
        recursos del planeta. Incluye oficiales, clase, formas de vida e items — más fiable
        que la estimación por niveles de mina. Devuelve None si no se pudo leer."""
        pid = str(getattr(planet, "id", "")).replace("planet-", "").replace("moon-", "")
        if not pid:
            return None
        url = (f"{self.cfg.server_url.rstrip('/')}/game/"
               f"index.php?page=ingame&component=resourcesettings&cp={pid}")
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=12000)
            self._delay()
        except Exception:
            return None
        js = r"""() => {
            const clean = s => parseInt(String(s||'').replace(/[^0-9-]/g, '')) || 0;
            for (const tr of document.querySelectorAll('tr')) {
                const label = (tr.innerText || '').toLowerCase();
                if (label.includes('total por hora') || label.includes('total per hour')
                        || label.includes('gesamt pro stunde')) {
                    const nums = [];
                    tr.querySelectorAll('td, th').forEach(c => {
                        const v = (c.textContent || '').trim();
                        // celdas puramente numéricas (excluye la celda de la etiqueta)
                        if (/[0-9]/.test(v) && !/[a-z]/i.test(v)) nums.push(clean(v));
                    });
                    if (nums.length >= 3) return {metal: nums[0], crystal: nums[1], deut: nums[2]};
                }
            }
            return null;
        }"""
        try:
            data = self.page.evaluate(js)
            if data:
                self.log.info("Producción real %s: M/h=%d C/h=%d D/h=%d",
                              planet.coords, data.get("metal", 0), data.get("crystal", 0), data.get("deut", 0))
            return data
        except Exception as e:
            self.log.debug("Error leyendo producción de %s: %s", getattr(planet, "coords", "?"), e)
            return None

    def read_debris_fields(self, planets_with_recyclers: List[Planet] = None) -> List[dict]:
        """Busca campos de escombros en los sistemas de nuestros planetas y alrededores."""
        targets = planets_with_recyclers if planets_with_recyclers is not None else self._planet_cache
        if not targets:
            return []
        debris_list = []
        seen_systems: set = set()
        
        search_range = getattr(self.cfg, "recycling_system_range", 0)
        min_debris = getattr(self.cfg, "recycling_min_debris", 10000)
        
        for planet in targets:
            c = planet.coords
            min_sys = max(1, c.system - search_range)
            max_sys = min(499, c.system + search_range)
            
            for sys in range(min_sys, max_sys + 1):
                key = (c.galaxy, sys)
                if key in seen_systems:
                    continue
                seen_systems.add(key)
                pid = planet.id.replace("planet-", "")
                url = (f"{self.cfg.server_url.rstrip('/')}/game/"
                       f"index.php?page=ingame&component=galaxy"
                       f"&galaxy={c.galaxy}&system={sys}&cp={pid}")
                self.log.info("Reciclaje: escaneando sistema [%d:%d]...", c.galaxy, sys)
                try:
                    self.page.goto(url, wait_until="domcontentloaded", timeout=10000)
                    self._delay()
                    try:
                        self.page.wait_for_selector(
                            "#galaxytable tr.row, #galaxytable tr.galaxyRow, .galaxyRow, #galaxytable tr", 
                            timeout=5000
                        )
                    except Exception:
                        pass
                    
                    js = f"""() => {{
                        const results = [];
                        
                        const getRawRes = (el, type) => {{
                            let val = el.getAttribute('data-raw-' + type) || el.getAttribute('data-' + type);
                            if (!val) {{
                                const child = el.querySelector('[data-raw-' + type + '], [data-' + type + '], [class*="' + type + '"]');
                                if (child) {{
                                    val = child.getAttribute('data-raw-' + type) || child.getAttribute('data-' + type) || child.getAttribute('data-raw');
                                    if (!val) val = child.textContent;
                                }}
                            }}
                            if (!val) {{
                                const regex = new RegExp('(?:' + (type === 'metal' ? 'metal' : 'cristal|crystal') + ')\\\\s*:?\\\\s*([\\\\d\\\\.\\\\s,]+k?M?)', 'i');
                                const m = el.textContent.match(regex);
                                if (m) val = m[1];
                            }}
                            if (!val) return 0;
                            let s = String(val).trim().toLowerCase();
                            let mult = 1;
                            if (s.endsWith('k')) {{ mult = 1000; s = s.slice(0, -1); }}
                            else if (s.endsWith('m')) {{ mult = 1000000; s = s.slice(0, -1); }}
                            const num = parseInt(s.replace(/[^0-9]/g, '')) || 0;
                            return num * mult;
                        }};

                        document.querySelectorAll(
                            '#galaxytable tr.row, #galaxytable tr, .galaxyRow'
                        ).forEach(row => {{
                            const debrisEl = row.querySelector(
                                '.debris, .expeditionDebris, [class*="debris"]'
                            );
                            if (!debrisEl) return;
                            const posEl = row.querySelector('.cellPosition, td.position, td:first-child');
                            const pos = posEl ? parseInt(posEl.textContent.trim()) : 0;
                            if (!pos) return;
                            
                            const metal = getRawRes(debrisEl, 'metal');
                            const crystal = getRawRes(debrisEl, 'crystal');
                            
                            if (metal + crystal > 0)
                                results.push({{pos, metal, crystal}});
                        }});
                        return results;
                    }}"""
                    rows = self.page.evaluate(js) or []
                    for row in rows:
                        tot = row.get("metal", 0) + row.get("crystal", 0)
                        if tot >= min_debris:
                            self.log.info("Campo de escombros detectado en %d:%d:%d con %d metal y %d cristal (Total: %d >= min: %d)",
                                          c.galaxy, sys, row['pos'], row['metal'], row['crystal'], tot, min_debris)
                            debris_list.append({
                                "coords": f"{c.galaxy}:{sys}:{row['pos']}",
                                "debris": {
                                    "metal": row["metal"], "crystal": row["crystal"], "deut": 0
                                },
                            })
                        elif tot > 0:
                            self.log.info("Campo de escombros en %d:%d:%d omitido por tamaño insuficiente (%d < min: %d)",
                                          c.galaxy, sys, row['pos'], tot, min_debris)
                except Exception as e:
                    self.log.debug("Error buscando escombros en %d:%d: %s",
                                   c.galaxy, sys, e)
        if debris_list:
            self.log.info("Escombros encontrados: %d campos.", len(debris_list))
        return debris_list

    def recall_fleet(self, origin: str, destination: str, mission: str = "deploy",
                     arrival: int = 0) -> bool:
        desc = f"Retornar flota de {origin} -> {destination} ({mission})"
        if self._act(desc):
            return True

        # Marcador de versión: si NO ves "recall v3" en el log al pedir un regreso, el contenedor
        # corre una imagen vieja -> reconstruye con `docker compose up -d --build`.
        self.log.info("recall v3 (coords por match): buscando %s -> %s", origin, destination)
        self._goto("movement")
        try:
            self.page.wait_for_selector(
                ".eventFleet, .fleetDetails, .fleet_row, #eventContent",
                timeout=5000,
            )
        except Exception:
            pass

        # Empareja por origen+destino+misión y, si hay varias flotas iguales, por la hora de
        # llegada (data-arrival-time) más cercana a la pedida, para recuperar la correcta.
        js_recall = """(origin, destination, mission, arrival) => {
            const norm = m => { m = String(m || '').toLowerCase();
                                return (m === 'deploy' || m === '4') ? '4' : m; };
            const want = norm(mission);
            const candidates = [];
            const rows = document.querySelectorAll(
                '.eventFleet, .fleetDetails, .fleet_row, tr.flightEventRow'
            );
            for (const row of rows) {
                try {
                    const origEl = row.querySelector(
                        '.originCoords a, .originCoords .coords, .coordsOrigin a, .coordsOrigin, ' +
                        '.originFleet a, [class*="origin"] a, [class*="orig"] a'
                    );
                    const rowOrigin = origEl ? (origEl.textContent.match(/\\d+:\\d+:\\d+/) || [''])[0] : '';
                    if (rowOrigin !== origin) continue;

                    const destEl = row.querySelector(
                        '.destinationCoords a, .destinationCoords .coords, .destCoords a, .destCoords .coords, ' +
                        '.coordsDest a, .coordsDest, .destFleet a, [class*="destination"] a, [class*="dest"] a'
                    );
                    const rowDest = destEl ? (destEl.textContent.match(/\\d+:\\d+:\\d+/) || [''])[0] : '';
                    if (rowDest !== destination) continue;

                    const rrf = row.getAttribute('data-return-flight');
                    // OJO: NO usar querySelector('.return_flight,...') aquí. En muchos servidores
                    // las filas RECUPERABLES (de ida) muestran la "hora de vuelta si la regresas"
                    // en un elemento con esa clase, lo que marcaba toda flota de ida como retorno
                    // y la saltaba. Una flota que YA regresa no trae botón recallFleet, así que el
                    // propio botón (más abajo) ya la excluye.
                    const is_return = row.classList.contains('is_return') ||
                                   rrf === 'true' || rrf === '1';
                    if (is_return) continue;

                    let rowMission = row.getAttribute('data-mission-type') || '';
                    if (!rowMission) {
                        const mEl = row.querySelector('[data-mission], [data-mission-type], .missionIcon, .icon_movement');
                        if (mEl) {
                            rowMission = mEl.getAttribute('data-mission') ||
                                         mEl.getAttribute('data-mission-type') ||
                                         (mEl.className.match(/mission(\\d+)/) || [])[1] || '';
                        }
                    }
                    // Filtra SIEMPRE por misión cuando se indica (no solo deploy), para no
                    // recuperar otra flota distinta en la misma ruta.
                    if (want && norm(rowMission) && norm(rowMission) !== want) continue;

                    const recallBtn = row.querySelector(
                        'a.recallFleet, a[class*="recall"], a[onclick*="sendRecall"], ' +
                        'a.reversal, .reversal_flight a, a.reversal_flight, a[class*="reversal"]'
                    );
                    if (!recallBtn) continue;

                    let rowArr = parseInt(row.getAttribute('data-arrival-time') || '0') || 0;
                    if (!rowArr) {
                        const ae = row.querySelector('[data-arrival-time]');
                        if (ae) rowArr = parseInt(ae.getAttribute('data-arrival-time') || '0') || 0;
                    }
                    candidates.push({ btn: recallBtn, arr: rowArr });
                } catch(e) {}
            }
            if (!candidates.length) return false;
            let chosen = candidates[0];
            if (arrival) {
                let bestDiff = 1e15;
                for (const c of candidates) {
                    if (c.arr) {
                        const d = Math.abs(c.arr - arrival);
                        if (d < bestDiff) { bestDiff = d; chosen = c; }
                    }
                }
                // si ninguna trae hora, nos quedamos con la primera
            }
            chosen.btn.click();
            return true;
        }"""
        try:
            result = self.page.evaluate(js_recall, (origin, destination, mission, int(arrival or 0)))
            if result:
                self.log.info("Recall ejecutado en UI para flota %s -> %s", origin, destination)
                try:
                    self.page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    time.sleep(2)
                return True
        except Exception as e:
            self.log.error("Excepción al intentar hacer recall de flota: %s", e)
        # No casó ninguna fila: volcar lo que ve el DOM (origen/destino/misión/retorno y las
        # clases de los enlaces) para ajustar los selectores. Si la lista sale vacía, es que no
        # se encontraron filas de movimiento (selector de fila incorrecto en este servidor).
        try:
            import json as _json
            diag = self.page.evaluate(_JS_RECALL_DIAG)
            # Solo las filas relevantes (mismo origen o destino), ENTERAS y sin truncar, para
            # ver exactamente por qué no casa la fila buscada (misión / is_return / botón).
            rel = [r for r in diag if r.get("o") == origin or r.get("d") == destination]
            self.log.warning("Recall DIAG (%s -> %s mision=%s): %d filas; coincidentes=%s",
                             origin, destination, mission, len(diag),
                             _json.dumps(rel, ensure_ascii=False))
        except Exception as e:
            self.log.debug("Recall DIAG falló: %s", e)
        return False

    def _parse_duration_to_seconds(self, time_str: str) -> Optional[int]:
        if not time_str:
            return None
        time_str = time_str.strip().lower()
        import re
        if ":" in time_str:
            time_str = re.sub(r'\s*[hms]$', '', time_str)
        m_hms = re.match(r'^(\d+):(\d+):(\d+)$', time_str)
        if m_hms:
            h, m, s = map(int, m_hms.groups())
            return h * 3600 + m * 60 + s
        
        parts = re.findall(r'(\d+)\s*([hms])', time_str)
        if parts:
            total_seconds = 0
            for val, unit in parts:
                if unit == 'h':
                    total_seconds += int(val) * 3600
                elif unit == 'm':
                    total_seconds += int(val) * 60
                elif unit == 's':
                    total_seconds += int(val)
            return total_seconds
        
        m_s = re.match(r'^(\d+)\s*s?$', time_str)
        if m_s:
            return int(m_s.group(1))
        return None
