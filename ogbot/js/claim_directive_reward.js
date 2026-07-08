// Corre DENTRO del overlay de directivas ya abierto (component=ipioverview).
// Hace UNA acción y devuelve qué hizo; el Python llama en bucle porque el panel
// se re-renderiza por AJAX tras cada recogida / cambio de capítulo:
//   {action:'collect', id}  -> recogió (intentó recoger) la tarea `id`
//   {action:'chapter'}      -> cambió a otro capítulo con recompensas pendientes
//   null                    -> nada más que recoger
//
// Estados de tarea: data-state = 'completed' (lista) | 'collected' (hecha) | 'none'.
// El colector es .ipiTaskItemTrack[data-target=taskid]; si faltara, la propia tarea.
() => {
  const task = document.querySelector('#ipiOverviewTasklist .ipiTaskItem[data-state="completed"]');
  if (task) {
    const id = task.getAttribute('data-taskid') || '';
    (task.querySelector('.ipiTaskItemTrack') || task).click();
    return { action: 'collect', id: id };
  }
  for (const a of document.querySelectorAll('#ipiOverviewChapters .ipiOverviewSelectChapter')) {
    const li = a.closest('.ipiChapterItem') || a;
    const active = (li.className || '').toString().indexOf('active') !== -1;
    if (!active && li.querySelector('.ipiHintCollect')) {
      a.click();
      return { action: 'chapter', id: '' };
    }
  }
  return null;
}
