"""
Pruebas de api_football_data.py.

NINGUNA prueba usa la red, el token real ni pausas reales:
  * las respuestas HTTP son simuladas,
  * el reloj y el 'sleep' están sustituidos por funciones falsas,
  * la base de datos es SQLite en memoria.
Los partidos y equipos de las fixtures son SINTÉTICOS (nombres ficticios).
"""

import argparse
import io
import json
import sqlite3
import unittest
import urllib.error
from collections import Counter

import api_football_data as api

TOKEN = "TOKEN_FALSO_abc123XYZ"

# Copia del esquema entregado en la tarea del importador de CSV.
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
)
"""


# ---------------------------------------------------------------------------
# Utilidades de simulación
# ---------------------------------------------------------------------------
def bd(con_partidos=True):
    conn = sqlite3.connect(":memory:")
    if con_partidos:
        conn.execute(ESQUEMA_PARTIDOS)
    return conn


def bd_lista():
    return api.preparar_bd(bd())


def partido(id_=1, local="Equipo Prueba A", visitante="Equipo Prueba B", gl=2, gv=1,
            status="FINISHED", duracion="REGULAR", fecha="2025-09-17T19:00:00Z", **extra_score):
    score = {"winner": "HOME_TEAM", "duration": duracion,
             "fullTime": {"home": gl, "away": gv}, "halfTime": {"home": 1, "away": 0}}
    score.update(extra_score)
    return {"id": id_, "utcDate": fecha, "status": status, "stage": "LEAGUE_STAGE",
            "homeTeam": {"id": 10, "name": local}, "awayTeam": {"id": 11, "name": visitante},
            "score": score}


class Respuesta:
    def __init__(self, cuerpo, cabeceras=None):
        self._cuerpo = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
        self.headers = cabeceras or {}

    def read(self):
        return self._cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def error_http(codigo, cuerpo=None, cabeceras=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else b""
    return urllib.error.HTTPError("https://api.football-data.org/v4/x", codigo, "err",
                                  cabeceras or {}, io.BytesIO(datos))


class AbrirFalso:
    """Sustituye a urlopen: devuelve/lanza lo programado, en orden, y guarda las peticiones."""
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.peticiones = []

    def __call__(self, peticion, timeout=None):
        self.peticiones.append(peticion)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class Reloj:
    """Reloj y sleep falsos: dormir() avanza el tiempo sin esperar."""
    def __init__(self):
        self.ahora = 1000.0
        self.pausas = []

    def reloj(self):
        return self.ahora

    def dormir(self, segundos):
        self.pausas.append(segundos)
        self.ahora += segundos


def cliente(*respuestas, reloj=None):
    reloj = reloj or Reloj()
    abrir = AbrirFalso(*respuestas)
    c = api.Cliente(TOKEN, abrir=abrir, dormir=reloj.dormir, reloj=reloj.reloj)
    return c, abrir, reloj


def total(conn, tabla="partidos"):
    return conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]


# ---------------------------------------------------------------------------
# Validación de partidos
# ---------------------------------------------------------------------------
class PruebasExtraccion(unittest.TestCase):

    def test_resultado_regular_usa_fulltime(self):
        reg, motivo = api.extraer_partido(partido(gl=3, gv=0))
        self.assertIsNone(motivo)
        self.assertEqual((reg["goles_local"], reg["goles_visitante"]), (3, 0))
        self.assertEqual(reg["fecha"], "2025-09-17")

    def test_penaltis_usa_marcador_de_90_minutos_no_fulltime(self):
        # fullTime 7-6 incluye penaltis; a los 90' fue 1-1.
        p = partido(gl=7, gv=6, duracion="PENALTY_SHOOTOUT",
                    regularTime={"home": 1, "away": 1}, extraTime={"home": 0, "away": 0},
                    penalties={"home": 6, "away": 5})
        reg, _ = api.extraer_partido(p)
        self.assertEqual((reg["goles_local"], reg["goles_visitante"]), (1, 1))

    def test_prorroga_usa_marcador_de_90_minutos(self):
        p = partido(gl=3, gv=2, duracion="EXTRA_TIME", regularTime={"home": 2, "away": 2})
        reg, _ = api.extraer_partido(p)
        self.assertEqual((reg["goles_local"], reg["goles_visitante"]), (2, 2))

    def test_penaltis_sin_marcador_de_90_se_descarta(self):
        reg, motivo = api.extraer_partido(partido(gl=7, gv=6, duracion="PENALTY_SHOOTOUT"))
        self.assertIsNone(reg)
        self.assertEqual(motivo, "sin_marcador")

    def test_solo_finalizados(self):
        for estado in ("SCHEDULED", "TIMED", "IN_PLAY", "PAUSED", "POSTPONED",
                       "CANCELLED", "SUSPENDED", "AWARDED"):
            reg, motivo = api.extraer_partido(partido(status=estado))
            self.assertIsNone(reg, estado)
            self.assertEqual(motivo, "no_finalizado")

    def test_marcadores_invalidos(self):
        casos = [
            {"home": None, "away": None},     # sin resultado
            {"home": 2, "away": None},        # incompleto
            {"home": -1, "away": 0},          # negativo
            {"home": "2", "away": "1"},       # texto, no entero
            {"home": True, "away": 1},        # booleano
            {"home": 1.5, "away": 0},         # decimal
        ]
        for full in casos:
            p = partido()
            p["score"]["fullTime"] = full
            reg, motivo = api.extraer_partido(p)
            self.assertIsNone(reg, full)
            self.assertEqual(motivo, "sin_marcador")

    def test_sin_score_ni_equipos_ni_fecha_ni_id(self):
        p = partido(); del p["score"]
        self.assertEqual(api.extraer_partido(p)[1], "sin_marcador")
        p = partido(local=""); self.assertEqual(api.extraer_partido(p)[1], "sin_equipos")
        p = partido(); p["homeTeam"] = None
        self.assertEqual(api.extraer_partido(p)[1], "sin_equipos")
        p = partido(fecha="no-es-fecha"); self.assertEqual(api.extraer_partido(p)[1], "sin_fecha")
        p = partido(); p["id"] = "abc"; self.assertEqual(api.extraer_partido(p)[1], "sin_id")
        self.assertEqual(api.extraer_partido("basura")[1], "formato")

    def test_duracion_desconocida(self):
        self.assertEqual(api.extraer_partido(partido(duracion="OTRA"))[1], "duracion_desconocida")

    def test_acepta_claves_hometeam_awayteam_de_la_documentacion(self):
        p = partido()
        p["score"]["fullTime"] = {"homeTeam": 4, "awayTeam": 2}
        reg, _ = api.extraer_partido(p)
        self.assertEqual((reg["goles_local"], reg["goles_visitante"]), (4, 2))


# ---------------------------------------------------------------------------
# Importación y duplicados
# ---------------------------------------------------------------------------
class PruebasImportacion(unittest.TestCase):

    def respuesta_cl(self):
        return {"matches": [
            partido(1, "Equipo Prueba A", "Equipo Prueba B", 2, 1),
            partido(2, "Equipo Prueba C", "Equipo Prueba D", 0, 0),
            partido(3, "Equipo Prueba E", "Equipo Prueba F", 1, 1, duracion="PENALTY_SHOOTOUT",
                    regularTime={"home": 1, "away": 1}),
            partido(4, "Equipo Prueba G", "Equipo Prueba H", status="SCHEDULED"),
            partido(5, "Equipo Prueba I", "Equipo Prueba J", duracion="EXTRA_TIME"),  # sin regularTime
        ]}

    def test_importa_solo_validos_y_guarda_campos(self):
        conn = bd_lista()
        c, abrir, _ = cliente(Respuesta(self.respuesta_cl()))
        r = api.importar_temporada(conn, c, "CL", 2025)
        self.assertEqual((r["recibidos"], r["insertados"], r["duplicados"], r["descartados"]),
                         (5, 3, 0, 2))
        self.assertEqual(r["motivos"], Counter({"no_finalizado": 1, "sin_marcador": 1}))
        self.assertEqual(total(conn), 3)
        fila = conn.execute("SELECT competicion, temporada, fecha, equipo_local, equipo_visitante, "
                            "goles_local, goles_visitante FROM partidos WHERE equipo_local = ?",
                            ("Equipo Prueba A",)).fetchone()
        self.assertEqual(fila, ("Champions League", "2025/26", "2025-09-17",
                                "Equipo Prueba A", "Equipo Prueba B", 2, 1))
        self.assertEqual(total(conn, "fuente_api_partidos"), 3)

    def test_peticion_correcta(self):
        conn = bd_lista()
        c, abrir, _ = cliente(Respuesta({"matches": []}))
        api.importar_temporada(conn, c, "CL", 2024)
        url = abrir.peticiones[0].full_url
        self.assertTrue(url.startswith("https://api.football-data.org/v4/competitions/CL/matches?"))
        self.assertIn("season=2024", url)
        self.assertIn("status=FINISHED", url)

    def test_reimportar_no_duplica(self):
        conn = bd_lista()
        c, _, _ = cliente(Respuesta(self.respuesta_cl()), Respuesta(self.respuesta_cl()))
        api.importar_temporada(conn, c, "CL", 2025)
        r2 = api.importar_temporada(conn, c, "CL", 2025)
        self.assertEqual((r2["insertados"], r2["duplicados"]), (0, 3))
        self.assertEqual(total(conn), 3)
        self.assertEqual(total(conn, "fuente_api_partidos"), 3)

    def test_mismo_id_con_nombre_de_equipo_cambiado_no_duplica(self):
        conn = bd_lista()
        primera = {"matches": [partido(1, "Equipo Prueba A", "Equipo Prueba B")]}
        renombrada = {"matches": [partido(1, "Equipo Prueba A FC", "Equipo Prueba B")]}
        c, _, _ = cliente(Respuesta(primera), Respuesta(renombrada))
        api.importar_temporada(conn, c, "CL", 2025)
        r = api.importar_temporada(conn, c, "CL", 2025)
        self.assertEqual((r["insertados"], r["duplicados"]), (0, 1))
        self.assertEqual(total(conn), 1)

    def test_fila_preexistente_de_otra_fuente_no_se_duplica_ni_se_modifica(self):
        conn = bd_lista()
        conn.execute("INSERT INTO partidos (competicion, temporada, fecha, equipo_local, "
                     "equipo_visitante, goles_local, goles_visitante) VALUES "
                     "('Champions League', '2025/26', '2025-09-17', 'Equipo Prueba A', "
                     "'Equipo Prueba B', 2, 1)")
        conn.commit()
        c, _, _ = cliente(Respuesta({"matches": [partido(1)]}))
        r = api.importar_temporada(conn, c, "CL", 2025)
        self.assertEqual((r["insertados"], r["duplicados"]), (0, 1))
        self.assertEqual(total(conn), 1)
        self.assertEqual(total(conn, "fuente_api_partidos"), 1)   # queda vinculada

    def test_historial_existente_intacto(self):
        conn = bd_lista()
        conn.execute("INSERT INTO partidos (competicion, temporada, fecha, equipo_local, "
                     "equipo_visitante, goles_local, goles_visitante) VALUES "
                     "('La Liga', '2024/25', '2024-08-15', 'Equipo Historico X', "
                     "'Equipo Historico Y', 3, 3)")
        conn.commit()
        antes = conn.execute("SELECT * FROM partidos").fetchall()
        c, _, _ = cliente(Respuesta(self.respuesta_cl()))
        api.importar_temporada(conn, c, "CL", 2025)
        despues = conn.execute("SELECT * FROM partidos WHERE competicion = 'La Liga'").fetchall()
        self.assertEqual(antes, despues)

    def test_respuesta_sin_lista_matches(self):
        conn = bd_lista()
        c, _, _ = cliente(Respuesta({"otra_cosa": 1}))
        with self.assertRaises(api.ErrorAPI):
            api.importar_temporada(conn, c, "CL", 2025)
        self.assertEqual(total(conn), 0)

    def test_actualizar_registra_estado_y_continua_tras_restriccion(self):
        conn = bd_lista()
        c, _, _ = cliente(error_http(403, {"message": "restringido"}),
                          Respuesta(self.respuesta_cl()))
        mensajes = []
        fallos = api.actualizar(conn, c, ["CL"], [2023, 2025], salida=mensajes.append)
        self.assertEqual(fallos, 1)
        self.assertEqual(total(conn), 3)
        filas = conn.execute("SELECT temporada, estado FROM importaciones_api ORDER BY id").fetchall()
        self.assertEqual(filas, [("2023/24", "restringido"), ("2025/26", "ok")])
        self.assertTrue(any("RESTRINGIDO" in m for m in mensajes))

    def test_actualizar_se_detiene_ante_limite_persistente(self):
        conn = bd_lista()
        c, abrir, _ = cliente(error_http(429, cabeceras={"X-RequestCounter-Reset": "10"}),
                              error_http(429, cabeceras={"X-RequestCounter-Reset": "10"}),
                              Respuesta(self.respuesta_cl()))
        fallos = api.actualizar(conn, c, ["CL"], [2023, 2024], salida=lambda m: None)
        self.assertEqual(fallos, 1)
        self.assertEqual(len(abrir.peticiones), 2)     # no se intentó la segunda temporada
        self.assertEqual(total(conn), 0)


# ---------------------------------------------------------------------------
# Cliente: límites, errores y token
# ---------------------------------------------------------------------------
class PruebasCliente(unittest.TestCase):

    def test_token_en_cabecera_y_no_en_url(self):
        c, abrir, _ = cliente(Respuesta({"matches": []}))
        c.obtener("/competitions/CL/matches", {"season": 2025})
        peticion = abrir.peticiones[0]
        self.assertEqual(peticion.get_header("X-auth-token"), TOKEN)
        self.assertNotIn(TOKEN, peticion.full_url)

    def test_espacia_las_peticiones(self):
        c, _, reloj = cliente(Respuesta({}), Respuesta({}), Respuesta({}))
        for _ in range(3):
            c.obtener("/x")
        self.assertEqual(len(reloj.pausas), 2)                  # no espera antes de la primera
        self.assertTrue(all(p >= api.INTERVALO_SEGUNDOS - 1e-9 for p in reloj.pausas))

    def test_cabecera_sin_peticiones_restantes_espera_al_reinicio(self):
        agotado = Respuesta({}, {"X-RequestsAvailable": "0", "X-RequestCounter-Reset": "20"})
        c, _, reloj = cliente(agotado, Respuesta({}))
        c.obtener("/x")
        c.obtener("/x")
        self.assertEqual(reloj.pausas, [21])

    def test_429_espera_y_reintenta_una_vez(self):
        c, abrir, reloj = cliente(error_http(429, cabeceras={"X-RequestCounter-Reset": "15"}),
                                  Respuesta({"ok": True}))
        self.assertEqual(c.obtener("/x"), {"ok": True})
        self.assertEqual(len(abrir.peticiones), 2)
        self.assertEqual(reloj.pausas, [16])

    def test_429_persistente_lanza_error_de_limite(self):
        c, abrir, _ = cliente(error_http(429), error_http(429))
        with self.assertRaises(api.ErrorLimite):
            c.obtener("/x")
        self.assertEqual(len(abrir.peticiones), 2)              # un solo reintento

    def test_errores_http(self):
        casos = [(403, api.ErrorAcceso), (401, api.ErrorAcceso), (400, api.ErrorAPI),
                 (404, api.ErrorAPI), (500, api.ErrorAPI), (503, api.ErrorAPI)]
        for codigo, clase in casos:
            c, _, _ = cliente(error_http(codigo))
            with self.assertRaises(clase, msg=codigo):
                c.obtener("/x")

    def test_errores_de_red_y_respuestas_invalidas(self):
        for fallo in (urllib.error.URLError("sin conexion"), TimeoutError("lento"),
                      Respuesta(b""), Respuesta(b"   "), Respuesta(b"<html>no json</html>"),
                      Respuesta(b"[1, 2, 3]")):
            c, _, _ = cliente(fallo)
            with self.assertRaises(api.ErrorAPI):
                c.obtener("/x")

    def test_el_token_nunca_aparece_en_errores_ni_repr(self):
        eco = {"message": f"token invalido {TOKEN}"}
        fallos = [error_http(400, eco), error_http(403, eco), error_http(500, eco),
                  urllib.error.URLError(f"fallo con {TOKEN}"), OSError(f"red {TOKEN}")]
        for fallo in fallos:
            c, _, _ = cliente(fallo)
            with self.assertRaises(api.ErrorAPI) as ctx:
                c.obtener("/x")
            self.assertNotIn(TOKEN, str(ctx.exception))
            self.assertNotIn(TOKEN, repr(ctx.exception))
        self.assertNotIn(TOKEN, repr(c))

    def test_el_token_nunca_aparece_en_la_salida_de_los_comandos(self):
        salida = []
        eco = {"message": f"eco {TOKEN}"}
        conn = bd_lista()
        c, _, _ = cliente(error_http(400, eco), error_http(403, eco), Respuesta({"matches": []}))
        api.actualizar(conn, c, ["CL"], [2023, 2024, 2025], salida=salida.append)
        c2, _, _ = cliente(error_http(400, eco))
        api.comprobar(c2, ["CL"], 2025, salida=salida.append)
        api.mostrar_estado(conn, True, salida=salida.append)
        registros = " ".join(f[0] for f in conn.execute("SELECT mensaje FROM importaciones_api"))
        self.assertNotIn(TOKEN, "\n".join(salida))
        self.assertNotIn(TOKEN, registros)


class PruebasToken(unittest.TestCase):

    def test_lee_solo_de_la_variable_de_entorno(self):
        self.assertEqual(api.leer_token({api.VARIABLE_TOKEN: f"  {TOKEN}\n"}), TOKEN)

    def test_ausente_o_vacio(self):
        for entorno in ({}, {api.VARIABLE_TOKEN: ""}, {api.VARIABLE_TOKEN: "   "}):
            with self.assertRaises(api.ErrorToken):
                api.leer_token(entorno)

    def test_caracteres_no_validos_sin_eco_del_valor(self):
        with self.assertRaises(api.ErrorToken) as ctx:
            api.leer_token({api.VARIABLE_TOKEN: "abc def\nghi"})
        self.assertNotIn("abc", str(ctx.exception))

    def test_main_sin_token_falla_limpiamente(self):
        import contextlib
        for comando in (["comprobar"], ["actualizar"]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(api.main(comando, entorno={}), 2)
            self.assertIn(api.VARIABLE_TOKEN, err.getvalue())   # nombra la variable, no un valor


# ---------------------------------------------------------------------------
# Esquema, comprobar, estado y argumentos
# ---------------------------------------------------------------------------
class PruebasEsquemaYComandos(unittest.TestCase):

    def test_sin_tabla_partidos(self):
        with self.assertRaises(api.ErrorEsquema):
            api.preparar_bd(bd(con_partidos=False))

    def test_columnas_ausentes(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE partidos (id INTEGER PRIMARY KEY, fecha TEXT)")
        with self.assertRaises(api.ErrorEsquema) as ctx:
            api.preparar_bd(conn)
        self.assertIn("competicion", str(ctx.exception))

    def test_preparar_no_modifica_partidos_y_crea_tablas_propias(self):
        conn = bd()
        antes = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'partidos'").fetchone()
        api.preparar_bd(conn)
        api.preparar_bd(conn)      # idempotente
        despues = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'partidos'").fetchone()
        self.assertEqual(antes, despues)
        tablas = {f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"fuente_api_partidos", "importaciones_api"} <= tablas)

    def test_abrir_bd_inexistente_no_la_crea(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            ruta = pathlib.Path(d) / "no_existe.db"
            with self.assertRaises(api.ErrorEsquema):
                api.abrir_bd(ruta)
            self.assertFalse(ruta.exists())

    def test_comprobar_clasifica_competiciones_y_no_escribe(self):
        respuestas = [
            Respuesta({"matches": [partido(1), partido(2, status="SCHEDULED")]}),   # CL
            error_http(403),                                                         # SA
            error_http(500),                                                         # FL1
        ]
        c, abrir, _ = cliente(*respuestas)
        mensajes = []
        res = api.comprobar(c, ["CL", "SA", "FL1"], 2025, salida=mensajes.append)
        self.assertEqual(res, {"CL": "accesible", "SA": "restringida", "FL1": "error"})
        self.assertTrue(any("ACCESIBLE: 2 finalizados, 1 importables" in m for m in mensajes))
        self.assertTrue(any("RESTRINGIDA" in m for m in mensajes))
        self.assertEqual(len(abrir.peticiones), 3)

    def test_comprobar_se_detiene_ante_limite(self):
        c, abrir, _ = cliente(error_http(429), error_http(429), Respuesta({"matches": []}))
        res = api.comprobar(c, ["CL", "SA"], 2025, salida=lambda m: None)
        self.assertEqual(res, {"CL": "error"})
        self.assertEqual(len(abrir.peticiones), 2)

    def test_estado(self):
        conn = bd_lista()
        mensajes = []
        api.mostrar_estado(conn, False, salida=mensajes.append)
        self.assertIn("NO configurado", mensajes[0])
        self.assertTrue(any("Aún no se ha ejecutado" in m for m in mensajes))

        c, _, _ = cliente(Respuesta({"matches": [partido(1), partido(2, "Equipo Prueba C",
                                                                       "Equipo Prueba D")]}))
        api.actualizar(conn, c, ["CL"], [2025], salida=lambda m: None)
        mensajes = []
        api.mostrar_estado(conn, True, salida=mensajes.append)
        texto = "\n".join(mensajes)
        self.assertIn("configurado", texto)
        self.assertIn("vinculados a la API: 2", texto)
        self.assertIn("Champions League", texto)
        self.assertIn("OK", texto)

    def test_competiciones_solapadas_bloqueadas(self):
        for codigo in ("PD", "pl", "BL1", "SD"):
            with self.assertRaises(argparse.ArgumentTypeError) as ctx:
                api.codigo_valido(codigo)
            self.assertIn("duplicad", str(ctx.exception))
        self.assertEqual(api.codigo_valido("cl"), "CL")
        with self.assertRaises(argparse.ArgumentTypeError):
            api.codigo_valido("UCL")      # Conference League: no soportada, evita confusión con CL

    def test_validacion_de_temporada(self):
        self.assertEqual(api.anio_valido("2025"), 2025)
        for malo in ("2526", "25", "abc", "1900"):
            with self.assertRaises(argparse.ArgumentTypeError):
                api.anio_valido(malo)

    def test_temporada_actual(self):
        from datetime import date
        self.assertEqual(api.temporada_actual(date(2026, 10, 14)), 2026)
        self.assertEqual(api.temporada_actual(date(2026, 3, 1)), 2025)


if __name__ == "__main__":
    unittest.main()