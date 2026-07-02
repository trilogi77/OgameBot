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
}
