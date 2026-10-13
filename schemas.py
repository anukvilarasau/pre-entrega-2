"""
schemas.py — Contrato de datos del pipeline (Pydantic).

Define la forma EXACTA que tiene que tener la información extraída de un
texto técnico. Si el LLM devuelve algo que no cumple este contrato, Pydantic
lanza un ValidationError en lugar de dejar pasar datos incorrectos.

Ojo: Pydantic valida la ESTRUCTURA (campos, tipos y reglas), no si el
contenido es verdadero. Eso sigue requiriendo criterio humano.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Valores "de relleno" que un LLM a veces usa para esquivar la regla de "lista
# no vacía" cuando el texto no menciona tecnologías. No son tecnologías reales,
# así que se descartan (los encontró la prueba de estrés con un texto ambiguo).
RELLENOS_NO_VALIDOS = {
    "no especificada", "no especificado", "no especificadas", "no especificados",
    "desconocida", "desconocido", "ninguna", "ninguno", "n/a", "na", "none", "null",
    "no aplica", "sin especificar", "no mencionada", "no mencionado", "-",
}


class NivelCriticidad(str, Enum):
    """Lista cerrada de valores válidos para la criticidad (un "enum").

    Hereda de `str` para que en el JSON se vea como texto: "alta", no NivelCriticidad.ALTA.
    """

    BAJA = "baja"
    MEDIA = "media"
    ALTA = "alta"


class ExtraccionTecnica(BaseModel):
    """Entidades técnicas extraídas de un texto (una descripción de arquitectura o un log de error)."""

    # extra="forbid": si el modelo agrega campos que no están acá, se rechaza la respuesta.
    model_config = ConfigDict(extra="forbid")

    # Cada `description` le llega al LLM como instrucción sobre qué poner en el campo.
    tecnologias: list[str] = Field(
        min_length=1,
        description=(
            "Tecnologías concretas mencionadas en el texto: lenguajes, frameworks, bases de datos, "
            "servicios en la nube, colas, cachés, herramientas. Usá el nombre propio de cada una "
            "(por ejemplo 'PostgreSQL', no 'base de datos'). Al menos una."
        ),
    )
    nivel_de_criticidad: NivelCriticidad = Field(
        description=(
            "Gravedad del problema o riesgo descrito: 'alta' si hay caída, pérdida de datos, "
            "riesgo de seguridad o impacto en producción; 'media' si hay degradación o un "
            "problema acotado; 'baja' si es informativo o sin impacto."
        ),
    )
    resumen_tecnico: str = Field(
        min_length=20,
        max_length=500,
        description="Resumen técnico de una o dos oraciones: qué componentes intervienen y cuál es el problema o el punto clave.",
    )

    @field_validator("tecnologias")
    @classmethod
    def limpiar_tecnologias(cls, valores: list[str]) -> list[str]:
        """Regla propia: saca espacios, entradas vacías, rellenos y repetidos (sin importar mayúsculas).

        Si después de limpiar no queda ninguna tecnología real, rechaza el dato.
        """
        limpias: list[str] = []
        vistas: set[str] = set()
        for valor in valores:
            nombre = valor.strip()
            clave = nombre.lower()
            if nombre and clave not in RELLENOS_NO_VALIDOS and clave not in vistas:
                vistas.add(clave)
                limpias.append(nombre)
        if not limpias:
            raise ValueError("no se identificó ninguna tecnología concreta (lista vacía o solo rellenos)")
        return limpias


# ---------------------------------------------------------------------------
# Prueba rápida del contrato (no usa la IA ni gasta crédito).
# Se ejecuta con:  python schemas.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from pydantic import ValidationError

    print("1) Dato válido (como el ejemplo de la consigna):")
    ok = ExtraccionTecnica(
        tecnologias=["FastAPI", "Redis", "PostgreSQL", "redis "],  # "redis " repetido: se limpia
        nivel_de_criticidad="alta",
        resumen_tecnico="API con caché en Redis y persistencia en PostgreSQL; cuello de botella en conexiones concurrentes.",
    )
    print("   ", ok.model_dump_json(indent=2).replace("\n", "\n    "))

    casos_invalidos = {
        "2) Lista de tecnologías vacía": dict(
            tecnologias=[], nivel_de_criticidad="media", resumen_tecnico="Un resumen técnico suficientemente largo."
        ),
        "3) Criticidad fuera del enum ('crítica')": dict(
            tecnologias=["Docker"], nivel_de_criticidad="crítica", resumen_tecnico="Un resumen técnico suficientemente largo."
        ),
        "4) Falta el resumen y sobra un campo inventado": dict(
            tecnologias=["Kafka"], nivel_de_criticidad="baja", opinion="me parece grave"
        ),
        "5) Tecnología de relleno ('No especificada')": dict(
            tecnologias=["No especificada"], nivel_de_criticidad="media",
            resumen_tecnico="Un resumen técnico suficientemente largo.",
        ),
    }
    for titulo, datos in casos_invalidos.items():
        print(f"\n{titulo}:")
        try:
            ExtraccionTecnica(**datos)
            print("    (se aceptó; no debería)")
        except ValidationError as error:
            for e in error.errors():
                print(f"    RECHAZADO -> campo {e['loc']}: {e['msg']}")
