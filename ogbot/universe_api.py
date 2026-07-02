"""
universe_api.py
===============
Cliente para la API XML PÚBLICA de OGame. Es de solo lectura y oficial; sirve
para inteligencia del universo sin tocar el cliente del juego:

  /api/serverData.xml   -> parámetros del universo
  /api/universe.xml     -> lista de planetas y a qué jugador pertenecen
  /api/players.xml      -> jugadores + estado (activo / inactivo 'i' / 'I')
  /api/highscore.xml    -> ranking (para evaluar fuerza relativa)
  /api/playerData.xml?id=ID -> detalle de un jugador

Esto es la base del 'buscador de objetivos': cruzamos jugadores inactivos con
sus coordenadas para construir una lista de candidatos a farmear.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET
import requests


@dataclass
class ApiPlayer:
    id: str
    name: str
    status: str = ""        # ''=activo, 'i'=inactivo, 'I'=inactivo largo, 'v'=vacaciones, 'b'=baneado
    rank: int = 999999
    points: int = 0
    planets: List[dict] = field(default_factory=list)  # {coords, name, moon}

    @property
    def is_inactive(self) -> bool:
        return "i" in self.status.lower()

    @property
    def is_protected(self) -> bool:
        # vacaciones o baneado => intocable
        return "v" in self.status or "b" in self.status


class UniverseAPI:
    def __init__(self, server_url: str, cache_ttl: int = 3600, logger=None):
        self.base = server_url.rstrip("/") + "/api"
        self.ttl = cache_ttl
        self.log = logger
        self._cache: Dict[str, tuple] = {}
        self._occupied_cache: Optional[tuple] = None  # (timestamp, set de posiciones)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "ogbot-intel/1.0"

    def _get(self, endpoint: str) -> Optional[ET.Element]:
        now = time.time()
        if endpoint in self._cache and now - self._cache[endpoint][0] < self.ttl:
            return self._cache[endpoint][1]
        try:
            r = self.session.get(f"{self.base}/{endpoint}", timeout=30)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            self._cache[endpoint] = (now, root)
            return root
        except Exception as e:
            if self.log:
                self.log.warning(f"API {endpoint} falló: {e}")
            return None

    # --- parámetros del universo ----------------------------------------
    def server_data(self) -> dict:
        root = self._get("serverData.xml")
        if root is None:
            return {}
        return {child.tag: child.text for child in root}

    # --- jugadores -------------------------------------------------------
    def players(self) -> Dict[str, ApiPlayer]:
        root = self._get("players.xml")
        out: Dict[str, ApiPlayer] = {}
        if root is None:
            return out
        for p in root.findall("player"):
            pid = p.get("id", "")
            out[pid] = ApiPlayer(
                id=pid,
                name=p.get("name", ""),
                status=p.get("status", "") or "",
            )
        return out

    # --- universo (planetas) --------------------------------------------
    def universe(self) -> List[dict]:
        root = self._get("universe.xml")
        out: List[dict] = []
        if root is None:
            return out
        for pl in root.findall("planet"):
            out.append({
                "id": pl.get("id"),
                "player": pl.get("player"),
                "name": pl.get("name"),
                "coords": pl.get("coords"),  # "g:s:p"
                "moon": pl.find("moon") is not None,
            })
        return out

    # --- highscore -------------------------------------------------------
    def highscore(self, category: int = 1, type_: int = 0) -> Dict[str, dict]:
        """type 0=puntos totales, 1=economía, 2=research, 3=militar..."""
        root = self._get(f"highscore.xml?category={category}&type={type_}")
        out: Dict[str, dict] = {}
        if root is None:
            return out
        for p in root.findall("player"):
            out[p.get("id")] = {"rank": int(p.get("position", 999999)),
                                "score": int(float(p.get("score", 0)))}
        return out

    def top_player_points(self, type_: int = 0) -> int:
        """
        Puntos del jugador Top-1 del universo (posición 1 del ranking).
        type 0=puntos totales. Se usa para el tope de botín de expedición.
        Devuelve 0 si la API no responde (el llamante usará el override de config).
        """
        scores = self.highscore(category=1, type_=type_)
        best = 0
        for s in scores.values():
            if s.get("rank") == 1:
                return s.get("score", 0)
            best = max(best, s.get("score", 0))
        return best

    def player_score(self, player_id) -> Optional[dict]:
        """Puntos totales y rank del jugador (highscore category=1 type=0, C5).
        Devuelve {"points": int, "rank": int} o None si no está o falla la API."""
        entry = self.highscore(category=1, type_=0).get(str(player_id))
        if not entry:
            return None
        return {"points": int(entry["score"]), "rank": int(entry["rank"])}

    def player_detail(self, player_id: str) -> dict:
        root = self._get(f"playerData.xml?id={player_id}")
        if root is None:
            return {}
        data = {"id": player_id, "planets": []}
        planets = root.find("planets")
        if planets is not None:
            for pl in planets.findall("planet"):
                data["planets"].append({
                    "coords": pl.get("coords"),
                    "name": pl.get("name"),
                    "moon": pl.find("moon") is not None,
                })
        return data

    # --- composición: candidatos a farmear -------------------------------
    def farm_targets(self, only_inactive: bool = True, max_rank_safe: int = 0) -> List[dict]:
        """
        Cruza players + universe + highscore para devolver planetas de jugadores
        farmeables (no protegidos: sin vacaciones/baneo), enriquecidos con su
        ranking económico. Con only_inactive=False incluye también activos.
        max_rank_safe > 0 excluye jugadores con rank <= max_rank_safe
        (rank 1 = el más fuerte del universo).
        """
        players = self.players()
        scores = self.highscore(category=1, type_=1)  # economía
        targets: List[dict] = []
        for pl in self.universe():
            owner = players.get(pl.get("player"))
            if not owner or owner.is_protected:
                continue
            if only_inactive and not owner.is_inactive:
                continue
            rank = scores.get(owner.id, {}).get("rank", 999999)
            if max_rank_safe > 0 and rank <= max_rank_safe:
                continue
            targets.append({
                "coords": pl["coords"],
                "player_id": owner.id,
                "player_name": owner.name,
                "status": owner.status,
                "econ_rank": rank,
                "has_moon": pl["moon"],
            })
        # Inactivos con mejor economía suelen tener más recursos acumulados
        targets.sort(key=lambda t: t["econ_rank"])
        return targets

    def inactive_targets(self, max_rank_safe: int = 0) -> List[dict]:
        """Planetas de jugadores inactivos (y no protegidos). Ver farm_targets."""
        return self.farm_targets(True, max_rank_safe)

    # --- posiciones ocupadas ----------------------------------------------
    def occupied_positions(self) -> set:
        """
        Set de tuplas (galaxia, sistema, posición) de todos los planetas del
        universo. Cacheado en memoria con el mismo TTL que universe().
        """
        now = time.time()
        if self._occupied_cache is not None and now - self._occupied_cache[0] < self.ttl:
            return self._occupied_cache[1]
        occupied: set = set()
        for pl in self.universe():
            coords = (pl.get("coords") or "").split(":")
            if len(coords) != 3:
                continue
            try:
                occupied.add((int(coords[0]), int(coords[1]), int(coords[2])))
            except ValueError:
                continue
        self._occupied_cache = (now, occupied)
        return occupied
