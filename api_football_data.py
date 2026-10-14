"""
Módulo de datos: importa partidos finalizados desde football-data.org API v4.

Independiente de datos_futbol.py (el importador de CSV): no lo importa ni lo
modifica. Escribe en la misma base SQLite, en la tabla `partidos` (solo INSERT)
y en dos tablas nuevas propias: `fuente_api_partidos` e `importaciones_api`.

Solo biblioteca estándar. El token se lee EXCLUSIVAMENTE de la variable de
entorno FOOTBALL_DATA_API_TOKEN y nunca se imprime, guarda ni incluye en errores.

Comandos (desde la carpeta del proyecto):
    python3 api_football_data.py comprobar  [--competiciones CL SA] [--temporada 2025]
    python3 api_football_data.py actualizar [--competiciones CL]    [--temporadas 2024 2025]
    python3 api_football_data.py estado

Marcador guardado: goles a los 90 minutos. Con prórroga o penaltis la API
devuelve en `fullTime` un total que los incluye, así que se usa `regularTime`;
si no está disponible, el partido se descarta (no se adivina).
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
RUTA_BD = Path(__file__).resolve().parent / "data" / "partidos.db"
URL_BASE = "https://api.football-data.org/v4"
VARIABLE_TOKEN = "FOOTBALL_DATA_API_TOKEN"
USER_AGENT = "football-predictor-personal/0.1 (uso privado)"

# Plan gratuito: 10 peticiones/minuto. 6,5 s entre llamadas -> máx. ~9,2/min.
INTERVALO_SEGUNDOS = 6.5
TIMEOUT_SEGUNDOS = 30
ESPERA_MAXIMA_SEGUNDOS = 65

# Código de la API -> nombre guardado en la columna `competicion`.
# Nota: UCL es la Conference League; la Champions League es CL.
COMPETICIONES = {
    "CL": "Champions League",
    "SA": "Serie A",
    "FL1": "Ligue 1",
    "DED": "Eredivisie",
    "PPL": "Primeira Liga",
    "ELC": "Championship",
}

# Ya importadas desde Football-Data.co.uk con otros nombres de equipo:
# importarlas aquí duplicaría partidos. Bloqueadas hasta tener equivalencias.
SOLAPADAS = {
    "PD": "La Liga",
    "SD": "Segunda División",
    "PL": "Premier League",
    "BL1": "Bundesliga",
}

COLUMNAS_PARTIDOS = ("id", "competicion", "temporada", "fecha", "equipo_local",
                     "equipo_visitante", "goles_local", "goles_visitante")

SQL_FUENTE = """
CREATE TABLE IF NOT EXISTS fuente_api_partidos (
    api_id     INTEGER PRIMARY KEY,                 -- id del partido en football-data.org
    partido_id INTEGER NOT NULL REFERENCES partidos(id)
)
"""

SQL_REGISTRO = """
CREATE TABLE IF NOT EXISTS importaciones_api (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_utc   TEXT    NOT NULL,
    codigo      TEXT    NOT NULL,
    competicion TEXT    NOT NULL,
    temporada   TEXT    NOT NULL,
    estado      TEXT    NOT NULL,       -- ok | restringido | error
    recibidos   INTEGER NOT NULL DEFAULT 0,
    insertados  INTEGER NOT NULL DEFAULT 0,
    duplicados  INTEGER NOT NULL DEFAULT 0,
    descartados INTEGER NOT NULL DEFAULT 0,
    mensaje     TEXT    NOT NULL DEFAULT ''
)
"""


# ---------------------------------------------------------------------------
# Errores (ningún mensaje incluye el token)
# ---------------------------------------------------------------------------
class ErrorAPI(Exception):
    """Fallo de red, respuesta inválida o error HTTP de la API."""


class ErrorToken(ErrorAPI):
    """Token ausente o con formato no válido."""


class ErrorAcceso(ErrorAPI):
    """HTTP 401/403: recurso no disponible con este token/plan."""


class ErrorLimite(ErrorAPI):
    """HTTP 429 persistente: límite de peticiones excedido."""


class ErrorEsquema(Exception):
    """La base de datos no tiene el esquema esperado."""


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------
def leer_token(entorno=None) -> str:
    entorno = os.environ if entorno is None else entorno
    token = (entorno.get(VARIABLE_TOKEN) or "").strip()
    if not token:
        raise ErrorToken(f"falta la variable de entorno {VARIABLE_TOKEN}")
    if any(c.isspace() or not c.isprintable() for c in token):
        raise ErrorToken(f"el valor de {VARIABLE_TOKEN} contiene caracteres no válidos")
    return token


# ---------------------------------------------------------------------------
# Cliente HTTP con control de límite
# ---------------------------------------------------------------------------
def _leer_entero(cabeceras, *nombres):
    if cabeceras is None:
        return None
    for nombre in nombres:
        try:
            return int(cabeceras.get(nombre))
        except (TypeError, ValueError):
            continue
    return None


class Cliente:
    def __init__(self, token, abrir=None, dormir=time.sleep, reloj=time.monotonic,
                 intervalo=INTERVALO_SEGUNDOS):
        self._token = token
        self._abrir = abrir or urllib.request.urlopen
        self._dormir = dormir
        self._reloj = reloj
        self._intervalo = intervalo
        self._ultima = None       # instante de la última petición
        self._restantes = None    # peticiones disponibles según la cabecera
        self._reinicio = None     # segundos hasta reiniciar el contador
        self.llamadas = 0

    def __repr__(self):
        return "Cliente(token=***)"

    def _sanear(self, texto) -> str:
        return str(texto).replace(self._token, "***")

    def _esperar_turno(self):
        if self._restantes is not None and self._restantes <= 0:
            espera = min((self._reinicio or 60) + 1, ESPERA_MAXIMA_SEGUNDOS)
            self._dormir(espera)
            self._restantes = None
            self._ultima = None
        if self._ultima is not None:
            falta = self._intervalo - (self._reloj() - self._ultima)
            if falta > 0:
                self._dormir(falta)

    def _registrar_cabeceras(self, cabeceras):
        self._restantes = _leer_entero(cabeceras, "X-RequestsAvailable",
                                       "X-Requests-Available-Minute")
        self._reinicio = _leer_entero(cabeceras, "X-RequestCounter-Reset")

    def _mensaje_api(self, error) -> str:
        try:
            cuerpo = json.loads(error.read().decode("utf-8"))
            texto = cuerpo.get("message") or cuerpo.get("error") or ""
            return ": " + self._sanear(texto)[:150] if texto else ""
        except Exception:
            return ""

    def _error_http(self, error):
        if error.code in (401, 403):
            return ErrorAcceso(f"acceso denegado (HTTP {error.code}): recurso no disponible "
                               "con tu token o plan")
        if error.code == 400:
            return ErrorAPI(f"petición rechazada (HTTP 400){self._mensaje_api(error)}")
        if error.code == 404:
            return ErrorAPI("recurso no encontrado (HTTP 404)")
        return ErrorAPI(f"el servidor respondió HTTP {error.code}")

    def obtener(self, ruta: str, params=None) -> dict:
        """GET a la API. Devuelve el JSON como dict."""
        url = URL_BASE + ruta
        if params:
            url += "?" + urllib.parse.urlencode(params)

        for intento in (1, 2):
            self._esperar_turno()
            peticion = urllib.request.Request(url, headers={
                "X-Auth-Token": self._token,
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            })
            self._ultima = self._reloj()
            self.llamadas += 1
            try:
                with self._abrir(peticion, timeout=TIMEOUT_SEGUNDOS) as respuesta:
                    self._registrar_cabeceras(respuesta.headers)
                    cuerpo = respuesta.read()
            except urllib.error.HTTPError as e:     # antes que URLError (es subclase)
                self._registrar_cabeceras(e.headers)
                if e.code == 429:
                    if intento == 1:
                        self._dormir(min((self._reinicio or 60) + 1, ESPERA_MAXIMA_SEGUNDOS))
                        self._restantes = None
                        self._ultima = None
                        continue
                    raise ErrorLimite("límite de peticiones excedido (HTTP 429); "
                                      "espera un minuto y repite") from None
                raise self._error_http(e) from None
            except urllib.error.URLError as e:
                raise ErrorAPI("error de conexión: " + self._sanear(e.reason)) from None
            except OSError as e:                    # timeouts, conexiones cortadas...
                raise ErrorAPI("error de red: " + self._sanear(e)) from None
            return self._decodificar(cuerpo)
        raise ErrorLimite("límite de peticiones excedido (HTTP 429)")  # inalcanzable

    @staticmethod
    def _decodificar(cuerpo: bytes) -> dict:
        if not cuerpo or not cuerpo.strip():
            raise ErrorAPI("respuesta vacía de la API")
        try:
            datos = json.loads(cuerpo.decode("utf-8"))
        except ValueError:
            raise ErrorAPI("la respuesta de la API no es JSON válido") from None
        if not isinstance(datos, dict):
            raise ErrorAPI("formato de respuesta inesperado")
        return datos


# ---------------------------------------------------------------------------
# Extracción y validación de partidos
# ---------------------------------------------------------------------------
def _goles(valor):
    if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0:
        return valor
    return None


def _par_goles(nodo):
    """Acepta {home, away} (API v4) y {homeTeam, awayTeam} (ejemplos de la documentación)."""
    if not isinstance(nodo, dict):
        return None
    for k_local, k_visit in (("home", "away"), ("homeTeam", "awayTeam")):
        if k_local in nodo or k_visit in nodo:
            return _goles(nodo.get(k_local)), _goles(nodo.get(k_visit))
    return None


def _nombre_equipo(equipo) -> str:
    if not isinstance(equipo, dict):
        return ""
    return str(equipo.get("name") or equipo.get("shortName") or "").strip()


def _fecha_iso(texto):
    if not isinstance(texto, str):
        return None
    try:
        return date.fromisoformat(texto[:10]).isoformat()   # fecha UTC
    except ValueError:
        return None


def extraer_partido(partido):
    """
    Devuelve (registro, None) si el partido es válido, o (None, motivo) si se descarta.
    Solo status FINISHED; marcador a los 90 minutos (ver cabecera del módulo).
    """
    if not isinstance(partido, dict):
        return None, "formato"
    if partido.get("status") != "FINISHED":
        return None, "no_finalizado"

    score = partido.get("score")
    if not isinstance(score, dict):
        return None, "sin_marcador"
    duracion = score.get("duration") or "REGULAR"
    if duracion == "REGULAR":
        par = _par_goles(score.get("fullTime"))
    elif duracion in ("EXTRA_TIME", "PENALTY_SHOOTOUT"):
        par = _par_goles(score.get("regularTime"))   # fullTime incluiría prórroga/penaltis
    else:
        return None, "duracion_desconocida"
    if par is None or par[0] is None or par[1] is None:
        return None, "sin_marcador"

    local = _nombre_equipo(partido.get("homeTeam"))
    visitante = _nombre_equipo(partido.get("awayTeam"))
    if not local or not visitante:
        return None, "sin_equipos"
    fecha = _fecha_iso(partido.get("utcDate"))
    if fecha is None:
        return None, "sin_fecha"
    api_id = partido.get("id")
    if not isinstance(api_id, int) or isinstance(api_id, bool):
        return None, "sin_id"

    return {"api_id": api_id, "fecha": fecha, "local": local, "visitante": visitante,
            "goles_local": par[0], "goles_visitante": par[1]}, None


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def preparar_bd(conn: sqlite3.Connection) -> sqlite3.Connection:
    """
    Verifica que `partidos` tenga las columnas esperadas (sin modificarla nunca)
    y crea, si faltan, las dos tablas propias de esta fuente.
    """
    columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(partidos)")}
    if not columnas:
        raise ErrorEsquema("no existe la tabla 'partidos'. Ejecuta primero el importador "
                           "de CSV: python3 datos_futbol.py importar")
    faltan = [c for c in COLUMNAS_PARTIDOS if c not in columnas]
    if faltan:
        raise ErrorEsquema("la tabla 'partidos' no tiene las columnas esperadas: "
                           + ", ".join(faltan))
    conn.execute(SQL_FUENTE)
    conn.execute(SQL_REGISTRO)
    conn.commit()
    return conn


def abrir_bd(ruta=RUTA_BD) -> sqlite3.Connection:
    if not Path(ruta).exists():
        raise ErrorEsquema(f"no existe la base de datos {ruta}. "
                           "Ejecuta primero: python3 datos_futbol.py importar")
    conn = sqlite3.connect(str(ruta))
    try:
        return preparar_bd(conn)
    except Exception:
        conn.close()
        raise


def _etiqueta_temporada(anio: int) -> str:
    return f"{anio}/{(anio + 1) % 100:02d}"


def importar_temporada(conn, cliente, codigo: str, anio: int) -> dict:
    """Descarga y guarda los partidos finalizados de una competición y temporada."""
    nombre = COMPETICIONES[codigo]
    etiqueta = _etiqueta_temporada(anio)
    datos = cliente.obtener(f"/competitions/{codigo}/matches",
                            {"season": anio, "status": "FINISHED"})
    partidos = datos.get("matches")
    if not isinstance(partidos, list):
        raise ErrorAPI("la respuesta no contiene la lista 'matches'")

    resumen = {"recibidos": len(partidos), "insertados": 0, "duplicados": 0,
               "descartados": 0, "motivos": Counter()}

    with conn:   # transacción: todo o nada para esta temporada
        for partido in partidos:
            registro, motivo = extraer_partido(partido)
            if registro is None:
                resumen["descartados"] += 1
                resumen["motivos"][motivo] += 1
                continue

            if conn.execute("SELECT 1 FROM fuente_api_partidos WHERE api_id = ?",
                            (registro["api_id"],)).fetchone():
                resumen["duplicados"] += 1      # ya importado antes desde la API
                continue

            clave = (nombre, etiqueta, registro["fecha"], registro["local"], registro["visitante"])
            cur = conn.execute(
                "INSERT OR IGNORE INTO partidos (competicion, temporada, fecha, equipo_local, "
                "equipo_visitante, goles_local, goles_visitante) VALUES (?, ?, ?, ?, ?, ?, ?)",
                clave + (registro["goles_local"], registro["goles_visitante"]))
            if cur.rowcount == 1:
                partido_id = cur.lastrowid
                resumen["insertados"] += 1
            else:                                # ya existía la misma fila (otra fuente)
                resumen["duplicados"] += 1
                partido_id = conn.execute(
                    "SELECT id FROM partidos WHERE competicion = ? AND temporada = ? AND "
                    "fecha = ? AND equipo_local = ? AND equipo_visitante = ?", clave).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO fuente_api_partidos (api_id, partido_id) "
                         "VALUES (?, ?)", (registro["api_id"], partido_id))
    return resumen


def registrar(conn, codigo, anio, estado, resumen=None, mensaje=""):
    r = resumen or {}
    with conn:
        conn.execute(
            "INSERT INTO importaciones_api (fecha_utc, codigo, competicion, temporada, estado, "
            "recibidos, insertados, duplicados, descartados, mensaje) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), codigo,
             COMPETICIONES[codigo], _etiqueta_temporada(anio), estado,
             r.get("recibidos", 0), r.get("insertados", 0), r.get("duplicados", 0),
             r.get("descartados", 0), mensaje))


def _texto_motivos(motivos) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(motivos.items()))


def actualizar(conn, cliente, codigos, anios, salida=print) -> int:
    """Importa cada competición/temporada. Devuelve el número de fallos."""
    fallos = 0
    for codigo in codigos:
        for anio in anios:
            etiqueta = f"[{COMPETICIONES[codigo]} {_etiqueta_temporada(anio)}]"
            try:
                resumen = importar_temporada(conn, cliente, codigo, anio)
            except ErrorLimite as e:
                fallos += 1
                registrar(conn, codigo, anio, "error", mensaje=str(e))
                salida(f"{etiqueta} ERROR: {e}\nSe detiene la actualización para respetar el límite.")
                return fallos
            except ErrorAcceso as e:
                fallos += 1
                registrar(conn, codigo, anio, "restringido", mensaje=str(e))
                salida(f"{etiqueta} RESTRINGIDO: {e}")
            except ErrorAPI as e:
                fallos += 1
                registrar(conn, codigo, anio, "error", mensaje=str(e))
                salida(f"{etiqueta} ERROR: {e}")
            else:
                motivos = _texto_motivos(resumen["motivos"])
                registrar(conn, codigo, anio, "ok", resumen, motivos)
                extra = f" ({motivos})" if motivos else ""
                salida(f"{etiqueta} {resumen['recibidos']} recibidos, {resumen['insertados']} nuevos, "
                       f"{resumen['duplicados']} ya existían, {resumen['descartados']} descartados{extra}")
    return fallos


# ---------------------------------------------------------------------------
# Comprobación de acceso (no escribe en la base de datos)
# ---------------------------------------------------------------------------
def comprobar(cliente, codigos, anio: int, salida=print) -> dict:
    """Prueba qué competiciones permite el token. Devuelve {código: 'accesible'|'restringida'|'error'}."""
    resultados = {}
    salida(f"Temporada {_etiqueta_temporada(anio)} · una petición por competición "
           f"(≈{INTERVALO_SEGUNDOS:g} s entre ellas)")
    for codigo in codigos:
        nombre = COMPETICIONES[codigo]
        try:
            datos = cliente.obtener(f"/competitions/{codigo}/matches",
                                    {"season": anio, "status": "FINISHED"})
            partidos = datos.get("matches")
            if not isinstance(partidos, list):
                raise ErrorAPI("la respuesta no contiene la lista 'matches'")
        except ErrorAcceso:
            resultados[codigo] = "restringida"
            salida(f"  {codigo:<4} {nombre:<18} RESTRINGIDA con tu token")
        except ErrorAPI as e:
            resultados[codigo] = "error"
            salida(f"  {codigo:<4} {nombre:<18} ERROR: {e}")
            if isinstance(e, ErrorLimite):
                salida("  Se detiene la comprobación para respetar el límite.")
                break
        else:
            validos = sum(1 for p in partidos if extraer_partido(p)[0] is not None)
            resultados[codigo] = "accesible"
            salida(f"  {codigo:<4} {nombre:<18} ACCESIBLE: {len(partidos)} finalizados, "
                   f"{validos} importables")
    return resultados


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------
def mostrar_estado(conn, token_configurado: bool, salida=print):
    total = conn.execute("SELECT COUNT(*) FROM partidos").fetchone()[0]
    de_api = conn.execute("SELECT COUNT(*) FROM fuente_api_partidos").fetchone()[0]
    salida(f"Token en {VARIABLE_TOKEN}: {'configurado' if token_configurado else 'NO configurado'}")
    salida(f"Partidos en la base de datos: {total} (de ellos, vinculados a la API: {de_api})")

    filas = conn.execute(
        "SELECT p.competicion, p.temporada, COUNT(*) FROM fuente_api_partidos f "
        "JOIN partidos p ON p.id = f.partido_id "
        "GROUP BY p.competicion, p.temporada ORDER BY p.competicion, p.temporada").fetchall()
    if filas:
        salida("\nPartidos de la API por competición / temporada")
        for competicion, temporada, n in filas:
            salida(f"  {competicion:<18} {temporada:<8} {n:>5}")

    ultimas = conn.execute(
        "SELECT fecha_utc, competicion, temporada, estado, recibidos, insertados, duplicados, "
        "descartados, mensaje FROM importaciones_api WHERE id IN "
        "(SELECT MAX(id) FROM importaciones_api GROUP BY codigo, temporada) "
        "ORDER BY competicion, temporada").fetchall()
    if ultimas:
        salida("\nÚltima ejecución por competición / temporada")
        for f, comp, temp, estado, rec, ins, dup, des, msg in ultimas:
            extra = f" · {msg}" if msg else ""
            salida(f"  {f} {comp} {temp}: {estado.upper()} — {rec} recibidos, {ins} nuevos, "
                   f"{dup} ya existían, {des} descartados{extra}")
    else:
        salida("\nAún no se ha ejecutado ninguna importación desde la API.")


# ---------------------------------------------------------------------------
# Línea de comandos
# ---------------------------------------------------------------------------
def temporada_actual(hoy: date = None) -> int:
    hoy = hoy or date.today()
    return hoy.year if hoy.month >= 7 else hoy.year - 1


def codigo_valido(texto: str) -> str:
    codigo = texto.strip().upper()
    if codigo in SOLAPADAS:
        raise argparse.ArgumentTypeError(
            f"{codigo} ({SOLAPADAS[codigo]}) ya se importa desde Football-Data.co.uk con otros "
            "nombres de equipo; se bloquea para evitar partidos duplicados")
    if codigo not in COMPETICIONES:
        raise argparse.ArgumentTypeError(
            f"competición '{texto}' no soportada. Disponibles: " + ", ".join(COMPETICIONES))
    return codigo


def anio_valido(texto: str) -> int:
    try:
        anio = int(texto)
    except ValueError:
        anio = None
    if anio is None or not (1993 <= anio <= date.today().year + 1):
        raise argparse.ArgumentTypeError(
            f"temporada no válida '{texto}': usa el año de inicio, p. ej. 2025 para 2025/26")
    return anio


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Importa partidos finalizados desde football-data.org API v4.")
    sub = parser.add_subparsers(dest="comando", required=True)
    ayuda_comp = "códigos: " + ", ".join(f"{c}={n}" for c, n in COMPETICIONES.items())

    p_com = sub.add_parser("comprobar", help="prueba qué competiciones permite tu token (no guarda nada)")
    p_com.add_argument("--competiciones", nargs="+", type=codigo_valido,
                       default=list(COMPETICIONES), help=ayuda_comp)
    p_com.add_argument("--temporada", type=anio_valido, default=None,
                       help="año de inicio, p. ej. 2025 (por defecto, la temporada en curso)")

    p_act = sub.add_parser("actualizar", help="descarga y guarda partidos finalizados")
    p_act.add_argument("--competiciones", nargs="+", type=codigo_valido, default=["CL"],
                       help=ayuda_comp + " (por defecto, CL)")
    p_act.add_argument("--temporadas", nargs="+", type=anio_valido, default=None,
                       help="años de inicio (por defecto, las 3 últimas)")

    sub.add_parser("estado", help="muestra el estado de la importación desde la API")
    return parser


def main(argv=None, entorno=None) -> int:
    args = crear_parser().parse_args(argv)
    actual = temporada_actual()

    try:
        if args.comando == "estado":
            conn = abrir_bd()
            try:
                try:
                    leer_token(entorno)
                    configurado = True
                except ErrorToken:
                    configurado = False
                mostrar_estado(conn, configurado)
            finally:
                conn.close()
            return 0

        cliente = Cliente(leer_token(entorno))

        if args.comando == "comprobar":
            resultados = comprobar(cliente, args.competiciones, args.temporada or actual)
            return 0 if "error" not in resultados.values() else 1

        if args.comando == "actualizar":
            anios = args.temporadas or [actual - 2, actual - 1, actual]
            conn = abrir_bd()
            try:
                fallos = actualizar(conn, cliente, args.competiciones, anios)
            finally:
                conn.close()
            return 1 if fallos else 0

    except ErrorToken as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except ErrorEsquema as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except ErrorAPI as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())