"""
Pruebas unitarias para prediccion.py.
Garantiza el cálculo correcto de lambdas, comprobación de fugas de datos (futuro)
y manejo de muestras insuficientes en bases SQLite en memoria.
"""

import sqlite3
import unittest
from prediccion import calcular_lambdas, predecir_partido

ESQUEMA = """
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


def crear_bd():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(ESQUEMA)
    return conn


def poblar_datos(conn):
    partidos = [
        ("La Liga", "2024/25", "2024-09-01", "Real Madrid", "Barcelona", 2, 1),
        ("La Liga", "2024/25", "2024-09-08", "Real Madrid", "Sevilla", 3, 0),
        ("La Liga", "2024/25", "2024-09-15", "Real Madrid", "Betis", 1, 0),
        ("La Liga", "2024/25", "2024-09-01", "Valencia", "Barcelona", 0, 2),
        ("La Liga", "2024/25", "2024-09-08", "Atletico Madrid", "Barcelona", 1, 2),
        ("La Liga", "2024/25", "2024-09-15", "Sevilla", "Barcelona", 1, 3),
        # Partido futuro para test de fuga
        ("La Liga", "2024/25", "2024-10-20", "Real Madrid", "Barcelona", 5, 0),
    ]
    conn.executemany("""
        INSERT INTO partidos (competicion, temporada, fecha, equipo_local, equipo_visitante, goles_local, goles_visitante)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, partidos)
    conn.commit()


class PruebasPrediccion(unittest.TestCase):

    def setUp(self):
        self.conn = crear_bd()
        poblar_datos(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_evita_fuga_informacion_fecha_corte(self):
        # Con corte al 2024-10-01, el partido del 2024-10-20 (5-0) NO debe influir.
        res = predecir_partido(self.conn, "Real Madrid", "Barcelona", fecha_corte="2024-10-01")
        self.assertTrue(res["ok"])
        # Real Madrid tiene 3 partidos locales (2+3+1 = 6 goles -> media 2.0)
        self.assertEqual(res["detalles"]["stats_local"]["partidos"], 3)
        self.assertEqual(res["detalles"]["stats_local"]["gf"], 6)

    def test_calculo_mercados_suma_100_por_ciento(self):
        res = predecir_partido(self.conn, "Real Madrid", "Barcelona", fecha_corte="2024-10-01")
        m = res["mercados"]
        suma_1x2 = m["local"] + m["empate"] + m["visitante"]
        suma_ou25 = m["over25"] + m["under25"]
        self.assertAlmostEqual(suma_1x2, 1.0, places=5)
        self.assertAlmostEqual(suma_ou25, 1.0, places=5)

    def test_equipo_sin_datos(self):
        res = predecir_partido(self.conn, "Equipo Ficticio", "Barcelona")
        self.assertFalse(res["ok"])
        self.assertIn("Datos insuficientes", res["error"])


if __name__ == "__main__":
    unittest.main()