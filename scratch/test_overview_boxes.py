"""Los 4 paneles de la vista general se leen ACOTADOS a su contenedor.

Regresion que cubre: con el panel de investigacion en "idle", el selector global
'.construction td.idle' daba por libre la cola de EDIFICIOS (falso negativo) y el bot
planificaba encima de una construccion en curso.

DOM calcado del real: la caja de forma de vida cuelga DENTRO de la columna de edificios.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from ogbot.client import GameClient, _load_js
from ogbot.models import Coords, Planet

BUILDING_ACTIVE = """
        <table class="construction active"><tbody>
          <tr class="data">
            <td class="first" rowspan="3"><a data-technology="14" href="#"></a></td>
            <td class="desc ausbau">Subiendo al <span class="level">Nivel 14</span></td>
          </tr>
          <tr class="data"><td class="desc">Duracion:</td>
              <td class="time"><span id="Countdown">1h 39m 41s</span></td></tr>
        </tbody></table>
"""

BUILDING_IDLE = """
        <table class="construction"><tbody><tr>
          <td class="idle">Ningun edificio en construccion</td>
        </tr></tbody></table>
"""


def overview(building_html):
    return """
<div id="productionboxBottom">
  <div class="productionBoxBuildings boxColumn building">
    <div id="productionboxbuildingcomponent" class="productionboxbuilding injectedComponent">
      <div class="content-box-s"><div class="header">Edificio</div><div class="content">
      %s
      </div></div>
    </div>
    <div id="productionboxlfbuildingcomponent" class="productionboxlfbuilding injectedComponent">
      <div class="content-box-s"><div class="content">
        <table class="construction"><tbody><tr><td class="idle">No hay formas de vida</td></tr></tbody></table>
      </div></div>
    </div>
  </div>
  <div class="productionBoxResearch boxColumn research">
    <div class="content-box-s"><div class="content">
      <table class="construction"><tbody><tr>
        <td class="idle">No hay ninguna investigacion en progreso en este momento.</td>
      </tr></tbody></table>
    </div></div>
  </div>
  <div class="productionBoxShips boxColumn ship">
    <div class="content-box-s"><div class="content">
      <table class="construction active"><tbody>
        <tr class="data"><td class="first"><a data-technology="210" href="#"></a></td>
            <td class="desc">Sonda de espionaje</td></tr>
        <tr class="data"><td class="desc">Tiempo total:</td>
            <td><span id="shipyardCountdown" data-remaining="5351">1h 29m 11s</span></td></tr>
      </tbody></table>
    </div></div>
  </div>
</div>
""" % building_html


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page()

    def boxes_for(html):
        page.set_content(html)
        return page.evaluate(_load_js("overview_boxes"))

    # --- Caso 1: edificio EN CURSO, investigacion libre (el falso negativo) -------------
    b = boxes_for(overview(BUILDING_ACTIVE))

    assert b["building"]["active"] is True, f"falso negativo: cola de edificios libre -> {b['building']}"
    assert b["building"]["remaining"] == 1 * 3600 + 39 * 60 + 41, b["building"]["remaining"]
    assert b["building"]["techs"] == [14], b["building"]["techs"]

    # El idle de investigacion / forma de vida NO contamina al panel de edificios.
    assert b["research"]["active"] is False, b["research"]
    assert b["lifeform"]["active"] is False, b["lifeform"]

    # Hangar: lee data-remaining, no el texto.
    assert b["ships"]["active"] is True and b["ships"]["remaining"] == 5351, b["ships"]

    # --- Caso 2: todo libre -> nada bloquea --------------------------------------------
    idle = boxes_for(overview(BUILDING_IDLE))
    assert idle["building"]["active"] is False, idle["building"]
    assert idle["building"]["remaining"] == 0 and idle["building"]["techs"] == []

    # --- Caso 3: panel ausente -> clave a null, no un False inventado -------------------
    gone = boxes_for("<div>overview sin paneles</div>")
    assert all(v is None for v in gone.values()), gone

    # --- Caso 4: volcado sobre el planeta (apply_overview_boxes) ------------------------
    def as_client(page_obj):
        """GameClient minimo (sin navegador propio) para llamar a los metodos sueltos."""
        c = SimpleNamespace(page=page_obj, log=SimpleNamespace(debug=lambda *a, **k: None))
        c.read_overview_boxes = lambda: GameClient.read_overview_boxes(c)
        return c

    page.set_content(overview(BUILDING_ACTIVE))
    p = Planet(id="1", name="Vacio Estelar", coords=Coords(3, 200, 8))
    GameClient.apply_overview_boxes(as_client(page), p)

    assert p.building_in_progress is True
    assert p.building_remaining_seconds == 5981
    assert p.building_queue == ["robotics_factory"], p.building_queue
    assert p.research_in_progress is False and p.research_remaining_seconds == 0
    assert p.lifeform_in_progress is False
    # lvl() suma lo encolado: el nivel en curso ya cuenta, para no repetir la orden.
    p.buildings["robotics_factory"] = 13
    assert p.lvl("robotics_factory") == 14

    # Lectura rota: fail-open a proposito (lo respalda build_finish_epoch en la cache),
    # pero no debe tumbar el ciclo ni declarar libre lo que no ha podido mirar.
    p2 = Planet(id="2", name="Avalon", coords=Coords(3, 200, 9))
    p2.lifeform_in_progress = True
    dead_page = SimpleNamespace(
        evaluate=lambda *a: (_ for _ in ()).throw(RuntimeError("page closed")))
    GameClient.apply_overview_boxes(as_client(dead_page), p2)
    assert p2.building_in_progress is False
    assert p2.lifeform_in_progress is True, "sin panel legible no se declara libre la forma de vida"

    browser.close()

print("OK: paneles acotados por contenedor; el idle de investigacion ya no libera la cola de edificios")
