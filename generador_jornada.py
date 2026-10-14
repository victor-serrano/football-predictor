
"""
Módulo Generador de Apuestas de la Jornada y Combinadas.

Analiza los enfrentamientos registrados en la base de datos,
aplica el modelo Poisson y el recomendador, y devuelve las
selecciones que superan el umbral de probabilidad configurado.
"""

import sqlite3
from typing import Any, Dict

import prediccion
import recomendador


def buscar_mejores_apuestas(
    conn: sqlite3.Connection,
    competicion: str = None,
    limite: int = 5,
    min_probabilidad: float = 60.0,
) -> Dict[str, Any]:
    """
    Busca enfrentamientos y devuelve las mejores selecciones.

    Devuelve un diccionario con:
        ok
        total_encontrados
        probabilidad_combinada_pct
        cuota_teorica_combinada
        selecciones
    """
    query = """
        SELECT DISTINCT equipo_local, equipo_visitante, competicion
        FROM partidos
        WHERE goles_local IS NOT NULL
          AND goles_visitante IS NOT NULL
    """
    params = []

    if competicion and competicion != "Todas":
        query += " AND competicion = ?"
        params.append(competicion)

    query += " ORDER BY fecha DESC LIMIT 50"

    cursor = conn.cursor()
    filas = cursor.execute(query, params).fetchall()

    candidatos = []
    parejas_vistas = set()

    for row in filas:
        loc = row["equipo_local"]
        vis = row["equipo_visitante"]
        comp = row["competicion"]

        clave = (loc, vis, comp)

        if clave in parejas_vistas or loc == vis:
            continue

        parejas_vistas.add(clave)

        res = prediccion.predecir_partido(
            conn,
            loc,
            vis,
            competicion=comp,
        )

        if not res.get("ok"):
            continue

        rec = recomendador.generar_recomendacion(
            res["mercados"],
            res.get("detalles", {}).get("advertencias", []),
        )

        if rec["probabilidad_pct"] >= min_probabilidad:
            candidatos.append({
                "equipo_local": res["equipo_local"],
                "equipo_visitante": res["equipo_visitante"],
                "competicion": comp,
                "lambda_local": res["lambda_local"],
                "lambda_visitante": res["lambda_visitante"],
                "mercado_recomendado": rec["mercado_recomendado"],
                "probabilidad_pct": rec["probabilidad_pct"],
                "nivel_confianza": rec["nivel_confianza"],
                "color_codigo": rec["color_codigo"],
            })

    candidatos.sort(
        key=lambda item: item["probabilidad_pct"],
        reverse=True,
    )

    limite = max(1, limite)
    top_selecciones = candidatos[:limite]

    # Multiplicación teórica de probabilidades.
    # Supone independencia entre las selecciones.
    prob_combinada = 1.0

    for seleccion in top_selecciones:
        prob_combinada *= seleccion["probabilidad_pct"] / 100.0

    return {
        "ok": True,
        "total_encontrados": len(top_selecciones),
        "probabilidad_combinada_pct": (
            round(prob_combinada * 100.0, 1)
            if top_selecciones else 0.0
        ),
        "cuota_teorica_combinada": (
            round(1.0 / prob_combinada, 2)
            if prob_combinada > 0 and top_selecciones else 0.0
        ),
        "selecciones": top_selecciones,
    }
