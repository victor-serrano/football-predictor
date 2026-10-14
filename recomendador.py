"""
Módulo de Recomendación Estadística de Apuestas.

Analiza los mercados calculados por el motor Poisson y selecciona la mejor opción
de acuerdo a umbrales rigurosos de probabilidad y filtros de riesgo.
"""

from typing import Any, Dict


def generar_recomendacion(
    mercados: dict[str, float],
    advertencias: list[str] = None
) -> dict[str, Any]:
    """
    Evalúa los mercados (1X2, Over/Under 2.5, BTTS y Doble Oportunidad)
    y selecciona el pronóstico con mayor probabilidad matemática.

    :param mercados: Diccionario con las probabilidades normalizadas (0.0 a 1.0).
    :param advertencias: Lista de advertencias (ej. muestra pequeña o fallback global).
    :return: Diccionario con la recomendación destacada y su nivel de confianza.
    """
    advertencias = advertencias or []

    # 1. Calcular mercados derivados útiles (Doble Oportunidad)
    p_1x = mercados["local"] + mercados["empate"]
    p_x2 = mercados["visitante"] + mercados["empate"]
    p_12 = mercados["local"] + mercados["visitante"]

    # 2. Catálogo de opciones evaluables
    candidatos = [
        {"mercado": "Victoria Local (1)", "prob": mercados["local"], "tipo": "1X2"},
        {"mercado": "Victoria Visitante (2)", "prob": mercados["visitante"], "tipo": "1X2"},
        {"mercado": "Empate (X)", "prob": mercados["empate"], "tipo": "1X2"},
        {"mercado": "1X (Gana Local o Empata)", "prob": p_1x, "tipo": "Doble Oportunidad"},
        {"mercado": "X2 (Gana Visitante o Empata)", "prob": p_x2, "tipo": "Doble Oportunidad"},
        {"mercado": "Más de 2.5 Goles", "prob": mercados["over25"], "tipo": "Goles"},
        {"mercado": "Menos de 2.5 Goles", "prob": mercados["under25"], "tipo": "Goles"},
        {"mercado": "Ambos Equipos Marcan (SÍ)", "prob": mercados["btts"], "tipo": "BTTS"},
        {"mercado": "Ambos Equipos Marcan (NO)", "prob": 1.0 - mercados["btts"], "tipo": "BTTS"},
    ]

    # Ordenar por mayor probabilidad
    candidatos.sort(key=lambda x: x["prob"], reverse=True)
    mejor = candidatos[0]

    prob_pct = mejor["prob"] * 100.0

    # 3. Determinar el Nivel de Confianza
    # Si hay advertencias de muestras pequeñas o datos de fallback, se penaliza el nivel máximo
    muestra_debil = any("Muestra pequeña" in adv for adv in advertencias)

    if prob_pct >= 80.0 and not muestra_debil:
        nivel = "Confianza Alta"
        color = "green"
    elif prob_pct >= 60.0:
        nivel = "Confianza Media"
        color = "yellow"
    else:
        nivel = "Riesgo Alto / Sin Apuesta Recomendada"
        color = "red"

    return {
        "mercado_recomendado": mejor["mercado"],
        "probabilidad_pct": round(prob_pct, 1),
        "nivel_confianza": nivel,
        "color_codigo": color,
        "tipo_mercado": mejor["tipo"],
        "apuesta_sugerida": prob_pct >= 60.0
    }