"""Selección de forma de vida: _choose_lf coge el MENOR nivel con botón activo
(equilibra la rama) en vez del primero de la lista (el bug: siempre el Sector
Residencial). Ver ogbot/client.py y ogbot/js/lf_candidates.js."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.client import _choose_lf

# Residencial (11101) es el primero pero está más alto: NO debe ganar; gana el de
# menor nivel activo. Esto es justo lo que fallaba antes ("solo sube residencial").
cands = [
    {"tech": 11101, "level": 20, "enabled": True},   # residencial, muy subido
    {"tech": 11102, "level": 3,  "enabled": True},
    {"tech": 11103, "level": 5,  "enabled": True},
]
assert _choose_lf(cands) == 11102, _choose_lf(cands)

# Empate por nivel -> desempata por menor id (estable).
assert _choose_lf([
    {"tech": 11105, "level": 2, "enabled": True},
    {"tech": 11103, "level": 2, "enabled": True},
]) == 11103

# Los deshabilitados (no asequibles / requisitos sin cumplir) se ignoran aunque sean
# de menor nivel: así respetamos prerequisitos y recursos sin modelar el árbol.
assert _choose_lf([
    {"tech": 11102, "level": 0, "enabled": False},
    {"tech": 11101, "level": 7, "enabled": True},
]) == 11101

# Nada activo (p.ej. cola ya ocupada -> OGame deshabilita todo) -> None (no encola).
assert _choose_lf([{"tech": 11101, "level": 1, "enabled": False}]) is None
assert _choose_lf([]) is None

print("OK test_lf_pick")
