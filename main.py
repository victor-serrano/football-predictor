"""
Calculadora de probabilidades de fútbol con distribución de Poisson.

Versión 1: sin APIs, sin servicios externos, sin dependencias.
Las medias de goles esperados son DATOS PROVISIONALES introducidos a mano.

Modelo: los goles de cada equipo siguen una distribución de Poisson
independiente:  P(X = k) = e^(-λ) · λ^k / k!
"""

import math

MAX_GOLES = 8          # marcadores de 0 a 8 goles por equipo
TOLERANCIA = 1e-9      # margen numérico para las comprobaciones


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------
def poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) para una Poisson de media lam."""
    return math.exp(-lam) * lam ** k / math.factorial(k)


def distribucion_goles(lam: float) -> list[float]:
    """Lista [P(0), P(1), ..., P(MAX_GOLES)] para una media lam."""
    return [poisson_pmf(k, lam) for k in range(MAX_GOLES + 1)]


def matriz_marcadores(lam_local: float, lam_visitante: float) -> list[list[float]]:
    """matriz[i][j] = P(local marca i) · P(visitante marca j)  (independencia)."""
    dist_l = distribucion_goles(lam_local)
    dist_v = distribucion_goles(lam_visitante)
    return [[pl * pv for pv in dist_v] for pl in dist_l]


def calcular_mercados(matriz: list[list[float]]) -> dict[str, float]:
    """Probabilidades de los mercados a partir de la matriz de marcadores."""
    local = empate = visitante = over25 = under25 = btts = 0.0

    for i in range(len(matriz)):
        for j in range(len(matriz[i])):
            p = matriz[i][j]
            if i > j:
                local += p
            elif i == j:
                empate += p
            else:
                visitante += p

            if i + j > 2.5:
                over25 += p
            else:
                under25 += p

            if i > 0 and j > 0:
                btts += p

    masa = local + empate + visitante

    return {
        "local": local,
        "empate": empate,
        "visitante": visitante,
        "over25": over25,
        "under25": under25,
        "btts": btts,
        "masa": masa,
    }


def verificar_coherencia(res: dict[str, float],
                         lam_local: float,
                         lam_visitante: float) -> list[tuple[str, bool]]:
    """
    Comprobaciones independientes del cálculo por bucles:
    usan las distribuciones marginales (fórmulas cerradas).
    """
    dl = distribucion_goles(lam_local)
    dv = distribucion_goles(lam_visitante)
    cdf_l, cdf_v = sum(dl), sum(dv)

    masa_esperada = cdf_l * cdf_v
    btts_esperado = (cdf_l - dl[0]) * (cdf_v - dv[0])

    def cerca(a: float, b: float) -> bool:
        return abs(a - b) < TOLERANCIA

    probabilidades = [res[k] for k in
                      ("local", "empate", "visitante", "over25", "under25", "btts")]

    return [
        ("Masa de la cuadrícula = P(local ≤ 8) · P(visitante ≤ 8)",
         cerca(res["masa"], masa_esperada)),
        ("Local + Empate + Visitante = masa de la cuadrícula",
         cerca(res["local"] + res["empate"] + res["visitante"], res["masa"])),
        ("Más de 2,5 + Menos de 2,5 = masa de la cuadrícula",
         cerca(res["over25"] + res["under25"], res["masa"])),
        ("Ambos marcan = (P(L ≥ 1)) · (P(V ≥ 1)) dentro de la cuadrícula",
         cerca(res["btts"], btts_esperado)),
        ("Todas las probabilidades están en [0, 1]",
         all(0.0 <= p <= 1.0 for p in probabilidades)),
    ]


# ---------------------------------------------------------------------------
# Entrada / salida
# ---------------------------------------------------------------------------
def pedir_nombre(mensaje: str, por_defecto: str) -> str:
    texto = input(f"{mensaje} [{por_defecto}]: ").strip()
    return texto if texto else por_defecto


def pedir_media_goles(mensaje: str) -> float:
    """Pide un número > 0 (acepta coma o punto decimal)."""
    while True:
        texto = input(mensaje).strip().replace(",", ".")
        try:
            valor = float(texto)
        except ValueError:
            print("  Valor no válido. Escribe un número, por ejemplo 1.45")
            continue
        if not math.isfinite(valor) or valor <= 0:
            print("  La media de goles debe ser un número mayor que 0.")
            continue
        if valor > 6:
            print("  Aviso: una media > 6 es poco realista y la cuadrícula 0-8 "
                  "perdería mucha probabilidad.")
        return valor


def pct(p: float) -> str:
    return f"{p * 100:6.2f} %"


def mostrar_matriz(matriz: list[list[float]], local: str, visitante: str) -> None:
    print(f"\nProbabilidad de cada marcador (%)  —  filas: goles de {local}, "
          f"columnas: goles de {visitante}")
    print("      " + "".join(f"{j:>7}" for j in range(MAX_GOLES + 1)))
    for i, fila in enumerate(matriz):
        print(f"{i:>4}  " + "".join(f"{p * 100:7.2f}" for p in fila))


def main() -> None:
    print("=" * 62)
    print(" PROBABILIDADES DE FÚTBOL (Poisson) — versión 1")
    print(" Las medias de goles son DATOS PROVISIONALES introducidos a mano.")
    print("=" * 62)

    local = pedir_nombre("Equipo local", "Local")
    visitante = pedir_nombre("Equipo visitante", "Visitante")

    lam_l = pedir_media_goles(f"Media de goles esperados de {local} (PROVISIONAL): ")
    lam_v = pedir_media_goles(f"Media de goles esperados de {visitante} (PROVISIONAL): ")

    matriz = matriz_marcadores(lam_l, lam_v)
    res = calcular_mercados(matriz)

    print(f"\n{local} (λ = {lam_l:g}, provisional)  vs  "
          f"{visitante} (λ = {lam_v:g}, provisional)")
    mostrar_matriz(matriz, local, visitante)

    print("\nRESULTADOS")
    print(f"  Victoria {local:<20}: {pct(res['local'])}")
    print(f"  Empate{'':<22}: {pct(res['empate'])}")
    print(f"  Victoria {visitante:<20}: {pct(res['visitante'])}")
    print(f"  Más de 2,5 goles{'':<12}: {pct(res['over25'])}")
    print(f"  Menos de 2,5 goles{'':<10}: {pct(res['under25'])}")
    print(f"  Ambos equipos marcan{'':<8}: {pct(res['btts'])}")

    fuera = 1.0 - res["masa"]
    print(f"\n  Probabilidad fuera de la cuadrícula (>8 goles de algún equipo): "
          f"{pct(fuera)}")
    print(f"  Suma 1X2 dentro de la cuadrícula: {pct(res['local'] + res['empate'] + res['visitante'])}")

    print("\nCOMPROBACIÓN DE COHERENCIA")
    todo_ok = True
    for descripcion, ok in verificar_coherencia(res, lam_l, lam_v):
        print(f"  [{'OK' if ok else 'ERROR'}] {descripcion}")
        todo_ok &= ok
    print("\n  ->", "Todas las comprobaciones superadas." if todo_ok
          else "Hay comprobaciones fallidas: revisa el cálculo.")


if __name__ == "__main__":
    main()