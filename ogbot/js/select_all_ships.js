() => {
    // Selecciona todas las naves del planeta escribiendo la cantidad máxima (.amount)
    // en cada input, con setter nativo para compatibilidad React/SPA.
    // Equivalente JS de utils.parse_localized_number: números del DOM de OGame en
    // cualquier locale ('1.234.567', '1,5M', '12k', '3,5', '1 234'...).
    const parseLocalizedNumber = (text) => {
        if (text === null || text === undefined) return 0;
        let s = String(text).trim();
        if (s === '' || s === '-') return 0;
        // espacios normales, nbsp, finos y de cifra usados como separador de miles
        s = s.replace(/[\s    ]/g, '');
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

    let clicked = false;
    document.querySelectorAll('li[data-technology]').forEach(li => {
        const inp = li.querySelector('input');
        if (inp) {
            const amountEl = li.querySelector('.amount');
            // Antes solo se quitaban los puntos: en locales con coma se escribía
            // '1,234' en el input. Normalizamos el número completo por locale.
            const maxVal = amountEl ? Math.floor(parseLocalizedNumber(amountEl.textContent)) : 0;
            if (maxVal > 0) {
                const nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                nativeSetter.call(inp, String(maxVal));
                inp.dispatchEvent(new Event('input', {bubbles:true}));
                inp.dispatchEvent(new Event('change', {bubbles:true}));
                clicked = true;
            }
        }
    });
    return clicked;
}
