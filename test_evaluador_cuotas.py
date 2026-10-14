import unittest
from evaluador_cuotas import evaluar_cuota_mercado


class PruebasEvaluadorCuotas(unittest.TestCase):

    def test_apuesta_con_valor_positivo(self):
        # Probabilidad modelo: 60% (0.60), Cuota casa: 2.00 (implícita 50%)
        # EV = (0.60 * 2.00) - 1 = +0.20 (+20%)
        res = evaluar_cuota_mercado(0.60, 2.00)
        self.assertTrue(res["tiene_valor"])
        self.assertEqual(res["ev_pct"], 20.0)
        self.assertGreater(res["stake_kelly_pct"], 0.0)

    def test_apuesta_sin_valor(self):
        # Probabilidad modelo: 40% (0.40), Cuota casa: 2.00 (implícita 50%)
        # EV = (0.40 * 2.00) - 1 = -0.20 (-20%)
        res = evaluar_cuota_mercado(0.40, 2.00)
        self.assertFalse(res["tiene_valor"])
        self.assertEqual(res["ev_pct"], -20.0)
        self.assertEqual(res["stake_kelly_pct"], 0.0)

    def test_limite_de_seguridad_stake(self):
        # Escenario con ventaja extrema (EV masivo)
        res = evaluar_cuota_mercado(0.90, 5.00)
        # El stake no debe sobrepasar el cap de seguridad del 5% del bankroll
        self.assertEqual(res["stake_kelly_pct"], 5.0)


if __name__ == "__main__":
    unittest.main()