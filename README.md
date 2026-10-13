# Pipeline de extracción de entidades técnicas

**Pre-entrega 2 · AI Engineering (Coderhouse)**: pipeline de procesamiento validado con **LangChain + Pydantic + Claude (Anthropic)**.

Recibe un párrafo de texto sin procesar (una descripción de arquitectura de software, un incidente o un log de error) y devuelve un **objeto validado** con las tecnologías mencionadas, el nivel de criticidad y un resumen técnico.

```json
{
  "tecnologias": ["FastAPI", "PostgreSQL", "psycopg2", "Redis"],
  "nivel_de_criticidad": "alta",
  "resumen_tecnico": "El api-gateway en FastAPI sufre timeouts porque el pool de conexiones a PostgreSQL está agotado (psycopg2.OperationalError: too many clients already), mientras Redis funciona normalmente. Los usuarios reciben HTTP 504 en el checkout, con impacto directo en producción."
}
```

## Estructura

| Archivo | Contenido |
|---|---|
| `schemas.py` | **Contrato de datos (Pydantic).** `NivelCriticidad` (enum: `baja`, `media`, `alta`) y `ExtraccionTecnica` (`tecnologias`, `nivel_de_criticidad`, `resumen_tecnico`) con sus restricciones. Incluye una autoprueba que no usa la API. |
| `chain.py` | **Cadena LCEL.** Cliente `ChatAnthropic`, `ChatPromptTemplate` modular, `prompt \| model.with_structured_output(ExtraccionTecnica)`, verificación del `stop_reason`, reintentos con `.with_retry()`, logs y la función asíncrona `process_text()`. |
| `probar_pipeline.py` | **Mini-script de prueba asíncrono** de punta a punta: dos textos en paralelo y dos pruebas de estrés. |
| `ejemplo_salida.json` | Objetos validados que devolvió el pipeline en una corrida real. |
| `salida_prueba.txt` | Los logs completos de esa corrida. |
| `requirements.txt` | Dependencias con versiones fijas. |
| `.env.example` | Plantilla para la clave de API (el `.env` real no se sube). |

## Instalación y ejecución

Requiere Python 3.10 o más (desarrollado con 3.13) y una clave de API de Anthropic.

```bash
git clone https://github.com/anukvilarasau/pipeline-extraccion-tecnica.git
cd pipeline-extraccion-tecnica

python3 -m venv .venv
source .venv/bin/activate            # en Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                 # y poné tu clave real en ANTHROPIC_API_KEY
```

```bash
python schemas.py            # autoprueba del contrato (no usa la API, no gasta crédito)
python probar_pipeline.py    # prueba completa de punta a punta (unas 6 llamadas a la API)
```

Uso desde código:

```python
import asyncio
from chain import process_text

resultado = asyncio.run(process_text("ERROR: Kafka consumer lag creciente en el servicio de pagos (Go)..."))
print(resultado.model_dump_json(indent=2))
```

**Configuración opcional (`.env`):**
- `ANTHROPIC_MODEL`: modelo de Claude. Por defecto `claude-opus-5-5`; `claude-haiku-5-5` es mucho más barato.
- `MAX_TOKENS`: tope de *tokens* de la respuesta. Por defecto 2048.

## Cómo funciona

```
texto
  │
  ▼
anotar_intento ──► ChatPromptTemplate ──► ChatAnthropic.with_structured_output(ExtraccionTecnica)
                   (system + human)        method="json_schema", include_raw=True
                                                     │
                                                     ▼
                                           verificar_respuesta
                                           1. ¿stop_reason == "max_tokens"? → RespuestaIncompleta
                                           2. ¿error de parseo / validación Pydantic? → se relanza
                                           3. OK → objeto ExtraccionTecnica
  └──────────────── todo envuelto en .with_retry(stop_after_attempt=3) ────────────────┘
```

### 1. Contrato de datos (`schemas.py`)
- `tecnologias: list[str]`: **mínimo 1** (`min_length=1`). Un `field_validator` saca espacios, repetidos (sin importar mayúsculas) y **rellenos** como "No especificada", "N/A" o "Desconocido". Si no queda ninguna tecnología real, lanza `ValidationError`.
- `nivel_de_criticidad`: **enum** `NivelCriticidad`, que solo admite `baja`, `media` o `alta`.
- `resumen_tecnico: str`: entre **20 y 500 caracteres**.
- `extra="forbid"`: se rechaza cualquier campo que el modelo invente.
- La `description` de cada campo le explica al modelo qué poner. Por ejemplo, cuándo la criticidad es "alta".

### 2. Prompt modular (`ChatPromptTemplate`)
- **Sin f-strings:** el prompt es un molde con dos variables, `{texto}` e `{instrucciones_formato}`, que completa LangChain.
- Las **instrucciones de formato se generan a partir del propio esquema** (`instrucciones_desde_esquema()`). Si cambia `schemas.py`, el prompt se actualiza solo.
- El mensaje **system** tiene el rol y las reglas: no inventar tecnologías, usar nombres propios, evaluar la criticidad por el impacto descrito. El mensaje **human** tiene el texto, que se trata **como dato, no como instrucción** (protección básica contra *prompt injection*).

### 3. Cadena LCEL y salida estructurada
```python
extraccion = prompt | modelo.with_structured_output(ExtraccionTecnica, method="json_schema", include_raw=True)
```
- `method="json_schema"` usa la **salida estructurada nativa de Claude**. Claude Opus 5.5 no admite el método por herramienta forzada (`function_calling`), que es el valor por defecto de LangChain.
- `include_raw=True` devuelve, además del objeto, la respuesta cruda, para poder leer el `stop_reason`.

### 4. Resiliencia
- **Detección de respuestas cortadas (el `finish_reason` de la consigna):** en Anthropic el motivo de corte se llama `stop_reason`. Si vale `max_tokens`, el modelo se quedó sin espacio y el objeto puede estar incompleto. `verificar_respuesta` lo rechaza **antes de usarlo** y lanza `RespuestaIncompleta`.
- **`.with_retry()`:** reintenta toda la cadena hasta **3 intentos**, con espera exponencial, ante `RespuestaIncompleta`, `OutputParserException` (JSON mal formado) o `ValidationError` (datos que no cumplen el contrato). Si se agotan los intentos, `process_text()` registra el error y relanza la excepción, para que quien llama decida qué hacer.
- **Errores de red, 429 y 5xx:** los reintenta el cliente `ChatAnthropic` (`max_retries=2`), con *timeout* de 60 s.

### 5. Ejecución asíncrona y logs
- `async def process_text(text: str) -> ExtraccionTecnica` ejecuta la cadena con `await cadena.ainvoke({"texto": text})`.
- **Logs** con hora en milisegundos:

| Log | Qué registra |
|---|---|
| `ENTRADA` | Largo y vista previa del texto recibido. |
| `INTENTO` / `REINTENTO` | Número de intento y modelo usado. |
| `RESPUESTA` | `stop_reason` y *tokens* de entrada y salida. |
| `INCOMPLETA` | La respuesta se cortó por el límite de *tokens*. |
| `VALIDACIÓN` | OK (con la cantidad de tecnologías y la criticidad) o FALLÓ (con el motivo). |
| `RESULTADO` / `FALLÓ` | El objeto final validado, o el error tras agotar los intentos. |

## Pruebas y resultados

`probar_pipeline.py` corre cuatro casos. Los logs completos están en `salida_prueba.txt`.

| # | Caso | Resultado |
|---|---|---|
| 1 | **Log de error** (FastAPI + PostgreSQL + Redis) | ✅ Válido en el 1.er intento: 4 tecnologías, criticidad **alta**. |
| 2 | **Descripción de arquitectura** (Django, Docker, AWS ECS, RabbitMQ, Celery, S3), procesada **en paralelo** con el caso 1 mediante `asyncio.gather` | ✅ Válido en el 1.er intento: 6 tecnologías, criticidad **baja**. Las dos `ENTRADA` arrancan juntas y terminan en ~6 s, contra ~9 s en serie. |
| 3 | **Estrés A: texto ambiguo** ("el sistema anduvo raro, algunos dicen que fue la base, otros la red") | ⚠️ El modelo **se recupera** y devuelve un objeto válido. |
| 4 | **Estrés B: respuesta cortada**, con `max_tokens=40` | ✅ Los 3 intentos terminan con `stop_reason=max_tokens`. Se detecta el corte, se registra `INCOMPLETA`, se reintenta 2 veces y al final `FALLÓ ... RespuestaIncompleta`. **El programa sigue sin romperse.** |

**Qué enseñó el caso 3.** En la primera versión, ante el texto ambiguo, el modelo devolvió `"tecnologias": ["No especificada"]`. Eso cumple "la lista no está vacía", pero es un relleno. Por eso se agregó la regla de rellenos al validador y al prompt. En la corrida siguiente el modelo esquivó la regla con una categoría genérica: `["Base de datos (genérica)"]`, que sale de "la base" del texto.

Esto muestra el límite de la validación estructural: **Pydantic garantiza que hay una lista con al menos un texto, pero no que el contenido sea verdadero.** Esa última capa (¿la tecnología existe?, ¿la criticidad está bien evaluada?) requiere criterio humano o una verificación adicional contra el texto de entrada. El validador frena los rellenos obvios y el resto queda señalado para revisión.

## Checklist de la consigna

- [x] `schemas.py` con el modelo Pydantic bien tipado (lista de textos, enum y texto, con restricciones).
- [x] Prompt modular con `ChatPromptTemplate`, que acepta el texto y las instrucciones de formato, sin f-strings.
- [x] Cadena LCEL: `prompt | model.with_structured_output(ExtraccionTecnica)`.
- [x] Reintentos con `.with_retry()` ante JSON mal formado o incompleto.
- [x] Detección de respuesta cortada (`stop_reason == "max_tokens"`) antes de usar el objeto.
- [x] Función asíncrona `process_text()` que usa `.ainvoke()`, con logs de entrada, validación y reintentos.
- [x] Mini-script de prueba asíncrono, más prueba de estrés con texto ambiguo.
- [x] Repositorio de GitHub con todo el código.

## Costos

Cada llamada usa unos 1.500 *tokens* de entrada y entre 150 y 400 de salida. Con `claude-opus-5-5` ($4 / $20 por millón de *tokens*) son unos **USD 0,01** por texto, y una corrida completa de `probar_pipeline.py` cuesta unos **USD 0,10**. Con `claude-haiku-5-5` el costo baja unas 40 veces.
