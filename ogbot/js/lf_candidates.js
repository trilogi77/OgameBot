// Candidatos de una página de Formas de vida (edificios o investigación). Para cada
// tecnología devuelve su nivel actual y si su botón de subir está ACTIVO (asequible y
// con requisitos cumplidos). Sirve para elegir con criterio (subir la rama de forma
// equilibrada) en vez de coger el primero de la lista, que era siempre el Sector
// Residencial y por eso el bot solo reventaba ese.
//
// Cuando ya hay algo en cola, OGame deja los botones deshabilitados: entonces no hay
// candidatos activos y el que llama entiende que no hay nada que iniciar.
() => {
    const out = [];
    for (const li of document.querySelectorAll('li[data-technology]')) {
        const tech = parseInt(li.getAttribute('data-technology'), 10);
        if (isNaN(tech)) continue;
        const lvlEl = li.querySelector('.level[data-value], .amount[data-value]');
        const level = lvlEl ? parseInt(lvlEl.getAttribute('data-value') || '0', 10) : 0;
        const btn = li.querySelector('button.upgrade:not([disabled]):not(.disabled)');
        out.push({ tech, level, enabled: !!btn });
    }
    return out;
}
