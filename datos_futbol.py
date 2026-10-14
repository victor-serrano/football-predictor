"""
Módulo de datos: importa resultados históricos de Football-Data.co.uk a SQLite.

Solo biblioteca estándar. Sin APIs de pago ni claves.

Uso (desde la carpeta del proyecto):
    python3 datos_futbol.py importar
    python3 datos_futbol.py importar --competiciones SP1 E0 --temporadas 2425 2526
    python3 datos_futbol.py contar

Notas sobre los datos:
  * Football-Data.co.uk es gratuito para uso privado. Este módulo hace pocas
    peticiones y deja una pausa entre descargas.
  * Solo se guardan partidos con resultado final (goles local y visitante).
    Los partidos sin resultado NO se guardan.
  * Football-Data.co.uk no ofrece Champions League, por eso no está incluida.
"""

import argparse
import csv
import io
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
RUTA_BD = Path(__file__).resolve().parent / "data" / "partidos.db"

# Patrón de URL de los CSV por temporada. Si el servidor cambiara, solo hay
# que tocar esta línea.
URL_CSV = "https://www.football-data.co.uk/mmz4281/{temporada}/{codigo}.csv"

USER_AGENT = "football-predictor-personal/0.1 (uso privado)"
PAUSA_SEGUNDOS = 1.0      # pausa entre descargas, por cortesía con el servidor
TIMEOUT_SEGUNDOS = 30

# Código de Football-Data.co.uk -> nombre de la competición
COMPETICIONES = {
    "SP1": "La Liga",
    "SP2": "Segunda División",
    "E0": "Premier League",
    "D1": "Bundesliga",
}

COLUMNAS_OBLIGATORIAS = ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG")

SQL_TABLA = """
CREATE TABLE IF NOT EXISTS partidos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    competicion      TEXT    NOT NULL,
    temporada        TEXT    NOT NULL,
    fecha            TEXT    NOT NULL,   -- formato ISO: AAAA-MM-DD
    equipo_local     TEXT    NOT NULL,
    equipo_visitante TEXT    NOT NULL,
    goles_local      INTEGER NOT NULL CHECK (goles_local >= 0),
    goles_visitante  INTEGER NOT NULL CHECK (goles_visitante >= 0),
    UNIQUE (competicion, temporada, fecha, equipo_local, equipo_visitante)
)
"""


class ErrorDescarga(Exception):
    """Fallo de red o respuesta HTTP no válida."""


class ErrorDatos(Exception):
    """Archivo vacío, sin columnas obligatorias o sin partidos."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def etiqueta_temporada(codigo: str) -> str:
    """'2425' -> '2024/25'. Lanza ValueError si el código no es válido."""
    if len(codigo) != 4 or not codigo.isdigit():
        raise ValueError(f"temporada no válida '{codigo}': usa el formato AABB, p. ej. 2425")
    a, b = int(codigo[:2]), int(codigo[2:])
    if b != (a + 1) % 100:
        raise ValueError(f"temporada no válida '{codigo}': los años deben ser consecutivos (2425, 2526...)")
    anio = 1900 + a if a >= 90 else 2000 + a
    return f"{anio}/{codigo[2:]}"


def temporadas_por_defecto(hoy: date = None, cuantas: int = 4) -> list:
    """Las últimas `cuantas` temporadas, incluida la actual (empieza en julio)."""
    hoy = hoy or date.today()
    inicio = hoy.year if hoy.month >= 7 else hoy.year - 1
    return [f"{a % 100:02d}{(a + 1) % 100:02d}" for a in range(inicio - cuantas + 1, inicio + 1)]


def parsear_fecha(texto: str):
    """Acepta dd/mm/aaaa y dd/mm/aa. Devuelve 'AAAA-MM-DD' o None."""
    for formato in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            continue
    return None


def decodificar(datos: bytes) -> str:
    try:
        return datos.decode("utf-8-sig")
    except UnicodeDecodeError:
        return datos.decode("latin-1")


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def conectar(ruta=RUTA_BD) -> sqlite3.Connection:
    if str(ruta) != ":memory:":
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ruta))
    conn.execute(SQL_TABLA)
    conn.commit()
    return conn


def importar_texto(conn: sqlite3.Connection, texto: str, competicion: str, temporada: str) -> dict:
    """
    Importa el contenido de un CSV. Devuelve un resumen con contadores.
    Lanza ErrorDatos si el archivo está vacío, le faltan columnas o no tiene partidos.
    La importación de cada archivo es atómica: si falla, no se guarda nada de él.
    """
    if not texto.strip():
        raise ErrorDatos("el archivo está vacío")

    lector = csv.DictReader(io.StringIO(texto))
    if not lector.fieldnames:
        raise ErrorDatos("el archivo no tiene cabecera")
    lector.fieldnames = [c.strip() for c in lector.fieldnames]

    faltan = [c for c in COLUMNAS_OBLIGATORIAS if c not in lector.fieldnames]
    if faltan:
        raise ErrorDatos("faltan columnas obligatorias: " + ", ".join(faltan))

    etiqueta = etiqueta_temporada(temporada)
    resumen = {"insertados": 0, "duplicados": 0, "sin_resultado": 0, "invalidos": 0}
    filas = 0

    with conn:  # transacción: commit si todo va bien, rollback si hay excepción
        for fila in lector:
            valores = {k: (v or "").strip() for k, v in fila.items() if k is not None}
            if not any(valores.values()):
                continue  # línea en blanco
            filas += 1

            goles_l, goles_v = valores.get("FTHG", ""), valores.get("FTAG", "")
            if not goles_l and not goles_v:
                resumen["sin_resultado"] += 1   # partido sin resultado: no se guarda
                continue

            fecha = parsear_fecha(valores.get("Date", ""))
            local = valores.get("HomeTeam", "")
            visitante = valores.get("AwayTeam", "")
            try:
                gl, gv = int(goles_l), int(goles_v)
            except ValueError:
                resumen["invalidos"] += 1
                continue
            if fecha is None or not local or not visitante or gl < 0 or gv < 0:
                resumen["invalidos"] += 1
                continue

            cur = conn.execute(
                "INSERT OR IGNORE INTO partidos "
                "(competicion, temporada, fecha, equipo_local, equipo_visitante, "
                " goles_local, goles_visitante) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (competicion, etiqueta, fecha, local, visitante, gl, gv),
            )
            resumen["insertados" if cur.rowcount == 1 else "duplicados"] += 1

    if filas == 0:
        raise ErrorDatos("el archivo no contiene partidos")
    return resumen


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------
def descargar(url: str, timeout: int = TIMEOUT_SEGUNDOS) -> bytes:
    peticion = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return respuesta.read()
    except urllib.error.HTTPError as e:      # va antes que URLError (es subclase)
        if e.code == 404:
            raise ErrorDescarga("no disponible en el servidor (HTTP 404)") from e
        raise ErrorDescarga(f"el servidor respondió HTTP {e.code}") from e
    except urllib.error.URLError as e:
        motivo = str(e.reason)
        if "CERTIFICATE_VERIFY_FAILED" in motivo:
            motivo += (" — en macOS con Python de python.org, ejecuta "
                       "'Install Certificates.command' de la carpeta de Python")
        raise ErrorDescarga(f"error de conexión: {motivo}") from e
    except OSError as e:                     # timeouts, conexiones cortadas...
        raise ErrorDescarga(f"error de red: {e}") from e


def importar(conn, codigos, temporadas, pausa=PAUSA_SEGUNDOS, descargar_fn=descargar, salida=print):
    """Descarga e importa cada combinación competición/temporada. Devuelve (ok, fallos)."""
    ok, fallos = 0, 0
    totales = {"insertados": 0, "duplicados": 0, "sin_resultado": 0, "invalidos": 0}
    combinaciones = [(c, t) for c in codigos for t in temporadas]

    for i, (codigo, temporada) in enumerate(combinaciones):
        nombre = COMPETICIONES[codigo]
        etiqueta = etiqueta_temporada(temporada)
        url = URL_CSV.format(temporada=temporada, codigo=codigo)
        try:
            datos = descargar_fn(url)
            if not datos or not datos.strip():
                raise ErrorDatos("el archivo descargado está vacío")
            resumen = importar_texto(conn, decodificar(datos), nombre, temporada)
        except (ErrorDescarga, ErrorDatos) as e:
            fallos += 1
            salida(f"[{nombre} {etiqueta}] ERROR: {e}")
        else:
            ok += 1
            for clave in totales:
                totales[clave] += resumen[clave]
            salida(f"[{nombre} {etiqueta}] {resumen['insertados']} nuevos, "
                   f"{resumen['duplicados']} ya existían, "
                   f"{resumen['sin_resultado']} sin resultado (no guardados), "
                   f"{resumen['invalidos']} filas inválidas (no guardadas)")
        if pausa and i < len(combinaciones) - 1:
            time.sleep(pausa)

    salida(f"\nResumen: {ok} archivos correctos, {fallos} con error. "
           f"Nuevos: {totales['insertados']}, ya existían: {totales['duplicados']}, "
           f"sin resultado: {totales['sin_resultado']}, inválidos: {totales['invalidos']}.")
    return ok, fallos


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
def contar(conn: sqlite3.Connection, salida=print) -> int:
    total = conn.execute("SELECT COUNT(*) FROM partidos").fetchone()[0]
    salida(f"Partidos guardados: {total}")
    if total:
        salida("\nCompetición / temporada / partidos")
        filas = conn.execute(
            "SELECT competicion, temporada, COUNT(*) FROM partidos "
            "GROUP BY competicion, temporada ORDER BY competicion, temporada"
        ).fetchall()
        for competicion, temporada, n in filas:
            salida(f"  {competicion:<18} {temporada:<8} {n:>5}")
    return total


# ---------------------------------------------------------------------------
# Línea de comandos
# ---------------------------------------------------------------------------
def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Importa y consulta resultados históricos de Football-Data.co.uk.")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_imp = sub.add_parser("importar", help="descarga CSV y los guarda en SQLite")
    p_imp.add_argument("--competiciones", nargs="+", choices=sorted(COMPETICIONES),
                       default=list(COMPETICIONES),
                       help="códigos: " + ", ".join(f"{c}={n}" for c, n in COMPETICIONES.items()))
    p_imp.add_argument("--temporadas", nargs="+", default=None,
                       help="formato AABB, p. ej. 2425 (por defecto, las 4 últimas)")

    sub.add_parser("contar", help="muestra cuántos partidos hay guardados")
    return parser


def main(argv=None) -> int:
    parser = crear_parser()
    args = parser.parse_args(argv)

    if args.comando == "importar":
        temporadas = args.temporadas or temporadas_por_defecto()
        for t in temporadas:
            try:
                etiqueta_temporada(t)
            except ValueError as e:
                parser.error(str(e))
        conn = conectar()
        try:
            _, fallos = importar(conn, args.competiciones, temporadas)
        finally:
            conn.close()
        return 1 if fallos else 0

    if args.comando == "contar":
        if not RUTA_BD.exists():
            print("Todavía no hay base de datos. Ejecuta primero: python3 datos_futbol.py importar")
            return 0
        conn = conectar()
        try:
            contar(conn)
        finally:
            conn.close()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())