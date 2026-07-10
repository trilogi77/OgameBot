// Diagnóstico de la capa de ataque con misiles (missileattacklayer).
// Solo LEE: enumera inputs, botones y selects visibles para poder ajustar los selectores
// del lanzador sin clicar nada. Se usa cuando launch_missiles no encuentra el formulario.
() => {
    const vis = el => el && el.offsetParent !== null;
    const txt = el => (el.textContent || '').trim().slice(0, 40);
    return {
        url: location.href,
        title: (document.title || '').slice(0, 80),
        bodyStart: (document.body ? document.body.innerText : '').trim().slice(0, 300),
        inputs: [...document.querySelectorAll('input')].filter(vis).slice(0, 12).map(i => ({
            id: i.id, name: i.name, type: i.type, cls: (i.className || '').slice(0, 40),
        })),
        buttons: [...document.querySelectorAll('button, a.btn, input[type=submit], a')]
            .filter(vis).slice(0, 15).map(b => ({
                tag: b.tagName, id: b.id, cls: (b.className || '').slice(0, 40), text: txt(b),
            })),
        selects: [...document.querySelectorAll('select')].filter(vis).slice(0, 6).map(s => ({
            id: s.id, name: s.name, options: [...s.options].slice(0, 8).map(o => o.value + ':' + txt(o)),
        })),
        forms: [...document.querySelectorAll('form')].slice(0, 4).map(f => ({
            id: f.id, action: (f.action || '').slice(0, 80), method: f.method,
        })),
    };
}
