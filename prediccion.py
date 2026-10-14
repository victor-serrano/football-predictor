"""
Módulo de predicción: conecta las estadísticas reales de SQLite con el modelo Poisson.

Estima las medias esperadas (lambda_local y lambda_visitante) basándose en:
- Fuerza ofensiva y defensiva de los equipos (local / visitante).
- Promedios de la competición o globales.
- Filtro estricto por fecha de corte para evitar fuga de información.

Genera probabilidades para mercados 1X2, Over/Under 2.5 y Ambos Marcan (BTTS).
"""

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import main as motor_poisson
from equipos import obtener_variantes_equipo

RUTA_BD_DEFECTO = Path(__file__).resolve().parent / "data" / "partidos.db"


def conectar_bd(ruta_bd: Path = RUTA_BD_DEFECTO) -> sqlite3.Connection:
    conn = sqlite3.connect(ruta_bd)
    conn.row_factory = sqlite3.Row
    return conn


def obtener_promedios_competicion(
    conn: sqlite3.Connection,
    competicion: Optional[str] = None,
    fecha_corte: Optional[str] = None
) -> dict[str, float]:
    """Calcula la media de goles locales, visitantes y totales en una competición antes de fecha_corte."""
    query = """
        SELECT COUNT(*) as partidos,
               COALESCE(SUM(goles_local), 0) as gf_local,
               COALESCE(SUM(goles_visitante), 0) as gf_vis
        FROM partidos
        WHERE goles_local IS NOT NULL AND goles_visitante IS NOT NULL
    """
    params: list[Any] = []

    if competicion:
        query += " AND competicion = ?"
        params.append(competicion)

    if fecha_corte:
        query += " AND fecha < ?"
        params.append(fecha_corte)

    row = conn.execute(query, params).fetchone()
    pts = row["partidos"]

    if pts == 0 and competicion:
        # Fallback a global si la competición no tiene partidos suficientes
        return obtener_promedios_competicion(conn, competicion=None, fecha_corte=fecha_corte)

    if pts == 0:
        return {"partidos": 0, "media_local": 1.4, "media_visitante": 1.1, "media_total": 2.5}

    m_loc = row["gf_local"] / pts
    m_vis = row["gf_vis"] / pts
    return {
        "partidos": pts,
        "media_local": round(m_loc, 4),
        "media_visitante": round(m_vis, 4),
        "media_total": round(m_loc + m_vis, 4)
    }


def obtener_stats_condicion(
    conn: sqlite3.Connection,
    equipo: str,
    es_local: bool,
    competicion: Optional[str] = None,
    fecha_corte: Optional[str] = None
) -> dict[str, Any]:
    """Obtiene el rendimiento específico de un equipo en condición local o visitante considerando sus variantes de nombre."""
    variantes = obtener_variantes_equipo(equipo)
    placeholders = ",".join(["?"] * len(variantes))

    col_equipo = "equipo_local" if es_local else "equipo_visitante"
    col_gf = "goles_local" if es_local else "goles_visitante"
    col_gc = "goles_visitante" if es_local else "goles_local"

    query = f"""
        SELECT COUNT(*) as partidos,
               COALESCE(SUM({col_gf}), 0) as gf,
               COALESCE(SUM({col_gc}), 0) as gc
        FROM partidos
        WHERE {col_equipo} IN ({placeholders})
          AND goles_local IS NOT NULL AND goles_visitante IS NOT NULL
    """
    params: list[Any] = list(variantes)

    if competicion:
        query += " AND competicion = ?"
        params.append(competicion)

    if fecha_corte:
        query += " AND fecha < ?"
        params.append(fecha_corte)

    row = conn.execute(query, params).fetchone()
    pts = row["partidos"]

    return {
        "partidos": pts,
        "gf": row["gf"],
        "gc": row["gc"],
        "prom_gf": (row["gf"] / pts) if pts > 0 else 0.0,
        "prom_gc": (row["gc"] / pts) if pts > 0 else 0.0,
    }


def calcular_lambdas(
    conn: sqlite3.Connection,
    equipo_local: str,
    equipo_visitante: str,
    competicion: Optional[str] = None,
    fecha_corte: Optional[str] = None,
    min_partidos: int = 3
) -> dict[str, Any]:
    """
    Calcula los parámetros lambda para el modelo Poisson.
    Retorna los valores lambda y los metadatos/advertencias del cálculo.
    """
    prom_comp = obtener_promedios_competicion(conn, competicion, fecha_corte)
    stats_loc = obtener_stats_condicion(conn, equipo_local, es_local=True, competicion=competicion, fecha_corte=fecha_corte)
    stats_vis = obtener_stats_condicion(conn, equipo_visitante, es_local=False, competicion=competicion, fecha_corte=fecha_corte)

    advertencias = []

    # FALLBACK AUTOMÁTICO: Si no hay partidos suficientes en la competición filtrada, recurre a datos globales
    if stats_loc["partidos"] < min_partidos and competicion is not None:
        stats_loc = obtener_stats_condicion(conn, equipo_local, es_local=True, competicion=None, fecha_corte=fecha_corte)
        advertencias.append(f"Datos globales para {equipo_local} (sin muestra suficiente en {competicion}).")

    if stats_vis["partidos"] < min_partidos and competicion is not None:
        stats_vis = obtener_stats_condicion(conn, equipo_visitante, es_local=False, competicion=None, fecha_corte=fecha_corte)
        advertencias.append(f"Datos globales para {equipo_visitante} (sin muestra suficiente en {competicion}).")

    # Advertencia si la muestra sigue siendo pequeña incluso a nivel global
    if stats_loc["partidos"] < min_partidos:
        advertencias.append(f"Muestra pequeña para {equipo_local} como local ({stats_loc['partidos']} partidos).")
    if stats_vis["partidos"] < min_partidos:
        advertencias.append(f"Muestra pequeña para {equipo_visitante} como visitante ({stats_vis['partidos']} partidos).")

    # Solo falla si el equipo no tiene absolutamente ningún partido registrado ni a nivel global
    if stats_loc["partidos"] == 0 or stats_vis["partidos"] == 0:
        return {
            "error": "Datos insuficientes para uno o ambos equipos.",
            "advertencias": advertencias,
            "stats_local": stats_loc,
            "stats_visitante": stats_vis
        }

    # Ataque Local vs Defensa Visitante
    ataque_loc = stats_loc["prom_gf"] / prom_comp["media_local"] if prom_comp["media_local"] > 0 else 1.0
    defensa_vis = stats_vis["prom_gc"] / prom_comp["media_local"] if prom_comp["media_local"] > 0 else 1.0
    lambda_loc = ataque_loc * defensa_vis * prom_comp["media_local"]

    # Ataque Visitante vs Defensa Local
    ataque_vis = stats_vis["prom_gf"] / prom_comp["media_visitante"] if prom_comp["media_visitante"] > 0 else 1.0
    defensa_loc = stats_loc["prom_gc"] / prom_comp["media_visitante"] if prom_comp["media_visitante"] > 0 else 1.0
    lambda_vis = ataque_vis * defensa_loc * prom_comp["media_visitante"]

    # Ajuste de límites razonables
    lambda_loc = max(0.2, min(5.0, round(lambda_loc, 3)))
    lambda_vis = max(0.2, min(5.0, round(lambda_vis, 3)))

    return {
        "lambda_local": lambda_loc,
        "lambda_visitante": lambda_vis,
        "promedios_competicion": prom_comp,
        "stats_local": stats_loc,
        "stats_visitante": stats_vis,
        "advertencias": advertencias
    }


def predecir_partido(
    conn: sqlite3.Connection,
    equipo_local: str,
    equipo_visitante: str,
    competicion: Optional[str] = None,
    fecha_corte: Optional[str] = None
) -> dict[str, Any]:
    """Genera la predicción matemática completa para un enfrentamiento."""
    res_lambdas = calcular_lambdas(conn, equipo_local, equipo_visitante, competicion, fecha_corte)

    if "error" in res_lambdas:
        return {"ok": False, "error": res_lambdas["error"], "advertencias": res_lambdas["advertencias"]}

    lam_loc = res_lambdas["lambda_local"]
    lam_vis = res_lambdas["lambda_visitante"]

    matriz = motor_poisson.matriz_marcadores(lam_loc, lam_vis)
    mercados_raw = motor_poisson.calcular_mercados(matriz)

    masa = mercados_raw.get("masa", 1.0)
    if masa <= 0:
        masa = 1.0

    mercados = {
        "local": mercados_raw["local"] / masa,
        "empate": mercados_raw["empate"] / masa,
        "visitante": mercados_raw["visitante"] / masa,
        "over25": mercados_raw["over25"] / masa,
        "under25": mercados_raw["under25"] / masa,
        "btts": mercados_raw["btts"] / masa,
    }

    # === CÁLCULO DEL TOP 5 DE MARCADORES EXACTOS ===
    lista_marcadores = []
    for gl in range(len(matriz)):
        for gv in range(len(matriz[gl])):
            prob_exacta = (matriz[gl][gv] / masa) * 100.0
            lista_marcadores.append({
                "marcador": f"{gl} - {gv}",
                "goles_local": gl,
                "goles_visitante": gv,
                "probabilidad_pct": round(prob_exacta, 1)
            })

    # Ordenar por probabilidad descendente y seleccionar los 5 principales
    lista_marcadores.sort(key=lambda x: x["probabilidad_pct"], reverse=True)
    top_marcadores = lista_marcadores[:5]

    return {
        "ok": True,
        "equipo_local": equipo_local,
        "equipo_visitante": equipo_visitante,
        "competicion": competicion or "Todas",
        "fecha_corte": fecha_corte or "Sin corte",
        "lambda_local": lam_loc,
        "lambda_visitante": lam_vis,
        "mercados": mercados,
        "top_marcadores": top_marcadores,
        "detalles": res_lambdas
    }

def imprimir_prediccion(pred: dict[str, Any]) -> None:
    if not pred["ok"]:
        print(f"\n[!] Error en la predicción: {pred['error']}")
        for adv in pred.get("advertencias", []):
            print(f"    - {adv}")
        return

    m = pred["mercados"]
    det = pred["detalles"]

    print(f"\n==================================================")
    print(f" PREDICCIÓN: {pred['equipo_local']} vs {pred['equipo_visitante']}")
    print(f" Competición: {pred['competicion']} | Fecha Corte: {pred['fecha_corte']}")
    print(f"==================================================")
    print(f" Medias de Goles Esperados (Lambda):")
    print(f"   {pred['equipo_local']} (Local)    : {pred['lambda_local']} goles")
    print(f"   {pred['equipo_visitante']} (Visitante): {pred['lambda_visitante']} goles")

    if det["advertencias"]:
        print(f"\n [!] Advertencias de muestra:")
        for adv in det["advertencias"]:
            print(f"     - {adv}")

    print(f"\n PROBABILIDADES CALCULADAS (Poisson):")
    print(f"   1X2:")
    print(f"     1 (Victoria Local) : {m['local']*100:.1f}%")
    print(f"     X (Empate)         : {m['empate']*100:.1f}%")
    print(f"     2 (Victoria Vis.)  : {m['visitante']*100:.1f}%")
    print(f"   Goles:")
    print(f"     +2.5 Goles         : {m['over25']*100:.1f}%")
    print(f"     -2.5 Goles         : {m['under25']*100:.1f}%")
    print(f"   Ambos Marcan:")
    print(f"     Sí (BTTS)          : {m['btts']*100:.1f}%")
    print(f"     No                 : {(1 - m['btts'])*100:.1f}%")
    print(f"==================================================\n")


def main():
    parser = argparse.ArgumentParser(description="Predicción matemática de partidos basada en Poisson y SQLite.")
    parser.add_argument("local", type=str, help="Equipo local")
    parser.add_argument("visitante", type=str, help="Equipo visitante")
    parser.add_argument("--competicion", type=str, default=None, help="Nombre de la competición")
    parser.add_argument("--fecha-corte", type=str, default=None, help="Fecha límite YYYY-MM-DD (excluye ese día y posteriores)")
    args = parser.parse_args()

    conn = conectar_bd()
    try:
        pred = predecir_partido(conn, args.local, args.visitante, args.competicion, args.fecha_corte)
        imprimir_prediccion(pred)
    finally:
        conn.close()


if __name__ == "__main__":
    main()