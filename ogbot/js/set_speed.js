// Establece el slider o selector de velocidad de flota (compatible con OGame Redesign y React).
(val) => {
    let stepNum = parseInt(val);
    if (stepNum > 10 && stepNum <= 100) {
        stepNum = stepNum / 10;
    }
    
    // 1. Intentar por data-step en el contenedor .steps (específico para OGame Redesign)
    if (stepNum >= 1 && stepNum <= 10) {
        const stepEl = document.querySelector(`.steps .step[data-step="${stepNum}"], .steps [data-step="${stepNum}"], [data-step="${stepNum}"].step`);
        if (stepEl) {
            stepEl.click();
            return 'clicked_data_step:' + stepNum;
        }
    }
    
    // 2. Buscar por texto dentro del contenedor .steps
    const stepsContainer = document.querySelector('.steps');
    if (stepsContainer && stepNum >= 1 && stepNum <= 10) {
        const targetPct = String(stepNum * 10);
        for (const el of stepsContainer.querySelectorAll('.step, div')) {
            const txt = el.textContent.trim();
            if (txt === targetPct || txt === targetPct + '%') {
                el.click();
                return 'clicked_steps_by_text:' + txt;
            }
        }
    }

    const pct = val * 10;
    
    // 3. Fallback de selectores tradicionales
    const selectors = [
        `ul.speed li.step${pct}`,
        `ul.speed li[data-value="${pct}"]`,
        `.speedLinks a.step${pct}`,
        `.speedLinks .step${pct}`,
        `#speedLinks a.step${pct}`,
        `#speedLinks .step${pct}`,
        `a[onclick*="selectSpeed(${pct})"]`,
        `a[onclick*="selectSpeed(${val})"]`,
        `#speedLinks a`,
        `.speedLinks a`
    ];
    
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) {
            el.click();
            return 'clicked_selector:' + sel;
        }
    }
    
    // Buscar por texto en elementos de velocidad específicos (excluyendo .step genérico que choca con paginadores)
    const elements = document.querySelectorAll('.speedLinks a, #speedLinks a, a.speedLink, .speed_percent, ul.speed li');
    for (const el of elements) {
        const txt = el.textContent.trim();
        if (txt === pct + '%' || txt === String(pct)) {
            el.click();
            return 'clicked_by_text:' + txt;
        }
    }
    
    // Buscar por el atributo onclick
    for (const el of document.querySelectorAll('a, span, div, li')) {
        const oc = el.getAttribute('onclick');
        if (oc && (oc.includes(`selectSpeed(${pct})`) || oc.includes(`selectSpeed(${val})`))) {
            el.click();
            return 'clicked_by_onclick';
        }
    }

    // 4. Fallback: slider de rango estándar (input range)
    const slider = document.querySelector('#speedPercent, input[name="speed"], input[type="range"]');
    if (slider) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(slider, String(val));
        slider.dispatchEvent(new Event('input', {bubbles: true}));
        slider.dispatchEvent(new Event('change', {bubbles: true}));
        return 'slider_set';
    }
    
    return false;
}
