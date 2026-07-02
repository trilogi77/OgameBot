// Diagnóstico para el regreso de flota: por cada fila de movimiento devuelve lo que ven los
// selectores del matcher (origen/destino/misión/retorno) y las clases de los enlaces, para
// poder ajustar los selectores del botón de regreso cuando no se encuentra.
() => {
    const norm = s => String(s || '').replace(/[\[\]\s]/g, '');
    const rows = document.querySelectorAll('.eventFleet, .fleetDetails, .fleet_row, tr.flightEventRow');
    const out = [];
    rows.forEach(row => {
        const oEl = row.querySelector('.originCoords a, .originCoords .coords, .coordsOrigin a, .coordsOrigin, .originFleet a, [class*="origin"] a, [class*="orig"] a');
        const dEl = row.querySelector('.destinationCoords a, .destinationCoords .coords, .destCoords a, .destCoords .coords, .coordsDest a, .coordsDest, .destFleet a, [class*="destination"] a, [class*="dest"] a');
        const recall = row.querySelector('a.recallFleet, a[class*="recall"], a[onclick*="sendRecall"], a.reversal, a.reversal_flight, a[class*="reversal"], a[href*="return="]');
        // Contenedor de regreso (span/div) aunque el <a> interno tenga clase vacía: sin esto el
        // diagnóstico no mostraba la estructura real (p.ej. <span class="reversal reversal_time">).
        const revBox = row.querySelector('[class*="reversal"], .return_flight, .returnflight');
        const revA = revBox ? revBox.querySelector('a') : null;
        const anchors = [];
        row.querySelectorAll('a').forEach(a => {
            const c = a.className || '';
            const oc = a.getAttribute('onclick') || '';
            const hr = a.getAttribute('href') || '';
            anchors.push((c || oc || hr).toString().slice(0, 60));
        });
        out.push({
            o: oEl ? norm(oEl.textContent) : null,
            d: dEl ? norm(dEl.textContent) : null,
            m: row.getAttribute('data-mission-type') || '',
            ret_cls: row.classList.contains('is_return'),
            rrf: row.getAttribute('data-return-flight'),
            ret_sel: !!row.querySelector('.return_flight, .returnflight'),
            hasRecall: !!recall,
            recallCls: recall ? (recall.className || recall.getAttribute('onclick') || recall.getAttribute('href') || '') : null,
            revBox: revBox ? (revBox.tagName + '.' + (revBox.className || '').slice(0, 40)) : null,
            revA: revA ? ((revA.className || '') + ' href=' + (revA.getAttribute('href') || '').slice(0, 70)) : null,
            anchors: anchors.slice(0, 12),
            rowcls: (row.className || '').slice(0, 80)
        });
    });
    return out;
}
