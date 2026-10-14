
"""
Servidor Web Local para football-predictor.

Utiliza la biblioteca estándar de Python para servir la interfaz
gráfica y exponer una API REST.

Uso:
    python3 app.py
Luego abre http://localhost:8000
"""

import json
import sqlite3
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

import estadisticas
import prediccion
import equipos
import recomendador
import evaluador_cuotas
import generador_jornada

PORT = 8000
BASE_DIR = Path(__file__).resolve().parent
RUTA_BD = BASE_DIR / "data" / "partidos.db"


def obtener_equipos_y_competiciones() -> dict[str, Any]:
    """Devuelve equipos unificados y competiciones registradas."""
    with sqlite3.connect(RUTA_BD) as conn:
        equipos_unicos = equipos.obtener_lista_equipos_unicos(conn)

        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT competicion
            FROM partidos
            WHERE competicion IS NOT NULL
            ORDER BY competicion ASC
        """)
        competiciones = [row[0] for row in cursor.fetchall()]

    return {
        "equipos": equipos_unicos,
        "competiciones": competiciones,
    }


class PredictorHTTPRequestHandler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        # Sirve los archivos desde la carpeta del proyecto,
        # independientemente de dónde se ejecute el comando.
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        try:
            # 1. Configuración del dashboard
            if path == "/api/config":
                self.responder_json(obtener_equipos_y_competiciones())
                return

            # 2. Predicción de un partido
            if path == "/api/prediccion":
                local = query_params.get("local", [""])[0].strip()
                visitante = query_params.get("visitante", [""])[0].strip()
                competicion = query_params.get("competicion", [None])[0]
                fecha_corte = query_params.get("fecha_corte", [None])[0]

                cuota_param = query_params.get("cuota", [None])[0]
                cuota = None

                if cuota_param:
                    try:
                        cuota = float(cuota_param)
                    except (TypeError, ValueError):
                        cuota = None

                if competicion in ("", "Todas"):
                    competicion = None

                if not fecha_corte:
                    fecha_corte = None

                if not local or not visitante:
                    self.responder_json({
                        "ok": False,
                        "error": "Debes indicar los dos equipos.",
                    }, status=400)
                    return

                conn = prediccion.conectar_bd(RUTA_BD)
                try:
                    res = prediccion.predecir_partido(
                        conn,
                        local,
                        visitante,
                        competicion,
                        fecha_corte,
                    )

                    if res.get("ok"):
                        res["stats_local_detallada"] = (
                            estadisticas.obtener_estadisticas_equipo(
                                conn, local, competicion, fecha_corte
                            )
                        )
                        res["stats_visitante_detallada"] = (
                            estadisticas.obtener_estadisticas_equipo(
                                conn, visitante, competicion, fecha_corte
                            )
                        )

                        rec = recomendador.generar_recomendacion(
                            res["mercados"],
                            res.get("detalles", {}).get("advertencias", []),
                        )

                        if cuota is not None and cuota > 1.0:
                            prob_mercado = rec["probabilidad_pct"] / 100.0
                            rec["evaluacion_cuota"] = (
                                evaluador_cuotas.evaluar_cuota_mercado(
                                    prob_mercado, cuota
                                )
                            )

                        res["recomendacion"] = rec

                    self.responder_json(res)
                finally:
                    conn.close()
                return

            # 3. Mejores apuestas de la jornada / combinada
            if path == "/api/combinada":
                competicion = query_params.get("competicion", [None])[0]

                if competicion in ("", "Todas"):
                    competicion = None

                try:
                    limite = int(query_params.get("limite", ["5"])[0])
                    min_probabilidad = float(
                        query_params.get("min_probabilidad", ["60"])[0]
                    )
                except (TypeError, ValueError):
                    self.responder_json({
                        "ok": False,
                        "error": "Los parámetros limite y min_probabilidad deben ser numéricos.",
                    }, status=400)
                    return

                # Evita valores extremos o inválidos en la consulta.
                limite = max(1, min(limite, 20))
                min_probabilidad = max(0.0, min(min_probabilidad, 100.0))

                conn = prediccion.conectar_bd(RUTA_BD)
                try:
                    resultado = generador_jornada.buscar_mejores_apuestas(
                        conn,
                        competicion=competicion,
                        limite=limite,
                        min_probabilidad=min_probabilidad,
                    )
                    self.responder_json(resultado)
                finally:
                    conn.close()
                return

            # 4. Página principal
            if path == "/":
                self.path = "/templates/index.html"
            elif path == "/index.html":
                self.path = "/templates/index.html"

            super().do_GET()

        except Exception as exc:
            # Evita que un error de la API deje la petición sin respuesta.
            print(f"Error atendiendo {path}: {exc}")
            self.responder_json({
                "ok": False,
                "error": "Se produjo un error interno al procesar la solicitud.",
            }, status=500)

    def responder_json(self, data: Any, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server():
    server_address = ("", PORT)
    httpd = HTTPServer(server_address, PredictorHTTPRequestHandler)

    print("\n==================================================")
    print("   FOOTBALL PREDICTOR - INTERFAZ WEB LOCAL")
    print("==================================================")
    print(" Servidor iniciado correctamente.")
    print(f" Abre tu navegador en: http://localhost:{PORT}")
    print(" Presiona Ctrl+C para detener el servidor.\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    run_server()
