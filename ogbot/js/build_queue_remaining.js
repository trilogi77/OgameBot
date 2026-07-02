() => {
    const el = document.querySelector('#build_list [data-remaining], .construction [data-remaining], [data-remaining]');
    if (el) {
        const val = parseInt(el.getAttribute('data-remaining') || '0');
        if (val > 0) return val;
    }
    const timer = document.querySelector('#build_list .timer, #build_list .build_list_timer, .construction .timer, .countdown');
    if (timer) {
        const txt = timer.textContent.trim();
        const parts = txt.split(':');
        if (parts.length === 3) {
            return parseInt(parts[0]) * 3600 + parseInt(parts[1]) * 60 + parseInt(parts[2]);
        }
        if (parts.length === 2) {
            return parseInt(parts[0]) * 60 + parseInt(parts[1]);
        }
        let secs = 0;
        const hM = txt.match(/(\d+)h/);
        const mM = txt.match(/(\d+)m/);
        const sM = txt.match(/(\d+)s/);
        if (hM) secs += parseInt(hM[1]) * 3600;
        if (mM) secs += parseInt(mM[1]) * 60;
        if (sM) secs += parseInt(sM[1]);
        if (secs > 0) return secs;
    }
    return 0;
}
