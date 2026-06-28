import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Agregar el directorio principal al path
sys.path.append(r"c:\Users\jbr65\Downloads\ogame-bot\ogame-bot")

from ogbot.config import Config
from ogbot.brain import Brain

class TestScheduler(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.cfg.state_file = "test_state.json"
        if os.path.exists(self.cfg.state_file):
            os.remove(self.cfg.state_file)
            
        # Mockear Config.load para evitar lectura de disco
        Config.load = MagicMock(return_value=self.cfg)
        self.brain = Brain(self.cfg)
        
        # Mockear llamadas externas de cycle()
        self.brain.client = MagicMock()
        self.brain.client.read_planets.return_value = []
        
        # Mockear los métodos de paso
        self.brain._recycle = MagicMock()
        self.brain._expeditions = MagicMock()
        self.brain._economy_step = MagicMock()
        self.brain._defense_step = MagicMock()
        self.brain._lifeforms_step = MagicMock()
        self.brain._facilities_step = MagicMock()
        self.brain._research_step = MagicMock()
        self.brain._fleet_step = MagicMock()
        self.brain._farm = MagicMock()
        self.brain._colonize = MagicMock()
        self.brain._moonshot = MagicMock()
        self.brain.update_imperial_stats = MagicMock()
        self.brain._has_ships = MagicMock(return_value=True)
        self.brain._has_free_slots_for_mission = MagicMock(return_value=True)
        self.brain._has_free_expe_slots = MagicMock(return_value=True)

        # El test comprueba SOLO el agendado por intervalos de cycle(); mockeamos la
        # lectura/persistencia de estado para no tropezar con la aritmética sobre los
        # planetas MagicMock (building_remaining_seconds, build_finish_epoch, etc.).
        self.brain.client.read_movements.return_value = []
        self.brain._read_location_state = MagicMock()
        self.brain._read_research_smart = MagicMock(return_value={})
        self.brain._aggregate_ships_in_motion = MagicMock(return_value={})
        self.brain._write_build_status = MagicMock()
        self.brain._process_build_queues = MagicMock()

    def tearDown(self):
        if os.path.exists(self.cfg.state_file):
            os.remove(self.cfg.state_file)

    def test_default_intervals_run_always(self):
        # Por defecto es 0, lo que significa correr en cada ciclo
        self.cfg.economy_run_interval_mins = 0
        self.cfg.farming_run_interval_mins = 0
        
        mock_planet = MagicMock()
        mock_planet.coords.type = "planet"
        mock_planet.coords.tuple.return_value = (1, 1, 1)
        mock_planet.has_moon = False
        mock_planet.ships = {"large_cargo": 10}
        mock_planet.defenses = {}
        mock_planet.buildings = {}
        self.brain.client.read_planets.return_value = [mock_planet]
        self.brain.client.read_fleet_slots.return_value = {}
        self.brain.client.read_research.return_value = {}

        # Ciclo 1
        self.brain.cycle()
        self.assertTrue(self.brain._recycle.called)
        self.assertTrue(self.brain._economy_step.called)
        
        self.brain._recycle.reset_mock()
        self.brain._economy_step.reset_mock()

        # Ciclo 2 inmediato
        self.brain.cycle()
        self.assertTrue(self.brain._recycle.called)
        self.assertTrue(self.brain._economy_step.called)

    def test_configured_intervals_respected(self):
        self.cfg.economy_run_interval_mins = 30
        self.cfg.farming_run_interval_mins = 10
        # El reciclaje tiene su propio intervalo independiente; lo igualamos al de farmeo
        # para que _recycle siga el mismo patrón que comprueba este test.
        self.cfg.recycling_run_interval_mins = 10
        
        mock_planet = MagicMock()
        mock_planet.coords.type = "planet"
        mock_planet.coords.tuple.return_value = (1, 1, 1)
        mock_planet.has_moon = False
        mock_planet.ships = {"large_cargo": 10}
        mock_planet.defenses = {}
        mock_planet.buildings = {}
        self.brain.client.read_planets.return_value = [mock_planet]
        self.brain.client.read_fleet_slots.return_value = {}
        self.brain.client.read_research.return_value = {}

        # Ejecución 1: Ambos last_run están en 0.0, deben ejecutarse
        self.brain.cycle()
        self.assertTrue(self.brain._recycle.called)
        self.assertTrue(self.brain._economy_step.called)
        
        self.brain._recycle.reset_mock()
        self.brain._economy_step.reset_mock()

        # Ejecución 2: Ciclo inmediato. Ninguno debería correr porque no ha pasado suficiente tiempo
        self.brain.cycle()
        self.assertFalse(self.brain._recycle.called)
        self.assertFalse(self.brain._economy_step.called)

        # Ejecución 3: Avanzar tiempo 11 minutos (debe correr farmeo pero economía no)
        now_val = time.time()
        with patch('time.time', return_value=now_val + 11 * 60):
            self.brain.cycle()
            self.assertTrue(self.brain._recycle.called)
            self.assertFalse(self.brain._economy_step.called)
            
        self.brain._recycle.reset_mock()
        self.brain._economy_step.reset_mock()

        # Ejecución 4: Avanzar tiempo 31 minutos (ambos deben correr)
        with patch('time.time', return_value=now_val + 31 * 60):
            self.brain.cycle()
            self.assertTrue(self.brain._recycle.called)
            self.assertTrue(self.brain._economy_step.called)

if __name__ == "__main__":
    unittest.main()
