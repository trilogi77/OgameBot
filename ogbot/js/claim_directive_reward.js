// Reclama UNA recompensa de directiva/misión completada y devuelve su etiqueta,
// o null si no queda ninguna reclamable. El Python llama en bucle (el panel se
// re-renderiza por AJAX tras cada reclamo).
//
// ponytail: seleccionamos por texto exacto (ES/EN) porque no tenemos el DOM real
// de la página de directivas. Techo: matcheo frágil ante otros idiomas / cambios
// de copy. Upgrade path: fijar el selector exacto (data-attr o clase del botón)
// cuando veamos la página en vivo y sustituir CLAIM/DONE por ese selector.
() => {
  const CLAIM = [
    'recibir recompensa', 'recibir todas las recompensas', 'recoger recompensa',
    'reclamar recompensa', 'obtener recompensa',
    'receive reward', 'receive all rewards', 'collect reward', 'claim reward'
  ];
  const DONE = ['recogida', 'recibida', 'reclamada', 'collected', 'claimed', 'received'];
  const norm = s => (s || '').trim().toLowerCase().replace(/\s+/g, ' ');

  for (const el of document.querySelectorAll('button, a, span, div')) {
    const t = norm(el.textContent);
    if (!t) continue;
    if (DONE.some(d => t.includes(d))) continue;   // ya reclamada
    if (!CLAIM.some(c => t === c)) continue;        // no es un botón de reclamo

    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;  // invisible
    const cls = (el.className || '').toString().toLowerCase();
    if (el.hasAttribute('disabled') || cls.includes('disabled') || cls.includes('off')) continue;

    el.click();
    return t;
  }
  return null;
}
