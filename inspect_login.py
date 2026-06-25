"""Vuelca HTML completo de un li de supplies y el estado del DOM tras clicar en él."""
from playwright.sync_api import sync_playwright

SERVER = "https://s272-es.ogame.gameforge.com/"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(
        storage_state="ogame_session.json",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        viewport={"width": 1366, "height": 768},
    )
    page = ctx.new_page()
    page.goto("https://lobby.ogame.gameforge.com/en_GB/accounts", wait_until="networkidle")
    with ctx.expect_page() as npi:
        page.locator("button.btn-primary").filter(has_text="Play").first.click()
    gp = npi.value
    gp.wait_for_load_state("networkidle", timeout=30000)
    gp.wait_for_timeout(2000)

    cp = ""
    items = gp.locator("#planetList .smallplanet")
    if items.count() > 0:
        pid = items.first.get_attribute("id") or ""
        if "planet-" in pid:
            cp = "&cp=" + pid.replace("planet-", "")

    gp.goto(f"{SERVER}game/index.php?page=ingame&component=supplies{cp}", wait_until="networkidle")
    gp.wait_for_selector("li[data-technology]", timeout=8000)
    gp.wait_for_timeout(1000)

    # HTML del li metal mine antes de clicar
    html = gp.evaluate(
        "() => document.querySelector('li[data-technology=\"1\"]')?.outerHTML?.substring(0,3000) || 'not found'"
    )
    print("=== LI tech=1 (metal mine) HTML ===")
    print(html)

    # Clicar en el li y esperar panel
    gp.locator("li[data-technology='1']").click()
    gp.wait_for_timeout(2000)
    gp.screenshot(path="ss_after_click.png")

    # Buscar cualquier panel/modal que aparecio
    panel = gp.evaluate("""() => {
        for (const sel of ['#technologydetails','#buildbuttons','.detail_button',
                           '#buttonz','.buildButton','#technologydetail',
                           '[id*=detail]','[class*=detail]','[class*=build]']) {
            const el = document.querySelector(sel);
            if (el && el.innerText.trim()) return sel + ': ' + el.outerHTML.substring(0,1500);
        }
        return 'no panel found';
    }""")
    print("\n=== PANEL TRAS CLICK ===")
    print(panel)

    # Todos los botones y links visibles
    btns = gp.evaluate("""() =>
        [...document.querySelectorAll('button,a[onclick],a.btn')].map(b => ({
            cls: b.className.substring(0,70),
            text: b.innerText.trim().substring(0,50),
            id: b.id,
            onclick: (b.getAttribute('onclick')||'').substring(0,80)
        })).filter(b => b.text || b.onclick)
    """)
    print("\n=== TODOS LOS BOTONES VISIBLES ===")
    for b in btns[:30]:
        print(f"  [{b['id']:15}] cls={b['cls'][:50]:50}  text={b['text']!r}")

    ctx.storage_state(path="ogame_session.json")
    browser.close()
    print("\nListo.")
