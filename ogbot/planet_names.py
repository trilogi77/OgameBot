"""
planet_names.py
===============
200 nombres "guays" para renombrar planetas y colonias. El brain elige uno al azar
que no se haya usado ya en la cuenta (ver renamed_planets/used_planet_names en state.json).

El juego valida el nombre con /^[a-zA-Z0-9\\-_\\s]+$/ y 2-20 caracteres (sin acentos ni
ñ), así que normalizamos a ASCII al cargar el módulo: la lista bonita de abajo se
convierte en nombres que el juego SÍ acepta (verificado contra el form planetRename).

ponytail: normalización en carga (unicodedata stdlib); sin fichero externo.
"""
import unicodedata


def _ascii(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return " ".join(s.split())[:20].strip()  # colapsa espacios, máx 20, sin bordes


_RAW = [
    "Nova Prime", "Asgard", "Valhalla", "Elysium", "Avalon", "Erebus", "Nyx", "Helios",
    "Nemesis", "Prometeo", "Titania", "Oberón", "Caronte", "Cerbero", "Tártaro", "Aqueronte",
    "Estigia", "Cocito", "Flegetonte", "Leteo", "Aurora Boreal", "Ío", "Europa", "Calisto",
    "Ganímedes", "Tritón", "Nereida", "Proteo", "Dione", "Rea", "Tetis", "Encélado",
    "Mimas", "Jápeto", "Febe", "Hiperión", "Pandora", "Prometheus", "Atlas", "Pan",
    "Deimos", "Fobos", "Ares", "Cronos", "Hiperborea", "Tule", "Aztlán", "Cíbola",
    "El Dorado", "Shambala", "Agartha", "Lemuria", "Atlántida", "Hesperia", "Arcadia", "Utopía",
    "Cydonia", "Olimpo", "Ícaro", "Dédalo", "Perseo", "Andrómeda", "Casiopea", "Orión",
    "Vega", "Altair", "Deneb", "Rigel", "Betelgeuse", "Antares", "Aldebarán", "Sirio",
    "Pólux", "Cástor", "Arturo", "Capella", "Espiga", "Régulo", "Bellatrix", "Alnilam",
    "Mintaka", "Alnitak", "Fomalhaut", "Achernar", "Canopus", "Mirach", "Alfa Centauri", "Próxima",
    "Kepler", "Trappist", "Gliese", "Wolf 359", "Tau Ceti", "Épsilon Eridani", "Barnard", "Ross 128",
    "Lalande", "Luyten", "Teegarden", "Kapteyn", "Bernard", "Cyrene", "Babilonia", "Nínive",
    "Ur", "Uruk", "Kadingir", "Ishtar", "Marduk", "Anunnaki", "Nibiru", "Enki",
    "Enlil", "Tiamat", "Apsu", "Kur", "Dilmun", "Aaru", "Duat", "Amenti",
    "Heliópolis", "Menfis", "Tebas", "Abidos", "Karnak", "Osiris", "Anubis", "Horus",
    "Ra", "Set", "Thoth", "Sekhmet", "Bastet", "Sobek", "Amón", "Atón",
    "Yggdrasil", "Midgard", "Jotunheim", "Niflheim", "Muspelheim", "Álfheim", "Vanaheim", "Helheim",
    "Bifrost", "Ragnarok", "Fenrir", "Jörmungandr", "Sleipnir", "Gungnir", "Mjölnir", "Draupnir",
    "Excalibur", "Camelot", "Tintagel", "Broceliande", "Ítaca", "Micenas", "Cnosos", "Delfos",
    "Esparta", "Corinto", "Éfeso", "Halicarnaso", "Pérgamo", "Alejandría", "Cartago", "Petra",
    "Palmira", "Persépolis", "Susa", "Ecbatana", "Samarcanda", "Bujará", "Timbuctú", "Zanzíbar",
    "Kioto", "Edo", "Nara", "Kamakura", "Kunlun", "Penglai", "Yaochi", "Fusang",
    "Aztlan Prime", "Teotihuacán", "Tenochtitlan", "Tulán", "Xibalbá", "Mictlán", "Aztatlán", "Cholula",
    "Vulcano", "Kronos II", "Solaris", "Perdición", "Némesis IX", "Zenith", "Nadir", "Cénit",
    "Ocaso", "Crepúsculo", "Alba", "Éter", "Cosmos", "Vacío Estelar", "Confín", "Última Frontera",
]

# Normaliza a ASCII y quita duplicados conservando el orden.
_seen = set()
PLANET_NAMES = []
for _n in (_ascii(x) for x in _RAW):
    if 2 <= len(_n) <= 20 and _n not in _seen:
        _seen.add(_n)
        PLANET_NAMES.append(_n)

import re as _re
_VALID = _re.compile(r"^[a-zA-Z0-9\-_ ]{2,20}$")
assert all(_VALID.match(n) for n in PLANET_NAMES), \
    [n for n in PLANET_NAMES if not _VALID.match(n)]  # todos válidos para el juego
assert len(PLANET_NAMES) >= 200, f"se esperaban >=200 nombres, hay {len(PLANET_NAMES)}"
