import unittest
import sqlite3
from generador_jornada import buscar_mejores_apuestas


class PruebasGeneradorJornada(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE partidos (
                id INTEGER PRIMARY KEY,
                competicion TEXT,
                temporada TEXT,
                fecha TEXT,
                equipo_local TEXT,
                equipo_visitante TEXT,
                goles_local INTEGER,
                goles_visitante INTEGER
            )
        """)
        # Insertar partidos de prueba
        partidos = [
            ("La Liga", "2023-24", "2024-01-01", "Real Madrid", "Barcelona", 3, 1),
            ("La Liga", "2023-24", "2024-01-02", "Real Madrid", "Getafe", 4, 0),
            ("La Liga", "2023-24", "2024-01-03", "Barcelona", "Getafe", 2, 0),
            ("La Liga", "2023-24", "2024-01-04", "Atletico Madrid", "Sevilla", 1, 1),
        ]
        self.conn.executemany("""
            INSERT INTO partidos (competicion, temporada, fecha, equipo_local, equipo_visitante, goles_local, goles_visitante)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, partidos)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_buscar_mejores_apuestas_devuelve_lista(self):
        res = buscar_mejores_apuestas(self.conn, limite=2, min_probabilidad=40.0)
        self.assertTrue(res["ok"])
        self.assertIn("selecciones", res)
        self.assertIn("probabilidad_combinada_pct", res)


if __name__ == "__main__":
    unittest.main()