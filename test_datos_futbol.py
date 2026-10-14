"""
Pruebas de datos_futbol.py.

IMPORTANTE: los CSV de estas pruebas son SINTÉTICOS (equipos ficticios
"Equipo Prueba ..."). Solo existen para verificar la lógica y se guardan en una
base de datos en memoria: nunca tocan data/partidos.db ni usan la red.
"""

import unittest
import urllib.error
from unittest import mock

import datos_futbol as df

CABECERA = "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"

CSV_MIXTO = CABECERA + (
    "SP1,15/08/2025,20:00,Equipo Prueba A,Equipo Prueba B,2,1,H\n"     # válido
    "SP1,16/08/25,18:00,Equipo Prueba C,Equipo Prueba D,0,0,D\n"       # válido (año 2 dígitos)
    "SP1,17/08/2025,18:00,Equipo Prueba E,Equipo Prueba F,,,\n"        # sin resultado
    "SP1,18/08/2025,18:00,Equipo Prueba G,Equipo Prueba H,x,1,\n"      # goles inválidos
    "SP1,fecha-mala,18:00,Equipo Prueba I,Equipo Prueba J,1,1,D\n"     # fecha inválida
    ",,,,,,,\n"                                                        # fila en blanco
)


def bd():
    return df.conectar(":memory:")


def total(conn):
    return conn.execute("SELECT COUNT(*) FROM partidos").fetchone()[0]


class PruebasImportacion(unittest.TestCase):

    def test_importa_solo_partidos_finalizados(self):
        conn = bd()
        r = df.importar_texto(conn, CSV_MIXTO, "La Liga", "2526")
        self.assertEqual(r, {"insertados": 2, "duplicados": 0, "sin_resultado": 1, "invalidos": 2})
        self.assertEqual(total(conn), 2)

    def test_campos_guardados(self):
        conn = bd()
        df.importar_texto(conn, CSV_MIXTO, "La Liga", "2526")
        fila = conn.execute(
            "SELECT competicion, temporada, fecha, equipo_local, equipo_visitante, "
            "goles_local, goles_visitante FROM partidos ORDER BY fecha LIMIT 1").fetchone()
        self.assertEqual(fila, ("La Liga", "2025/26", "2025-08-15",
                                "Equipo Prueba A", "Equipo Prueba B", 2, 1))

    def test_reimportar_no_duplica(self):
        conn = bd()
        df.importar_texto(conn, CSV_MIXTO, "La Liga", "2526")
        r2 = df.importar_texto(conn, CSV_MIXTO, "La Liga", "2526")
        self.assertEqual(r2["insertados"], 0)
        self.assertEqual(r2["duplicados"], 2)
        self.assertEqual(total(conn), 2)

    def test_misma_fila_en_otra_competicion_no_es_duplicado(self):
        conn = bd()
        df.importar_texto(conn, CSV_MIXTO, "La Liga", "2526")
        df.importar_texto(conn, CSV_MIXTO, "Segunda División", "2526")
        self.assertEqual(total(conn), 4)

    def test_archivo_vacio(self):
        for contenido in ("", "   \n\n"):
            with self.assertRaises(df.ErrorDatos):
                df.importar_texto(bd(), contenido, "La Liga", "2526")

    def test_solo_cabecera(self):
        with self.assertRaises(df.ErrorDatos):
            df.importar_texto(bd(), CABECERA, "La Liga", "2526")

    def test_columnas_ausentes(self):
        csv_malo = "Date,HomeTeam,AwayTeam\n15/08/2025,Equipo Prueba A,Equipo Prueba B\n"
        conn = bd()
        with self.assertRaises(df.ErrorDatos) as ctx:
            df.importar_texto(conn, csv_malo, "La Liga", "2526")
        self.assertIn("FTHG", str(ctx.exception))
        self.assertIn("FTAG", str(ctx.exception))
        self.assertEqual(total(conn), 0)

    def test_decodifica_latin1(self):
        crudo = (CABECERA + "SP1,15/08/2025,20:00,Peña Prueba,Equipo Prueba B,1,0,H\n").encode("latin-1")
        conn = bd()
        df.importar_texto(conn, df.decodificar(crudo), "La Liga", "2526")
        local = conn.execute("SELECT equipo_local FROM partidos").fetchone()[0]
        self.assertEqual(local, "Peña Prueba")


class PruebasTemporadas(unittest.TestCase):

    def test_etiquetas(self):
        self.assertEqual(df.etiqueta_temporada("2425"), "2024/25")
        self.assertEqual(df.etiqueta_temporada("9900"), "1999/00")

    def test_temporada_invalida(self):
        for malo in ("2526x", "2527", "25", "abcd"):
            with self.assertRaises(ValueError):
                df.etiqueta_temporada(malo)

    def test_temporadas_por_defecto(self):
        from datetime import date
        self.assertEqual(df.temporadas_por_defecto(date(2026, 10, 14)),
                         ["2324", "2425", "2526", "2627"])
        self.assertEqual(df.temporadas_por_defecto(date(2026, 3, 1), cuantas=2),
                         ["2425", "2526"])


class PruebasDescargaYComandos(unittest.TestCase):

    def test_error_404(self):
        error = urllib.error.HTTPError("http://x", 404, "Not Found", {}, None)
        with mock.patch("datos_futbol.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(df.ErrorDescarga) as ctx:
                df.descargar("http://x")
        self.assertIn("404", str(ctx.exception))

    def test_error_de_conexion(self):
        error = urllib.error.URLError("sin red")
        with mock.patch("datos_futbol.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(df.ErrorDescarga):
                df.descargar("http://x")

    def test_timeout(self):
        with mock.patch("datos_futbol.urllib.request.urlopen", side_effect=TimeoutError("lento")):
            with self.assertRaises(df.ErrorDescarga):
                df.descargar("http://x")

    def test_importar_continua_tras_un_fallo_y_cuenta(self):
        """Una descarga falla, otra llega vacía, otra es correcta: el proceso sigue."""
        respuestas = {
            "SP1": df.ErrorDescarga("sin conexión"),
            "SP2": b"",
            "E0": CSV_MIXTO.encode("utf-8"),
        }

        def falsa_descarga(url):
            codigo = url.rsplit("/", 1)[1].replace(".csv", "")
            r = respuestas[codigo]
            if isinstance(r, Exception):
                raise r
            return r

        conn, mensajes = bd(), []
        ok, fallos = df.importar(conn, ["SP1", "SP2", "E0"], ["2526"], pausa=0,
                                 descargar_fn=falsa_descarga, salida=mensajes.append)
        self.assertEqual((ok, fallos), (1, 2))
        self.assertEqual(total(conn), 2)
        self.assertEqual(conn.execute("SELECT DISTINCT competicion FROM partidos").fetchall(),
                         [("Premier League",)])

    def test_contar(self):
        conn, mensajes = bd(), []
        df.importar_texto(conn, CSV_MIXTO, "La Liga", "2526")
        self.assertEqual(df.contar(conn, salida=mensajes.append), 2)
        self.assertIn("Partidos guardados: 2", mensajes[0])


if __name__ == "__main__":
    unittest.main()