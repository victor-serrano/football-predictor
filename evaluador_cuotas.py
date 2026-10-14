"""
Módulo de Detección de Value Bets (+EV) y Gestión de Capital (Criterio de Kelly).

Compara las probabilidades del modelo Poisson contra las cuotas ofrecidas por
las casas de apuestas para encontrar selecciones con Valor Esperado Positivo.
"""

from typing import Any, Dict


def evaluar_cuota_mercado(
    probabilidad_modelo: float,
    cuota_casa: float,
    fraccion_kelly: float = 0.25
) -> Dict[str, Any]:
    """
    Calcula el Valor Esperado (+EV) y el porcentaje del Bankroll sugerido (Kelly).

    :param probabilidad_modelo: Probabilidad calculada por Poisson (0.0 a 1.0).
    :param cuota_casa: Cuota decimal ofrecida por la casa (ej. 1.85, 2.10).
    :param fraccion_kelly: Ajuste de prudencia (0.25 equivale a un Kelly al 25%).
    :return: Diccionario con EV%, bankroll_pct sugerido y banderas de valor.
    """
    if cuota_casa <= 1.0 or probabilidad_modelo <= 0:
        return {
            "tiene_valor": False,
            "ev_pct": 0.0,
            "stake_kelly_pct": 0.0,
            "mensaje": "Cuota o probabilidad no válida."
        }

    # 1. Probabilidad implícita de la casa
    prob_implicita_casa = 1.0 / cuota_casa

    # 2. Valor Esperado (EV)
    # EV = (p * cuota) - 1
    ev = (probabilidad_modelo * cuota_casa) - 1.0
    ev_pct = round(ev * 100.0, 2)

    # 3. Criterio de Kelly
    # f* = (p * cuota - 1) / (cuota - 1) = ev / (cuota - 1)
    b = cuota_casa - 1.0
    f_kelly = ev / b

    if f_kelly > 0 and ev > 0:
        stake_sugerido = round((f_kelly * fraccion_kelly) * 100.0, 2)
        # Cap de seguridad: Nunca sugerir más del 5% del bankroll en un solo partido
        stake_sugerido = min(stake_sugerido, 5.0)
        tiene_valor = True
    else:
        stake_sugerido = 0.0
        tiene_valor = False

    return {
        "tiene_valor": tiene_valor,
        "ev_pct": ev_pct,
        "prob_modelo_pct": round(probabilidad_modelo * 100.0, 1),
        "prob_casa_pct": round(prob_implicita_casa * 100.0, 1),
        "cuota_casa": cuota_casa,
        "stake_kelly_pct": stake_sugerido,
    }