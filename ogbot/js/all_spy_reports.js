// Extrae todos los informes de espionaje de la página de mensajes.
() => {
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
                // Misiles del defensor (ABM/IPM). Van APARTE de 'defense' porque no
                // participan en el combate de flotas, pero cada interceptor se come un
                // IPM nuestro: sin este dato el ataque con misiles malgasta munición.
                missiles: {},
                // VISIBILIDAD del informe. OGame NO expone data-raw-fleet/defense en muchos
                // servidores: marca 'data-raw-hiddenships/hiddendef' = 1 cuando faltaron
                // sondas. Sin esto, fleet/defense salían {} y el bot daba el objetivo por
                // INDEFENSO y atacaba a ciegas. fleet_value/defense_value traen el valor
                // ('-' si oculto) aunque no haya desglose.
                fleet_visible: true,
                defense_visible: true,
                fleet_value: null,
                defense_value: null,
                counterespionage: 0,
                military_score: 0,
                loot_percent: null,
                player_name: el.getAttribute('data-raw-playername') || '',
                is_inactive: rawStatusStr.includes('inactive'),
                // Actividad del informe (minutos): 15 = '*' (<15 min), 15-59 = minutos.
                // Atributo ausente o no numérico -> null (sin dato, no bloquear).
                activity: (() => {
                    const a = parseInt(el.getAttribute('data-raw-activity'));
                    return isNaN(a) ? null : a;
                })()
            };

            // '-' = dato oculto (faltaron sondas). Devuelve null en ese caso.
            const numOrNull = (v) => {
                if (v === null || v === undefined) return null;
                const s = String(v).trim();
                if (s === '' || s === '-') return null;
                const n = parseInt(s.replace(/[^0-9-]/g, ''));
                return isNaN(n) ? null : n;
            };
            const hiddenShips = el.getAttribute('data-raw-hiddenships');
            const hiddenDef   = el.getAttribute('data-raw-hiddendef');
            // Si el servidor no expone los flags 'hidden*', caemos a comprobar si el
            // atributo con el desglose existe (algunos servidores sí lo traen).
            r.fleet_visible   = hiddenShips !== null ? hiddenShips !== '1'
                                                     : el.hasAttribute('data-raw-fleet');
            r.defense_visible = hiddenDef !== null ? hiddenDef !== '1'
                                                   : el.hasAttribute('data-raw-defense');
            r.fleet_value      = numOrNull(el.getAttribute('data-raw-fleetvalue'));
            r.defense_value    = numOrNull(el.getAttribute('data-raw-defensevalue'));
            r.counterespionage = numOrNull(el.getAttribute('data-raw-counterespionagechance')) || 0;
            r.military_score   = numOrNull(el.getAttribute('data-raw-highscoremilitary')) || 0;
            r.loot_percent     = numOrNull(el.getAttribute('data-raw-loot'));   // 75 => 75%

            const SHIP_MAP = {
                202:'small_cargo', 203:'large_cargo', 204:'light_fighter',
                205:'heavy_fighter', 206:'cruiser', 207:'battleship',
                208:'colony_ship', 209:'recycler', 210:'espionage_probe',
                211:'bomber', 212:'solar_satellite', 213:'destroyer',
                214:'deathstar', 215:'battlecruiser',
                // Naves modernas: sin ellas, un defensor con reapers/pathfinders parecia
                // INDEFENSO y el bot atacaba con cargueros desnudos -> perdida de flota.
                217:'crawler', 218:'reaper', 219:'pathfinder'
            };
            const DEF_MAP = {
                401:'rocket_launcher', 402:'light_laser', 403:'heavy_laser',
                404:'gauss_cannon', 405:'ion_cannon', 406:'plasma_turret',
                407:'small_shield_dome', 408:'large_shield_dome'
            };
            // OGame mete los misiles en el mismo payload data-raw-defense.
            const MISSILE_MAP = { 502:'interceptor_missile', 503:'interplanetary_missile' };
            
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
                        } else if (MISSILE_MAP[tid]) {
                            // Fuera de 'defense': no combaten, pero los interceptores
                            // se comen IPMs nuestros (y no deben marcar 'defendido').
                            r.missiles[MISSILE_MAP[tid]] = parseInt(count);
                        }
                    }
                }
            } catch(e) {}
            
            results.push(r);
        } catch(e) {}
    });
    return results;
}
