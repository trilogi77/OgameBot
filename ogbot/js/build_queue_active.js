// True si hay algo en la cola de construcción de suministros/instalaciones.
// NOTA: data-status="on" significa "disponible para construir", NO "construyendo".
// Indicadores reales de cola activa: timer en #build_list o elemento countdown.
() => {
    // Items con timer en la lista de cola de construcción
    if (document.querySelector('#build_list .timer, #build_list .build_list_timer, #build_list [data-remaining]')) return true;
    // Countdown de construcción activo
    if (document.querySelector('.build-it_countdown, .ctn .cnt span')) return true;
    // Lista de cola no vacía (nodos li/item directos)
    const bl = document.querySelector('#build_list');
    if (bl && bl.querySelectorAll(':scope > li, :scope > .item').length > 0) return true;
    return false;
}
