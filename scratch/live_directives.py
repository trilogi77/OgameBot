"""Prueba EN VIVO del panel de directivas. Ejecutar con cwd = directorio de la cuenta
(usa su config.yaml y ogame_session.json):

    python /app/scratch/live_directives.py

1) Vuelca las 7 directivas (tareas, estados, progreso, recompensas) a
   /tmp/directives_dump.json ANTES de tocar nada.
2) Llama a GameClient.claim_directive_rewards() para recoger las pendientes.
3) Relee el badge y deja capturas en /tmp para verificación.
"""
import json
import logging
import os
import sys
import time

OUT = "/out" if os.path.isdir("/out") else "/tmp"   # montar -v host:/out para conservar el dump

sys.path.insert(0, "/app")
from ogbot.config import Config
from ogbot.client import GameClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("live")

cfg = Config.load("config.yaml")
client = GameClient(cfg, log)
client.start()
if not client.login():
    log.error("Login fallido"); sys.exit(1)

client._goto("overview")
page = client.page

badge = page.evaluate(
    "() => { const s = document.querySelector('#ipimenucomponent .ipiHintCollect');"
    " return s ? s.textContent.trim() : null; }")
log.info("Badge de directivas: %r", badge)
# Diagnóstico del badge: HTML real del menú y cualquier ipiHintCollect en la página
menu_html = page.evaluate(
    "() => { const m = document.querySelector('#ipimenucomponent');"
    " return m ? m.outerHTML.slice(0, 4000) : 'NO #ipimenucomponent'; }")
hints = page.evaluate(
    "() => [...document.querySelectorAll('.ipiHintCollect')].map(s =>"
    " ({ text: s.textContent.trim(), parent: s.parentElement ? s.parentElement.className : '' }))")
log.info("ipiHintCollect en la página: %s", hints)

# Abrir el overlay
opened = page.evaluate(
    "() => { const a = document.querySelector('#ipiInnerMenuContentHolder');"
    " if (!a) return false; a.click(); return true; }")
log.info("Overlay abierto: %s", opened)
if opened:
    page.wait_for_selector("#ipiOverviewTasklist .ipiTaskItem", timeout=10000)
    time.sleep(1.5)
page.screenshot(path=f"{OUT}/directives_open.png")

DUMP_CHAPTER = """() => ({
  chapterTitle: (document.querySelector('#ipiOverviewContent h1, #ipiOverviewContent h2, .ipiOverviewChapterName')?.textContent || '').trim(),
  tasks: [...document.querySelectorAll('#ipiOverviewTasklist .ipiTaskItem')].map(t => ({
    id: t.getAttribute('data-taskid'),
    state: t.getAttribute('data-state'),
    title: (t.querySelector('.ipiTaskItemTitle')?.textContent || '').trim(),
    progress: t.querySelector('.ipiTaskItemProgress')?.getAttribute('data-progress') ?? null,
    total: t.querySelector('.ipiTaskItemProgress')?.getAttribute('data-total') ?? null,
    trackText: (t.querySelector('.ipiTaskItemTrack')?.textContent || '').trim(),
    content: (t.querySelector('.ipiTaskItemContent')?.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 1500),
    contentHTML: (t.querySelector('.ipiTaskItemContent')?.innerHTML || '').slice(0, 5000)
  })),
  chapterRewards: (document.querySelector('#ipiOverviewChapterRewards')?.textContent || '').trim().replace(/\\s+/g, ' '),
  chapterRewardsHTML: (document.querySelector('#ipiOverviewChapterRewards')?.innerHTML || '').slice(0, 8000)
})"""

chapters_meta = page.evaluate(
    "() => [...document.querySelectorAll('#ipiOverviewChapters .ipiOverviewSelectChapter')]"
    ".map(a => ({ label: (a.textContent || '').trim().split('\\n')[0],"
    " pending: a.querySelector('.ipiHintCollect') ? a.querySelector('.ipiHintCollect').textContent.trim() : null }))")
log.info("Capítulos: %s", chapters_meta)

dump = {"badge": badge, "menuHTML": menu_html, "hints": hints, "chapters": []}
n_chapters = len(chapters_meta or [])
for i in range(n_chapters):
    page.evaluate(
        "(i) => document.querySelectorAll('#ipiOverviewChapters .ipiOverviewSelectChapter')[i].click()", i)
    time.sleep(2.0)  # re-render AJAX del capítulo
    try:
        page.wait_for_selector("#ipiOverviewTasklist .ipiTaskItem", timeout=8000)
    except Exception:
        pass
    ch = page.evaluate(DUMP_CHAPTER)
    ch["index"] = i
    ch["menuLabel"] = chapters_meta[i].get("label")
    ch["menuPending"] = chapters_meta[i].get("pending")
    dump["chapters"].append(ch)
    log.info("Capítulo %d (%s): %d tareas", i + 1, ch.get("chapterTitle") or "?",
             len(ch.get("tasks") or []))

with open(f"{OUT}/directives_dump.json", "w", encoding="utf-8") as f:
    json.dump(dump, f, ensure_ascii=False, indent=1)
log.info("Dump guardado en %s/directives_dump.json", OUT)

# --- Recoger las recompensas pendientes con el método real del bot ---
claimed = client.claim_directive_rewards()
log.info("RESULTADO claim_directive_rewards() = %d", claimed)
page.screenshot(path=f"{OUT}/directives_after.png")

client._goto("overview")
badge2 = page.evaluate(
    "() => { const s = document.querySelector('#ipimenucomponent .ipiHintCollect');"
    " return s ? s.textContent.trim() : null; }")
log.info("Badge tras el reclamo: %r (antes: %r)", badge2, badge)

client.stop()
print(json.dumps({"badge_before": badge, "badge_after": badge2, "claimed": claimed}))
