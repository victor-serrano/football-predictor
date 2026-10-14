"""
Módulo de mapeo y unificación de nombres de equipos.

Traduce las distintas variantes de nombres de fuentes (CSV de Football-Data.co.uk
y API v4 de football-data.org) a un nombre canónico o estándar.
"""

import sqlite3
from pathlib import Path
from typing import Optional

# Mapeo: Variante/Oficial -> Nombre Canónico
MAPEO_EQUIPOS = {
    # España
    "Real Madrid CF": "Real Madrid",
    "FC Barcelona": "Barcelona",
    "Club Atlético de Madrid": "Atletico Madrid",
    "Atlético Madrid": "Atletico Madrid",
    "Sevilla FC": "Sevilla",
    "Real Betis Balompié": "Betis",
    "Real Sociedad de Fútbol": "Real Sociedad",
    "Villarreal CF": "Villarreal",
    "Valencia CF": "Valencia",
    "Athletic Club": "Athletic Bilbao",
    "RCD Espanyol de Barcelona": "Espanyol",
    "RCD Espanyol": "Espanyol",
    "Getafe CF": "Getafe",
    "Girona FC": "Girona",
    "RC Celta de Vigo": "Celta",
    "Celta de Vigo": "Celta",
    "CA Osasuna": "Osasuna",
    "Rayo Vallecano de Madrid": "Rayo Vallecano",
    "RCD Mallorca": "Mallorca",
    "Deportivo Alavés": "Alaves",
    "Real Valladolid CF": "Valladolid",
    "UD Las Palmas": "Las Palmas",
    "CD Leganés": "Leganes",

    # Inglaterra
    "Arsenal FC": "Arsenal",
    "Chelsea FC": "Chelsea",
    "Liverpool FC": "Liverpool",
    "Manchester City FC": "Man City",
    "Manchester United FC": "Man United",
    "Tottenham Hotspur FC": "Tottenham",
    "Aston Villa FC": "Aston Villa",
    "Newcastle United FC": "Newcastle",

    # Italia
    "AS Roma": "Roma",
    "AC Milan": "Milan",
    "FC Internazionale Milano": "Inter",
    "Inter Milan": "Inter",
    "Juventus FC": "Juventus",
    "SS Lazio": "Lazio",
    "SSC Napoli": "Napoli",
    "ACF Fiorentina": "Fiorentina",
    "Atalanta BC": "Atalanta",

    # Alemania
    "FC Bayern München": "Bayern Munich",
    "Bayern Munich": "Bayern Munich",
    "Borussia Dortmund": "Dortmund",
    "Bayer 04 Leverkusen": "Leverkusen",
    "RB Leipzig": "RB Leipzig",
}


def obtener_nombre_canonico(nombre: str) -> str:
    """Devuelve el nombre estandarizado del equipo si existe en el mapeo."""
    nombre_clean = nombre.strip()
    return MAPEO_EQUIPOS.get(nombre_clean, nombre_clean)


def obtener_variantes_equipo(nombre_canonico: str) -> list[str]:
    """
    Devuelve la lista de todas las variantes registradas para un equipo canónico.
    Sirve para hacer consultas SQL tipo: WHERE equipo IN ('Real Madrid', 'Real Madrid CF').
    """
    canonico = obtener_nombre_canonico(nombre_canonico)
    variantes = {canonico}

    for variante, objetivo in MAPEO_EQUIPOS.items():
        if objetivo == canonico:
            variantes.add(variante)

    return sorted(list(variantes))


def obtener_lista_equipos_unicos(conn: sqlite3.Connection) -> list[str]:
    """Obtiene la lista de nombres canónicos de equipos únicos en la BD."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT equipo FROM (
            SELECT equipo_local AS equipo FROM partidos WHERE goles_local IS NOT NULL
            UNION
            SELECT equipo_visitante AS equipo FROM partidos WHERE goles_visitante IS NOT NULL
        )
    """)
    filas = cursor.fetchall()
    
    unicos = set()
    for row in filas:
        unicos.add(obtener_nombre_canonico(row[0]))

    return sorted(list(unicos))