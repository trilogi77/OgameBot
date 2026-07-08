"""Inspección EN VIVO del diálogo de renombrar planeta. Ejecutar con cwd = cuenta:
    python /app/scratch/live_rename.py

CUIDADO: el mismo diálogo suele tener 'abandonar planeta' (destructivo). Este script
SOLO lee/vuelca el HTML; NO envía ningún rename ni abandon. Deja el dump en /out.
"""
import json
import logging
import os
import sys

OUT = "/out" if os.path.isdir("/out") else "/tmp"
sys.path.insert(0, "/app")
from ogbot.config import Config
from ogbot.client import GameClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rename")

cfg = Config.load("config.yaml")
client = GameClient(cfg, log)
client.start()
if not client.login():
    log.error("Login fallido"); sys.exit(1)

planets = client.read_planets()
log.info("Planetas: %s", [(p.name, str(p.coords), p.id) for p in planets])
p0 = planets[0]
client._goto("overview", p0)
page = client.page

dump = {"planet": {"name": p0.name, "id": p0.id, "coords": str(p0.coords)}}

# 1) Enlaces "reubicar/abandonar/renombrar" y cualquier form de rename en el overview
dump["links"] = page.evaluate("""() => {
  const out = [];
  for (const a of document.querySelectorAll('a, span, button')) {
    const t = (a.textContent || '').trim().toLowerCase();
    if (/renombr|abandon|reubic|rename|relocat/.test(t)) {
      out.push({ tag: a.tagName, text: (a.textContent||'').trim().slice(0,40),
                 id: a.id, cls: (a.className||'').toString(), href: a.getAttribute('href') || '' });
    }
  }
  return out;
}""")
log.info("Enlaces renombrar/abandonar: %s", dump["links"])

# 2) ¿Hay ya un input/form de rename visible sin abrir nada?
dump["inline_forms"] = page.evaluate("""() => {
  const out = [];
  for (const f of document.querySelectorAll('form')) {
    const html = f.outerHTML.slice(0, 1500);
    if (/rename|planet|abandon/i.test(html)) out.push(html);
  }
  for (const i of document.querySelectorAll('input[name], input[id]')) {
    const n = (i.name||'') + '|' + (i.id||'');
    if (/name|rename|planet/i.test(n)) out.push('INPUT ' + n + ' type=' + i.type);
  }
  return out;
}""")

# 3) Abrir el diálogo de abandonar/renombrar SIN enviar nada, y volcar su HTML
opened = page.evaluate("""() => {
  for (const a of document.querySelectorAll('a, span, button')) {
    const t = (a.textContent || '').trim().toLowerCase();
    if (/renombr|abandon|rename/.test(t)) { a.click(); return (a.textContent||'').trim(); }
  }
  return null;
}""")
log.info("Click en: %r", opened)
import time as _t
_t.sleep(2.0)
page.screenshot(path=f"{OUT}/rename_dialog.png")

dump["dialog_html"] = page.evaluate("""() => {
  // Buscar el contenedor de diálogo/overlay más probable
  const sels = ['#deletePlanet', '.overlayDiv', '#planetMoveContent', '.ui-dialog',
                '#box', '.rename', 'form[name=\"renamePlanet\"]', '#renameContent'];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (el && el.offsetParent !== null) return { sel: s, html: el.outerHTML.slice(0, 6000) };
  }
  // fallback: cualquier form con input de texto visible que mencione planet/rename
  for (const f of document.querySelectorAll('form')) {
    if (f.offsetParent !== null && /rename|planet|abandon/i.test(f.outerHTML))
      return { sel: 'form(fallback)', html: f.outerHTML.slice(0, 6000) };
  }
  return { sel: 'NO_DIALOG', html: document.body.innerHTML.slice(0, 3000) };
}""")
log.info("Diálogo (sel=%s)", dump["dialog_html"].get("sel"))

# 4) Todos los inputs de texto visibles ahora (para localizar el campo del nombre)
dump["visible_text_inputs"] = page.evaluate("""() => [...document.querySelectorAll('input')]
  .filter(i => i.offsetParent !== null && (i.type === 'text' || i.type === ''))
  .map(i => ({ name: i.name, id: i.id, value: i.value, placeholder: i.placeholder,
               cls: (i.className||'').toString(),
               formId: i.form ? (i.form.id || i.form.name || '') : '' }))""")
log.info("Inputs de texto visibles: %s", dump["visible_text_inputs"])

with open(f"{OUT}/rename_dump.json", "w", encoding="utf-8") as f:
    json.dump(dump, f, ensure_ascii=False, indent=1)
log.info("Dump en %s/rename_dump.json (NO se envió ningún rename ni abandon)", OUT)
client.stop()
print("OK")
