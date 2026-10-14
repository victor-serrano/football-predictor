"""
Pruebas unitarias para el módulo estadisticas.py.
Utiliza bases de datos SQLite en memoria para no alterar los datos reales.
"""

import sqlite3
import unittest
from estadisticas import obtener_estadisticas_equipo

ESQUEMA_PARTIDOS = """
CREATE TABLE partidos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    competicion      TEXT    NOT NULL,
    temporada        TEXT    NOT NULL,
    fecha            TEXT    NOT NULL,
    equipo_local     TEXT    NOT NULL,
    equipo_visitante TEXT    NOT NULL,
    goles_local      INTEGER NOT NULL CHECK (goles_local >= 0),
    goles_visitante  INTEGER NOT NULL CHECK (goles_visitante >= 0),
    UNIQUE (competicion, temporada, fecha, equipo_local, equipo_visitante)
);
"""


def crear_bd_test():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(ESQUEMA_PARTIDOS)
    return conn


def poblar_datos_test(conn):
    partidos = [
        ("La Liga", "2024/25", "2024-09-01", "Real Madrid", "Barcelona", 2, 1), # V Local
        ("La Liga", "2024/25", "2024-09-08", "Atletico Madrid", "Real Madrid", 1, 1), # E Visitante
        ("La Liga", "2024/25", "2024-09-15", "Real Madrid", "Sevilla", 3, 0), # V Local
        ("La Liga", "2024/25", "2024-09-22", "Valencia", "Real Madrid", 2, 1), # D Visitante
        ("La Liga", "2024/25", "2024-09-29", "Real Madrid", "Betis", 1, 1), # E Local
        ("La Liga", "2024/25", "2024-10-06", "Villarreal", "Real Madrid", 0, 2), # V Visitante
        ("Champions League", "2024/25", "2024-10-15", "Real Madrid", "AC Milan", 3, 1), # V Local (CL)
        ("La Liga", "2024/25", "2024-10-20", "Real Madrid", "Espanyol", 4, 0), # Partidos futuros
    ]
    conn.executemany("""
        INSERT INTO partidos (competicion, temporada, fecha, equipo_local, equipo_visitante, goles_local, goles_visitante)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, partidos)
    conn.commit()


class PruebasEstadisticas(unittest.TestCase):

    def setUp(self):
        self.conn = crear_bd_test()
        poblar_datos_test(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_calculo_totales_sin_filtro(self):
        stats = obtener_estadisticas_equipo(self.conn, "Real Madrid")
        tot = stats["total"]
        self.assertEqual(tot["partidos"], 8)
        self.assertEqual(tot["victorias"], 5)
        self.assertEqual(tot["empates"], 2)
        self.assertEqual(tot["derrotas"], 1)
        self.assertEqual(tot["gf"], 17)
        self.assertEqual(tot["gc"], 6)
        self.assertEqual(tot["promedio_gf"], 2.12)

    def test_desglose_local_visitante(self):
        stats = obtener_estadisticas_equipo(self.conn, "Real Madrid")
        loc = stats["local"]
        vis = stats["visitante"]

        # Local: 4 partidos (2-1 vs Barça, 3-0 vs Sevilla, 1-1 vs Betis, 3-1 vs Milan, 4-0 vs Espanyol)
        self.assertEqual(loc["partidos"], 5)
        self.assertEqual(loc["victorias"], 4)
        self.assertEqual(loc["empates"], 1)
        self.assertEqual(loc["derrotas"], 0)

        # Visitante: 3 partidos (1-1 @ Atleti, 2-1 @ Valencia, 0-2 @ Villarreal)
        self.assertEqual(vis["partidos"], 3)
        self.assertEqual(vis["victorias"], 1)
        self.assertEqual(vis["empates"], 1)
        self.assertEqual(vis["derrotas"], 1)

    def test_filtro_por_competicion(self):
        stats = obtener_estadisticas_equipo(self.conn, "Real Madrid", competicion="Champions League")
        tot = stats["total"]
        self.assertEqual(tot["partidos"], 1)
        self.assertEqual(tot["victorias"], 1)
        self.assertEqual(tot["gf"], 3)

    def test_fecha_corte_excluye_partidos_futuros(self):
        stats = obtener_estadisticas_equipo(self.conn, "Real Madrid", fecha_corte="2024-10-10")
        tot = stats["total"]
        self.assertEqual(tot["partidos"], 6)
        # El partido del 2024-10-15 (Champions) y el del 2024-10-20 (Espanyol) quedan excluidos
        self.assertEqual(tot["gf"], 10)

    def test_equipo_sin_partidos(self):
        stats = obtener_estadisticas_equipo(self.conn, "Equipo Desconocido")
        tot = stats["total"]
        self.assertEqual(tot["partidos"], 0)
        self.assertEqual(tot["victorias"], 0)
        self.assertEqual(tot["promedio_gf"], 0.0)

    def test_forma_reciente_orden_cronologico(self):
        stats = obtener_estadisticas_equipo(self.conn, "Real Madrid", fecha_corte="2024-10-10")
        u5 = stats["ultimos_5"]
        self.assertEqual(len(u5), 5)
        # El más reciente dentro del corte es Villarreal (2024-10-06)
        self.assertEqual(u5[-1]["rival"], "Villarreal")
        self.assertEqual(u5[-1]["resultado"], "victoria")
        # Racha esperada: E, V, D, E, V -> "EVDEV"
        self.assertEqual(stats["racha_ultimos_5"], "EVDEV")


if __name__ == "__main__":
    unittest.main()