// True si la cola está activa según la página de overview.
() => {
    // td.idle = nada construyendo; su ausencia dentro de .construction.active = cola ocupada
    const idle = document.querySelector('.construction td.idle');
    if (idle !== null) return false;
    return !!document.querySelector('.construction.active');
}
