"""
probar_pipeline.py — Mini-script de prueba asíncrono (de punta a punta).

Se ejecuta con:  python probar_pipeline.py

1. Procesa dos textos técnicos AL MISMO TIEMPO (asyncio.gather) con process_text().
2. Prueba de estrés A: un texto ambiguo, para ver si el validador lanza
   excepciones o si el modelo se recupera.
3. Prueba de estrés B: limita la respuesta a muy pocos tokens para forzar un
   corte (stop_reason = max_tokens) y comprobar que se detecta y se reintenta.

Guarda los resultados válidos en ejemplo_salida.json.
"""

import asyncio
import json
import logging
from pathlib import Path

# Logs con la hora en milisegundos. Se configuran ANTES de importar la cadena.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
for ruidoso in ("httpx", "anthropic", "httpcore"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)

from chain import ERRORES_REINTENTABLES, construir_cadena, process_text  # noqa: E402

log = logging.getLogger("prueba")

TEXTOS = {
    "log_de_error": (
        "ERROR 2026-10-12 03:14:07 api-gateway: FastAPI worker timeout al consultar PostgreSQL; "
        "pool de conexiones agotado (psycopg2.OperationalError: too many clients already). "
        "Redis de caché respondiendo normalmente. Los usuarios reciben HTTP 504 en el checkout."
    ),
    "arquitectura": (
        "El servicio de reportes es una app en Django que corre en contenedores Docker sobre AWS ECS. "
        "Los reportes pesados se encolan en RabbitMQ y los procesan workers de Celery; los PDF "
        "generados se guardan en Amazon S3. Hoy funciona estable y estamos documentando el diseño."
    ),
    "ambiguo": (
        "Anoche el sistema anduvo raro un rato y después se arregló solo. Algunos dicen que fue "
        "la base, otros que fue la red. Nadie tocó nada."
    ),
}


def titulo(texto: str) -> None:
    log.info("=" * 20 + f" {texto} " + "=" * 20)


async def main() -> None:
    resultados: dict[str, dict] = {}

    # 1) Dos textos a la vez: se ve en los logs que las dos ENTRADAS arrancan juntas.
    titulo("1. Dos textos en paralelo con asyncio.gather")
    log_error, arquitectura = await asyncio.gather(
        process_text(TEXTOS["log_de_error"]),
        process_text(TEXTOS["arquitectura"]),
    )
    resultados["log_de_error"] = log_error.model_dump(mode="json")
    resultados["arquitectura"] = arquitectura.model_dump(mode="json")

    # 2) Estrés A: texto ambiguo, casi sin información técnica.
    titulo("2. Estrés A: texto ambiguo")
    try:
        ambiguo = await process_text(TEXTOS["ambiguo"])
        resultados["ambiguo"] = ambiguo.model_dump(mode="json")
        log.info("-> El modelo se recuperó y devolvió un objeto válido.")
    except ERRORES_REINTENTABLES as error:
        log.info("-> El validador rechazó todas las respuestas (%s); el programa sigue.", type(error).__name__)

    # 3) Estrés B: tope de 40 tokens -> la respuesta se corta (stop_reason = max_tokens).
    titulo("3. Estrés B: respuesta cortada por falta de tokens (max_tokens=40)")
    cadena_corta = construir_cadena(max_tokens=40)
    try:
        await process_text(TEXTOS["log_de_error"], cadena_a_usar=cadena_corta)
        log.info("-> Inesperado: alcanzaron 40 tokens.")
    except ERRORES_REINTENTABLES as error:
        log.info("-> Corte detectado en cada intento; se agotaron los reintentos (%s) y el programa sigue.",
                 type(error).__name__)

    destino = Path(__file__).with_name("ejemplo_salida.json")
    destino.write_text(json.dumps(resultados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    titulo(f"Listo: {len(resultados)} resultado(s) válido(s) guardados en {destino.name}")


if __name__ == "__main__":
    asyncio.run(main())
