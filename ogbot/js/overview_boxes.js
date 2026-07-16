// Estado de los paneles de produccion de la vista general en UNA sola lectura:
// edificio, forma de vida, investigacion y hangar.
//
// Cada panel se mira SOLO dentro de su contenedor. Los selectores globales de antes
// ('.construction td.idle') encontraban el "idle" del panel de investigacion y daban
// por libre la cola de EDIFICIOS: falso negativo -> el bot planificaba encima.
//
// Devuelve {building, lifeform, research, ships}; cada uno {active, remaining, techs}
// o null si ese panel no existe en el DOM.
() => {
    const BOXES = {
        // ponytail: el panel de forma de vida cuelga de la misma columna que el de
        // edificios, asi que ambos se acotan por su componente propio, no por .boxColumn.
        building: ['#productionboxbuildingcomponent', '.productionboxbuilding'],
        lifeform: ['#productionboxlfbuildingcomponent', '.productionboxlfbuilding'],
        research: ['.productionBoxResearch', '.boxColumn.research'],
        ships:    ['.productionBoxShips', '.boxColumn.ship'],
    };

    const seconds = (txt) => {
        txt = (txt || '').trim();
        const p = txt.split(':').map(n => parseInt(n, 10));
        if (p.length === 3 && p.every(n => !isNaN(n))) return p[0] * 3600 + p[1] * 60 + p[2];
        if (p.length === 2 && p.every(n => !isNaN(n))) return p[0] * 60 + p[1];
        const grab = (re) => { const m = txt.match(re); return m ? parseInt(m[1], 10) : 0; };
        return grab(/(\d+)\s*h/) * 3600 + grab(/(\d+)\s*m/) * 60 + grab(/(\d+)\s*s/);
    };

    const read = (selectors) => {
        let box = null;
        for (const sel of selectors) {
            box = document.querySelector(sel);
            if (box) break;
        }
        if (!box) return null;

        if (box.querySelector('td.idle, .idle')) return { active: false, remaining: 0, techs: [] };

        let remaining = 0;
        for (const el of box.querySelectorAll('[data-remaining]')) {
            const v = parseInt(el.getAttribute('data-remaining') || '0', 10);
            if (v > remaining) remaining = v;
        }
        if (!remaining) {
            for (const el of box.querySelectorAll('.timer, .countdown, span[id$="ountdown"]')) {
                const v = seconds(el.textContent);
                if (v > remaining) remaining = v;
            }
        }
        const active = !!box.querySelector('.construction.active') || remaining > 0;
        const techs = [...box.querySelectorAll('[data-technology]')]
            .map(el => parseInt(el.getAttribute('data-technology'), 10))
            .filter(n => !isNaN(n));
        return { active, remaining, techs };
    };

    const out = {};
    for (const key of Object.keys(BOXES)) out[key] = read(BOXES[key]);
    return out;
}
