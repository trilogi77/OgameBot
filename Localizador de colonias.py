import requests
import xml.etree.ElementTree as ET

def rastrear_imperio(coordenada_origen, servidor="s273-es.ogame.gameforge.com"):
    url_universe = f"https://{servidor}/api/universe.xml"
    
    print(f"\n[*] Descargando datos del universo desde {servidor}...")
    try:
        response = requests.get(url_universe, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[!] Error de red al intentar descargar los datos: {e}")
        return

    print("[*] Parseando el mapa estelar...")
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        print("[!] Error al procesar el XML de la API.")
        return
    
    # 1. Buscar el ID del jugador a partir de la coordenada
    player_id = None
    nombre_planeta_origen = None
    for planet in root.findall('.//planet'):
        if planet.get('coords') == coordenada_origen:
            player_id = planet.get('player')
            nombre_planeta_origen = planet.get('name')
            break
            
    if not player_id:
        print(f"[!] No se ha encontrado ningún planeta en las coordenadas {coordenada_origen}.")
        print("    Verifica el formato (ej. '1:123:4') o si el planeta ha sido abandonado.")
        return
        
    print(f"[+] Planeta localizado: '{nombre_planeta_origen}'")
    print(f"[*] ID Interno del jugador: {player_id}")
    print("[*] Rastreando el resto de colonias...\n")
    
    # 2. Filtrar todos los planetas que pertenezcan a ese ID
    planetas = []
    for planet in root.findall('.//planet'):
        if planet.get('player') == player_id:
            coords = planet.get('coords')
            name = planet.get('name')
            luna = "Sí" if planet.find('moon') is not None else "No"
            
            planetas.append({
                'coords': coords,
                'name': name,
                'moon': luna
            })
            
    # 3. Formatear y mostrar los resultados por consola
    print("-" * 50)
    print(f"INFORME DE IMPERIO (Jugador {player_id})")
    print("-" * 50)
    
    for p in planetas:
        marcador = " <-- (Coordenada introducida)" if p['coords'] == coordenada_origen else ""
        print(f"[{p['coords']}] {p['name']} | Luna: {p['moon']}{marcador}")
        
    print("-" * 50)
    print(f"Total de planetas detectados: {len(planetas)}\n")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        coordenada_busqueda = sys.argv[1]
        servidor = sys.argv[2] if len(sys.argv) > 2 else "s273-es.ogame.gameforge.com"
        rastrear_imperio(coordenada_busqueda, servidor)
    else:
        print("=== RADAR OGAME ===")
        # Pide la coordenada por terminal hasta que se introduzca un valor
        while True:
            coordenada_busqueda = input("Introduce la coordenada del planeta (ej. 3:125:8): ").strip()
            
            if coordenada_busqueda:
                break
            print("[!] Entrada vacía. Por favor, introduce una coordenada.\n")
            
        rastrear_imperio(coordenada_busqueda)