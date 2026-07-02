() => {
    // Lee los campos de escombros visibles en la página de galaxia:
    // [{pos, metal, crystal}, ...].
    // Equivalente JS de utils.parse_localized_number: números del DOM de OGame en
    // cualquier locale ('1.234.567', '1,5M', '12k', '3,5', '1 234'...).
    const parseLocalizedNumber = (text) => {
        if (text === null || text === undefined) return 0;
        let s = String(text).trim();
        if (s === '' || s === '-') return 0;
        // \s ya cubre nbsp y espacios finos/de cifra usados como separador de miles
        s = s.replace(/\s/g, '');
        if (!s) return 0;
        const neg = s.startsWith('-');
        if (s[0] === '+' || s[0] === '-') s = s.slice(1);
        let mult = 1;
        const low = s.toLowerCase();
        // multi-letra antes que 'm'/'g'/'b' sueltas para no recortar de menos
        for (const [suf, m] of [['mio', 1e6], ['mrd', 1e9], ['k', 1e3], ['m', 1e6], ['g', 1e9], ['b', 1e9]]) {
            if (low.endsWith(suf)) { s = s.slice(0, -suf.length); mult = m; break; }
        }
        if (mult !== 1) {
            // con sufijo el separador es decimal ('1,5M' -> 1.5 millones)
            s = s.replace(/,/g, '.');
            const last = s.lastIndexOf('.');
            if (last >= 0 && s.indexOf('.') !== last)
                s = s.slice(0, last).replace(/\./g, '') + '.' + s.slice(last + 1);
        } else {
            const hasDot = s.includes('.'), hasComma = s.includes(',');
            if (hasDot && hasComma) {
                // el ÚLTIMO separador es el decimal, el otro es de miles
                const dec = s.lastIndexOf('.') > s.lastIndexOf(',') ? '.' : ',';
                const thou = dec === '.' ? ',' : '.';
                s = s.split(thou).join('');
                if (dec === ',') s = s.replace(',', '.');
            } else if (hasDot || hasComma) {
                const sep = hasDot ? '.' : ',';
                const parts = s.split(sep);
                const looksThousands = parts.slice(1).every(p => p.length === 3) &&
                    parts[0].length >= 1 && parts[0].length <= 3 && parts[0] !== '0';
                if (looksThousands) s = parts.join('');
                else if (parts.length > 2) s = parts.slice(0, -1).join('') + '.' + parts[parts.length - 1];
                else s = parts.join('.');
            }
        }
        const val = parseFloat(s);
        if (isNaN(val)) return 0;
        return (neg ? -val : val) * mult;
    };

    const results = [];

    const getRawRes = (el, type) => {
        let val = el.getAttribute('data-raw-' + type) || el.getAttribute('data-' + type);
        if (!val) {
            const child = el.querySelector('[data-raw-' + type + '], [data-' + type + '], [class*="' + type + '"]');
            if (child) {
                val = child.getAttribute('data-raw-' + type) || child.getAttribute('data-' + type) || child.getAttribute('data-raw');
                if (!val) val = child.textContent;
            }
        }
        if (!val) {
            const regex = new RegExp('(?:' + (type === 'metal' ? 'metal' : 'cristal|crystal') + ')\\s*:?\\s*([\\d\\.\\s,]+k?M?)', 'i');
            const m = el.textContent.match(regex);
            if (m) val = m[1];
        }
        if (!val) return 0;
        // Normalizar el número COMPLETO por locale primero y aplicar el multiplicador
        // k/M después (lo hace parseLocalizedNumber): antes se quitaba el sufijo y
        // LUEGO se borraba el separador decimal, inflando hasta x100 ('2.5k' -> 25000).
        return Math.round(parseLocalizedNumber(val));
    };

    document.querySelectorAll(
        '#galaxytable tr.row, #galaxytable tr, .galaxyRow'
    ).forEach(row => {
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
            results.push({pos, metal, crystal});
    });
    return results;
}
