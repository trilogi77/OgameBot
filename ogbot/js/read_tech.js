// JS que lee {tech_id_str: level/cantidad} de cualquier página de componente.
// Defensas usan .amount en vez de .level; probamos ambos.
() => {
    const r = {};
    document.querySelectorAll('li[data-technology]').forEach(li => {
        const tech = li.getAttribute('data-technology');
        const el = li.querySelector('.level[data-value], .amount[data-value]');
        if (el) r[tech] = parseInt(el.getAttribute('data-value') || '0');
    });
    return r;
}
