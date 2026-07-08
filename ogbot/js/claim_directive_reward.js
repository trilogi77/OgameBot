// Corre DENTRO del overlay de directivas ya abierto (component=ipioverview).
// Hace UNA acción y devuelve qué hizo; el Python llama en bucle porque el panel
// se re-renderiza por AJAX tras cada recogida / cambio de capítulo:
//   {action:'collect', id}  -> recogió (intentó recoger) la tarea `id`
//   {action:'chapter'}      -> cambió a otro capítulo con recompensas pendientes
//   null                    -> nada más que recoger
//
// Estados de tarea: data-state = 'completed' (lista) | 'collected' (hecha) | 'none'.
// El colector real es <a class="claimTaskRewards ipiOverviewCollectRewards" data-target=id>
// dentro de .ipiTaskItemContentCollect (lleva 'disabled' si ya se recogió); verificado
// contra el HTML vivo del panel (dump 2026-07-08).
() => {
  const task = document.querySelector('#ipiOverviewTasklist .ipiTaskItem[data-state="completed"]');
  if (task) {
    const id = task.getAttribute('data-taskid') || '';
    const btn = task.querySelector('a.claimTaskRewards:not(.disabled), .ipiOverviewCollectRewards:not(.disabled)');
    (btn || task).click();
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
