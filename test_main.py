import math
import unittest

from main import (calcular_mercados, matriz_marcadores,
                  poisson_pmf, verificar_coherencia)


class PruebasBasicas(unittest.TestCase):

    def test_poisson_valor_conocido(self):
        # P(0; 1,5) = e^-1,5
        self.assertAlmostEqual(poisson_pmf(0, 1.5), math.exp(-1.5), places=12)

    def test_menos_de_2_5_coincide_con_poisson_total(self):
        # La suma de dos Poisson independientes es Poisson(λ1 + λ2):
        # P(total ≤ 2) = e^-μ · (1 + μ + μ²/2), con μ = 2,7 (exacto en la cuadrícula)
        mu = 1.5 + 1.2
        esperado = math.exp(-mu) * (1 + mu + mu ** 2 / 2)
        res = calcular_mercados(matriz_marcadores(1.5, 1.2))
        self.assertAlmostEqual(res["under25"], esperado, places=12)

    def test_equipos_iguales_son_simetricos(self):
        res = calcular_mercados(matriz_marcadores(1.3, 1.3))
        self.assertAlmostEqual(res["local"], res["visitante"], places=12)

    def test_coherencia_global(self):
        res = calcular_mercados(matriz_marcadores(1.5, 1.2))
        self.assertTrue(all(ok for _, ok in verificar_coherencia(res, 1.5, 1.2)))


if __name__ == "__main__":
    unittest.main()