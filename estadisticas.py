"""
Módulo de estadísticas de equipos para football-predictor.

Calcula estadísticas reales a partir de los partidos finalizados almacenados en SQLite:
- Goles marcados y recibidos por partido (general, local y visitante).
- Balance de victorias, empates y derrotas.
- Resultados de los últimos 5 y 10 partidos ordenados cronológicamente.
- Filtro por competición y fecha de corte (excluyendo encuentros futuros).
- Conteo explícito de muestras para evaluar fiabilidad.
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from equipos import obtener_variantes_equipo

RUTA_BD_DEFECTO = Path(__file__).resolve().parent / "data" / "partidos.db"


def conectar_bd(ruta_bd: Path = RUTA_BD_DEFECTO) -> sqlite3.Connection:
    """Conecta a la base de datos SQLite."""
    conn = sqlite3.connect(ruta_bd)
    conn.row_factory = sqlite3.Row
    return conn


def obtener_estadisticas_equipo(
    conn: sqlite3.Connection,
    equipo: str,
    competicion: Optional[str] = None,
    fecha_corte: Optional[str] = None,
    limite_forma_reciente: int = 10,
) -> dict[str, Any]:
    """
    Calcula las estadísticas completas de un equipo unificando sus variantes de nombre.

    :param conn: Conexión SQLite activa.
    :param equipo: Nombre canónico o variante del equipo.
    :param competicion: Opcional. Filtra por competición.
    :param fecha_corte: Opcional. Fecha YYYY-MM-DD. Excluye partidos con fecha > fecha_corte.
    :param limite_forma_reciente: Número máximo de partidos para el desglose reciente (por defecto 10).
    :return: Diccionario con estadísticas, promedios y desglose de forma reciente.
    """
    variantes = obtener_variantes_equipo(equipo)
    placeholders = ",".join(["?"] * len(variantes))

    query = f"""
        SELECT id, competicion, temporada, fecha, equipo_local, equipo_visitante,
               goles_local, goles_visitante
        FROM partidos
        WHERE (equipo_local IN ({placeholders}) OR equipo_visitante IN ({placeholders}))
          AND goles_local IS NOT NULL 
          AND goles_visitante IS NOT NULL
    """
    params: list[Any] = variantes + variantes

    if competicion:
        query += " AND competicion = ?"
        params.append(competicion)

    if fecha_corte:
        query += " AND fecha <= ?"
        params.append(fecha_corte)

    query += " ORDER BY fecha ASC, id ASC"

    cursor = conn.cursor()
    filas = cursor.execute(query, params).fetchall()

    totales = {"partidos": 0, "victorias": 0, "empates": 0, "derrotas": 0, "gf": 0, "gc": 0}
    local = {"partidos": 0, "victorias": 0, "empates": 0, "derrotas": 0, "gf": 0, "gc": 0}
    visitante = {"partidos": 0, "victorias": 0, "empates": 0, "derrotas": 0, "gf": 0, "gc": 0}

    partidos_procesados = []

    for f in filas:
        # Se verifica si es local comparando contra todas las variantes
        es_local = (f["equipo_local"] in variantes)
        gl = f["goles_local"]
        gv = f["goles_visitante"]

        gf = gl if es_local else gv
        gc = gv if es_local else gl

        if gf > gc:
            res = "victoria"
        elif gf < gc:
            res = "derrota"
        else:
            res = "empate"

        # Totales
        totales["partidos"] += 1
        totales["gf"] += gf
        totales["gc"] += gc
        totales[f"{res}s" if res != "victoria" else "victorias"] += 1

        # Condición (Local o Visitante)
        target = local if es_local else visitante
        target["partidos"] += 1
        target["gf"] += gf
        target["gc"] += gc
        target[f"{res}s" if res != "victoria" else "victorias"] += 1

        partidos_procesados.append({
            "id": f["id"],
            "fecha": f["fecha"],
            "competicion": f["competicion"],
            "temporada": f["temporada"],
            "condicion": "local" if es_local else "visitante",
            "rival": f["equipo_visitante"] if es_local else f["equipo_local"],
            "goles_favor": gf,
            "goles_contra": gc,
            "resultado": res
        })

    def calc_promedios(d):
        pts = d["partidos"]
        return {
            **d,
            "promedio_gf": round(d["gf"] / pts, 2) if pts > 0 else 0.0,
            "promedio_gc": round(d["gc"] / pts, 2) if pts > 0 else 0.0,
        }

    # Desglose reciente (forma)
    ultimos = partidos_procesados[-limite_forma_reciente:]
    ultimos_5 = partidos_procesados[-5:]

    return {
        "equipo": equipo,
        "competicion_filtrada": competicion or "Todas",
        "fecha_corte": fecha_corte or "Ninguna (hasta hoy)",
        "total": calc_promedios(totales),
        "local": calc_promedios(local),
        "visitante": calc_promedios(visitante),
        "ultimos_5": ultimos_5,
        "ultimos_10": ultimos,
        "racha_ultimos_5": "".join([p["resultado"][0].upper() for p in ultimos_5]),
        "racha_ultimos_10": "".join([p["resultado"][0].upper() for p in ultimos]),
    }


def imprimir_informe_estadisticas(stats: dict[str, Any]) -> None:
    """Imprime por consola un informe claro de las estadísticas obtenidas."""
    eq = stats["equipo"]
    tot = stats["total"]
    loc = stats["local"]
    vis = stats["visitante"]

    print(f"\n==================================================")
    print(f" ESTADÍSTICAS: {eq}")
    print(f" Competición: {stats['competicion_filtrada']} | Fecha de corte: {stats['fecha_corte']}")
    print(f"==================================================")

    if tot["partidos"] == 0:
        print(" [!] No se encontraron partidos finalizados para los criterios especificados.")
        print("==================================================\n")
        return

    print(f" TOTAL ({tot['partidos']} partidos jugados):")
    print(f"   Balance : {tot['victorias']}V - {tot['empates']}E - {tot['derrotas']}D")
    print(f"   Goles   : {tot['gf']} a favor ({tot['promedio_gf']}/partido) | {tot['gc']} en contra ({tot['promedio_gc']}/partido)")

    print(f"\n LOCAL ({loc['partidos']} partidos):")
    print(f"   Balance : {loc['victorias']}V - {loc['empates']}E - {loc['derrotas']}D")
    print(f"   Goles   : {loc['gf']} GF ({loc['promedio_gf']}/partido) | {loc['gc']} GC ({loc['promedio_gc']}/partido)")

    print(f"\n VISITANTE ({vis['partidos']} partidos):")
    print(f"   Balance : {vis['victorias']}V - {vis['empates']}E - {vis['derrotas']}D")
    print(f"   Goles   : {vis['gf']} GF ({vis['promedio_gf']}/partido) | {vis['gc']} GC ({vis['promedio_gc']}/partido)")

    print(f"\n FORMA RECIENTE:")
    print(f"   Últimos 5 partidos  [{stats['racha_ultimos_5']}]:")
    for p in stats["ultimos_5"]:
        print(f"     - {p['fecha']} ({p['competicion']}): vs {p['rival']} ({p['condicion'][0].upper()}) -> {p['goles_favor']}-{p['goles_contra']} ({p['resultado']})")

    if len(stats["ultimos_10"]) > 5:
        print(f"   Racha últimos 10   [{stats['racha_ultimos_10']}]")
    print("==================================================\n")


def main():
    parser = argparse.ArgumentParser(description="Consulta de estadísticas de equipos en football-predictor.")
    parser.add_argument("equipo", type=str, help="Nombre del equipo (ej. 'Real Madrid', 'Barcelona')")
    parser.add_argument("--competicion", type=str, default=None, help="Filtrar por competición (ej. 'La Liga')")
    parser.add_argument("--fecha-corte", type=str, default=None, help="Fecha límite YYYY-MM-DD")
    args = parser.parse_args()

    conn = conectar_bd()
    try:
        stats = obtener_estadisticas_equipo(conn, args.equipo, args.competicion, args.fecha_corte)
        imprimir_informe_estadisticas(stats)
    finally:
        conn.close()


if __name__ == "__main__":
    main()