// Lee movimientos de flota activos desde la página de movimientos.
() => {
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
                                 (mEl.className.match(/mission(\d+)/) || [])[1] || '';
                }
            }
            mv.mission = missionVal || '';

            // 2. Origin Coordinates
            const origEl = row.querySelector(
                '.originCoords a, .originCoords .coords, .coordsOrigin a, .coordsOrigin, ' +
                '.originFleet a, [class*="origin"] a, [class*="orig"] a'
            );
            mv.origin = origEl ? origEl.textContent.replace(/[\[\]\s]/g, '') : '';

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
            mv.destination = destEl ? destEl.textContent.replace(/[\[\]\s]/g, '') : '';

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
                const dm = revTxt.match(/(\d{1,2})[.\-\/](\d{1,2})[.\-\/](\d{2,4})\D+(\d{1,2}):(\d{2}):(\d{2})/);
                const arrCell = row.querySelector('.arrivalTime');
                const am = arrCell ? (arrCell.textContent || '').match(/(\d{1,2}):(\d{2}):(\d{2})/) : null;
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
                    if (/^[\d.,\s]+$/.test(a)) { valStr = a; name = b; }
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
}
