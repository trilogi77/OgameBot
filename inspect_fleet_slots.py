from ogbot.config import Config
from ogbot.client import GameClient

def check_slots():
    cfg = Config.load("config.yaml")
    cfg.dry_run = False
    cfg.headless = True
    
    from ogbot import utils
    logger = utils.setup_logger("check_slots", "test_fleet.log", "DEBUG")
    client = GameClient(cfg, logger)
    client.start()
    
    try:
        if not client.login():
            print("Login failed")
            return
        planets = client.read_planets()
        p = planets[0]
        
        client._goto("fleet", p)
        client.page.wait_for_timeout(3000)
        
        # Read fleet slots text from the page (usually somewhere at the top, like X/Y)
        slots_text = client.page.evaluate("""() => {
            // Buscamos indicadores de slots de flota, p.ej. #slots, .slots, o texto conteniendo "Flotas" o "Fleets"
            const selectors = ['#slots', '.slots', '#slotsValue', '.slotsValue', '#fleetSlots', '#countFleets', '#maxFleets'];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el) return sel + ': ' + el.innerText.trim();
            }
            // Buscar en todo el texto visible
            const text = document.body.innerText;
            const match = text.match(/Flotas:\\s*\\d+\\/\\d+/i) || text.match(/Fleets:\\s*\\d+\\/\\d+/i) || text.match(/Expediciones:\\s*\\d+\\/\\d+/i) || text.match(/Expeditions:\\s*\\d+\\/\\d+/i);
            return match ? match[0] : 'No match in text';
        }""")
        print(f"Slots info: {slots_text}")
        
        # Read movements to see what fleets are flying
        mvs = client.read_movements()
        print(f"Movimientos de flotas activos en total ({len(mvs)}):")
        for mv in mvs:
            print(f"  Misión {mv.get('mission')}: {mv.get('origin')} -> {mv.get('destination')} (Retorno={mv.get('is_return')})")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        client.stop()

if __name__ == "__main__":
    check_slots()
