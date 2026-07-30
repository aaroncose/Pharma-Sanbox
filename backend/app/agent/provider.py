"""Capa de proveedor de IA.

El requisito es poder cambiar de modelo sin reescribir la aplicación. Eso no se
consigue con una función `llamar_al_modelo()`: se consigue aislando **tres**
cosas distintas que suelen mezclarse.

1. **El transporte** — cómo se hace la llamada.
2. **Las capacidades** — qué acepta cada modelo. No son iguales, y enviar un
   parámetro que un modelo no admite no degrada: devuelve 400. Están declaradas
   en `MODEL_CAPABILITIES` y la petición se construye a partir de ahí.
3. **El coste** — precio por millón de tokens, por modelo, para poder informar
   del coste real de cada operación en la auditoría.

Diferencias reales entre los modelos que usa este proyecto, y que hacen que la
tabla de capacidades no sea una abstracción prematura:

  · `output_config.effort` existe en Opus 4.8 y Sonnet 5, y **da error** en
    Haiku 4.5. El verificador usa Haiku, así que enviar `effort` a todos por
    igual rompería exactamente el segundo paso del harness.
  · `temperature`, `top_p` y `top_k` se **rechazan con 400** en Opus 4.8 y
    Sonnet 5. Por eso esta capa no expone `temperature`: sería un parámetro que
    funciona en un modelo y rompe en otro.
  · En Opus 4.8, **omitir** `thinking` lo desactiva; en Sonnet 5, omitirlo lo
    activa. Se envía siempre de forma explícita para que el comportamiento no
    dependa del modelo configurado.
  · `thinking.display` por defecto es `"omitted"`: los bloques de pensamiento
    llegan vacíos. Se pide `"summarized"` porque las trazas del agente son un
    entregable del proyecto. **Pedirlo no garantiza recibirlo**: con pensamiento
    adaptativo es el modelo quien decide cuánto razona, y puede decidir que
    cero. Medido con la misma petición y el mismo prompt de razonamiento:

        claude-opus-4-8  -> bloques ['thinking', 'text'], thinking_tokens 139
        claude-sonnet-5  -> bloques ['text'],             thinking_tokens 0

    Por eso `LLMUsage.thinking_tokens` se registra siempre. Sin ese número, una
    traza sin razonamiento es ambigua: no se puede distinguir un modelo que
    decidió no razonar de un fallo al capturar el bloque. Con él, la traza
    afirma cuál de las dos cosas pasó.

Sin credencial, el proveedor mock determinista ocupa su lugar con el mismo
contrato. No es un stub de pruebas: es lo que permite que la demostración y las
evaluaciones se ejecuten sin red, sin coste y con resultados reproducibles.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from app.config import settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger

log = get_logger("llm")

Effort = Literal["low", "medium", "high", "xhigh", "max"]


# ─────────────────────────────────────────────────────────────────────────────
# Capacidades y precios por modelo
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """Qué admite un modelo concreto.

    `supports_effort` y `supports_adaptive_thinking` no son adornos: enviar un
    parámetro no admitido devuelve 400, no un aviso.
    """

    model_id: str
    context_window: int
    max_output_tokens: int
    input_price_per_mtok: float   # USD por millón de tokens de entrada
    output_price_per_mtok: float
    cache_read_price_per_mtok: float
    supports_effort: bool
    supports_adaptive_thinking: bool
    supports_structured_output: bool
    # Prefijo mínimo para que la caché de prompt se active. Por debajo de este
    # umbral no se cachea y no hay error: `cache_creation_input_tokens` sale 0.
    min_cacheable_prefix_tokens: int


MODEL_CAPABILITIES: dict[str, ModelCapabilities] = {
    "claude-opus-4-8": ModelCapabilities(
        model_id="claude-opus-4-8",
        context_window=1_000_000,
        max_output_tokens=128_000,
        input_price_per_mtok=5.00,
        output_price_per_mtok=25.00,
        cache_read_price_per_mtok=0.50,
        supports_effort=True,
        supports_adaptive_thinking=True,
        supports_structured_output=True,
        min_cacheable_prefix_tokens=4096,
    ),
    "claude-sonnet-5": ModelCapabilities(
        model_id="claude-sonnet-5",
        context_window=1_000_000,
        max_output_tokens=128_000,
        input_price_per_mtok=3.00,
        output_price_per_mtok=15.00,
        cache_read_price_per_mtok=0.30,
        supports_effort=True,
        supports_adaptive_thinking=True,
        supports_structured_output=True,
        min_cacheable_prefix_tokens=2048,
    ),
    "claude-haiku-4-5": ModelCapabilities(
        model_id="claude-haiku-4-5",
        context_window=200_000,
        max_output_tokens=64_000,
        input_price_per_mtok=1.00,
        output_price_per_mtok=5.00,
        cache_read_price_per_mtok=0.10,
        # Haiku 4.5 NO admite `output_config.effort`: devuelve error.
        supports_effort=False,
        supports_adaptive_thinking=False,
        supports_structured_output=True,
        min_cacheable_prefix_tokens=4096,
    ),
}

# Tipo de cambio para expresar el coste en euros, que es la moneda de la
# interfaz. Es una constante y no una llamada a un servicio de divisas a
# propósito: un coste aproximado y estable es más útil aquí que uno exacto y
# variable, y el proyecto no debe depender de una API más.
USD_TO_EUR = 0.92


def capabilities_for(model_id: str) -> ModelCapabilities:
    caps = MODEL_CAPABILITIES.get(model_id)
    if caps is None:
        raise ValueError(
            f"Modelo no declarado en MODEL_CAPABILITIES: {model_id}. "
            "Añádelo antes de usarlo: enviar parámetros no admitidos devuelve 400."
        )
    return caps


# ─────────────────────────────────────────────────────────────────────────────
# Contrato
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Desglose de `output_tokens`, NO un sumando aparte: el razonamiento ya
    # viene facturado dentro de la salida. Se guarda para poder distinguir en la
    # traza "el modelo no razonó" de "el razonamiento se perdió por el camino",
    # que desde una casilla vacía se ven igual.
    thinking_tokens: int = 0

    def cost_eur(self, caps: ModelCapabilities) -> float:
        """Coste aproximado en euros.

        Los tokens leídos de caché se facturan a una décima parte, así que
        contarlos como entrada normal sobreestimaría el coste de forma
        sistemática en las conversaciones largas del simulador.
        """
        usd = (
            self.input_tokens * caps.input_price_per_mtok
            + self.output_tokens * caps.output_price_per_mtok
            + self.cache_read_tokens * caps.cache_read_price_per_mtok
            # La escritura en cache cuesta 1,25 veces la entrada.
            + self.cache_write_tokens * caps.input_price_per_mtok * 1.25
        ) / 1_000_000
        return round(usd * USD_TO_EUR, 6)


@dataclass(slots=True)
class LLMResponse:
    text: str
    parsed: dict[str, Any] | None
    model: str
    provider: str
    usage: LLMUsage
    latency_ms: int
    cost_eur: float
    stop_reason: str | None = None
    thinking: str | None = None
    attempts: int = 1
    degraded: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Contrato mínimo. Cambiar de proveedor no debe tocar nada más."""

    name: str = "abstract"

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 4096,
        effort: Effort = "high",
        json_schema: dict[str, Any] | None = None,
        thinking: bool = True,
    ) -> LLMResponse: ...

    def health(self) -> dict[str, Any]:
        return {"provider": self.name, "status": "unknown"}


# ─────────────────────────────────────────────────────────────────────────────
# Proveedor real
# ─────────────────────────────────────────────────────────────────────────────


class AnthropicProvider(LLMProvider):
    """Cliente de la API de Anthropic.

    Reintentos: el SDK ya reintenta 408/409/429 y 5xx con espera exponencial.
    Se le pasa `max_retries` y **no** se envuelve en un bucle propio, que
    multiplicaría los intentos reales sin que nadie lo advirtiera. Lo que sí se
    añade encima es la traducción a `ProviderUnavailableError`, para que la
    aplicación distinga "el proveedor no responde" de "el proveedor respondió
    algo inválido".
    """

    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        import anthropic  # importación diferida: sin credencial no hace falta

        self._sdk = anthropic
        self._client = anthropic.Anthropic(
            api_key=api_key or settings.anthropic_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    def _build_request(
        self,
        *,
        system: str,
        user: str,
        caps: ModelCapabilities,
        max_tokens: int,
        effort: Effort,
        json_schema: dict[str, Any] | None,
        thinking: bool,
    ) -> dict[str, Any]:
        """Construye la petición a partir de las capacidades declaradas.

        Aquí es donde la tabla de capacidades deja de ser documentación y pasa
        a tener efecto. Nunca se envían parámetros de muestreo: `temperature`,
        `top_p` y `top_k` se rechazan con 400 en los modelos actuales.
        """
        request: dict[str, Any] = {
            "model": caps.model_id,
            "max_tokens": min(max_tokens, caps.max_output_tokens),
            # El prompt de sistema va como bloque con `cache_control` para que
            # se cachee entre peticiones. Por debajo del prefijo mínimo del
            # modelo no se cachea y tampoco falla: simplemente no ahorra.
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user}],
        }

        output_config: dict[str, Any] = {}

        if caps.supports_effort:
            output_config["effort"] = effort

        if json_schema is not None and caps.supports_structured_output:
            # Salida estructurada del propio API: el modelo no puede devolver
            # algo que no valide contra el esquema. Es la primera de las tres
            # capas que garantizan la salida estructurada del harness; las
            # otras dos son la validación local y el reintento de reparación.
            output_config["format"] = {"type": "json_schema", "schema": json_schema}

        if output_config:
            request["output_config"] = output_config

        if caps.supports_adaptive_thinking:
            # Explícito en los dos sentidos. Omitirlo desactiva el pensamiento
            # en Opus 4.8 y lo activa en Sonnet 5: dejarlo implícito haría que
            # el comportamiento dependiera del modelo configurado.
            request["thinking"] = (
                {"type": "adaptive", "display": "summarized"}
                if thinking
                else {"type": "disabled"}
            )

        return request

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 4096,
        effort: Effort = "high",
        json_schema: dict[str, Any] | None = None,
        thinking: bool = True,
    ) -> LLMResponse:
        caps = capabilities_for(model)
        request = self._build_request(
            system=system,
            user=user,
            caps=caps,
            max_tokens=max_tokens,
            effort=effort,
            json_schema=json_schema,
            thinking=thinking,
        )

        started = time.perf_counter()
        try:
            message = self._client.messages.create(**request)
        except (
            self._sdk.APIConnectionError,
            self._sdk.RateLimitError,
            self._sdk.InternalServerError,
        ) as exc:
            # Fallos transitorios: el SDK ya agotó sus reintentos.
            log.warning(
                "llm_provider_unavailable",
                model=model,
                error=type(exc).__name__,
                retries_exhausted=settings.llm_max_retries,
            )
            raise ProviderUnavailableError(
                details={"model": model, "error_type": type(exc).__name__}
            ) from exc
        except self._sdk.APIStatusError as exc:
            # 4xx que no son reintentables: petición mal construida. Es un
            # error nuestro, no del proveedor, y debe verse como tal.
            log.error(
                "llm_request_rejected",
                model=model,
                status=exc.status_code,
                error_type=getattr(exc, "type", None),
            )
            raise

        latency_ms = int((time.perf_counter() - started) * 1000)

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "thinking":
                thinking_parts.append(block.thinking)

        text = "".join(text_parts)

        usage = LLMUsage(
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            cache_read_tokens=getattr(message.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=(
                getattr(message.usage, "cache_creation_input_tokens", 0) or 0
            ),
            thinking_tokens=getattr(
                getattr(message.usage, "output_tokens_details", None),
                "thinking_tokens",
                0,
            )
            or 0,
        )

        parsed: dict[str, Any] | None = None
        if json_schema is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                # No se lanza: el harness tiene un paso de reparación. Que la
                # salida no valide es un estado previsto, no una excepción.
                log.warning("llm_output_not_json", model=model)

        return LLMResponse(
            text=text,
            parsed=parsed,
            model=caps.model_id,
            provider=self.name,
            usage=usage,
            latency_ms=latency_ms,
            cost_eur=usage.cost_eur(caps),
            stop_reason=message.stop_reason,
            thinking="\n".join(thinking_parts) or None,
            raw={"id": message.id, "stop_reason": message.stop_reason},
        )

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "status": "configured",
            "primary_model": settings.llm_primary_model,
            "verifier_model": settings.llm_verifier_model,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Proveedor mock
# ─────────────────────────────────────────────────────────────────────────────


class MockProvider(LLMProvider):
    """Proveedor determinista sin red.

    No es un stub de pruebas. Es lo que hace que:

      · La demostración completa se pueda ejecutar sin credencial ni coste.
      · Las evaluaciones sean reproducibles, requisito para comparar dos
        versiones de prompt: con un modelo real, dos ejecuciones de la misma
        suite dan números distintos y la comparación no significa nada.
      · La prueba 5 del Failure Lab (caída del proveedor) sea real. El
        interruptor `fail_next` provoca un fallo de verdad en el mismo punto
        del código donde fallaría el proveedor real, en lugar de simular la
        pantalla de error.

    La respuesta se deriva por hash de la entrada, de modo que la misma
    pregunta produce siempre la misma salida y preguntas distintas producen
    salidas distintas.
    """

    name = "mock"

    def __init__(self) -> None:
        # Contador de fallos programados para el Failure Lab.
        self.fail_next: int = 0
        self.latency_ms_range: tuple[int, int] = (180, 900)

    def _seed(self, *parts: str) -> random.Random:
        digest = hashlib.blake2b("|".join(parts).encode(), digest_size=8).digest()
        return random.Random(int.from_bytes(digest, "big"))  # noqa: S311

    def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 4096,
        effort: Effort = "high",
        json_schema: dict[str, Any] | None = None,
        thinking: bool = True,
    ) -> LLMResponse:
        caps = capabilities_for(model)

        if self.fail_next > 0:
            self.fail_next -= 1
            log.warning("llm_mock_injected_failure", model=model)
            raise ProviderUnavailableError(
                details={"model": model, "error_type": "InjectedFailure"}
            )

        rng = self._seed(system, user, model)
        latency_ms = rng.randint(*self.latency_ms_range)

        payload = build_mock_payload(system=system, user=user, schema=json_schema, rng=rng)
        text = json.dumps(payload, ensure_ascii=False, indent=2)

        # Recuento aproximado por longitud. No pretende ser exacto: existe para
        # que las métricas de coste de la demostración tengan órdenes de
        # magnitud creíbles en lugar de ceros.
        usage = LLMUsage(
            input_tokens=max(1, len(system) + len(user)) // 4,
            output_tokens=max(1, len(text)) // 4,
        )

        return LLMResponse(
            text=text,
            parsed=payload,
            model=caps.model_id,
            provider=self.name,
            usage=usage,
            latency_ms=latency_ms,
            cost_eur=usage.cost_eur(caps),
            stop_reason="end_turn",
            thinking=None,
            raw={"deterministic": True},
        )

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "status": "deterministic",
            "note": "sin credencial: la demostración funciona sin red ni coste",
        }


def build_mock_payload(
    *,
    system: str,
    user: str,
    schema: dict[str, Any] | None,
    rng: random.Random,
) -> dict[str, Any]:
    """Genera una respuesta que valida contra el esquema pedido.

    Se construye recorriendo el propio esquema en lugar de tener una plantilla
    por tarea. Así, cuando se añade un campo a un esquema de salida, el
    proveedor mock lo produce sin tocarlo, y no aparece el fallo de "en mock
    funciona y con el modelo real falta un campo".
    """
    if schema is None:
        return {"answer": "Respuesta determinista del proveedor simulado."}
    return _instantiate(schema, rng, depth=0)


_BOUNDS_RE = re.compile(r"rango permitido:\s*(-?[\d.]+|inf)\.\.(-?[\d.]+|inf)")


def _bounds_from_description(description: str) -> tuple[float | None, float | None]:
    match = _BOUNDS_RE.search(description)
    if not match:
        return None, None

    def parse(bound: str) -> float | None:
        return None if bound == "inf" else float(bound)

    return parse(match.group(1)), parse(match.group(2))


def _instantiate(schema: dict[str, Any], rng: random.Random, depth: int) -> Any:
    if depth > 6:
        return None

    # Un campo anulable se instancia como null, no como texto.
    #
    # Encontrado al probar el round-trip: el mock rellenaba `blocked_reason`
    # con una cadena, y el validador de coherencia —correctamente— concluía que
    # una respuesta bloqueada no puede declarar confianza y la forzaba a cero.
    # Toda salida del proveedor simulado aparecía bloqueada.
    if union := (schema.get("anyOf") or schema.get("oneOf")):
        if any(option.get("type") == "null" for option in union):
            return None
        return _instantiate(union[0], rng, depth + 1)

    schema_type = schema.get("type")

    if isinstance(schema_type, list):
        if "null" in schema_type:
            return None
        schema_type = schema_type[0]

    if schema_type == "object":
        result: dict[str, Any] = {}
        for key, subschema in (schema.get("properties") or {}).items():
            result[key] = _instantiate(subschema, rng, depth + 1)
        return result

    if schema_type == "array":
        items = schema.get("items") or {"type": "string"}
        count = 2 if depth < 2 else 1
        return [_instantiate(items, rng, depth + 1) for _ in range(count)]

    if enum := schema.get("enum"):
        return enum[0]

    if schema_type in ("integer", "number"):
        # El rango viaja plegado en la descripción, porque la salida
        # estructurada no admite `minimum`/`maximum`. El mock lo respeta por el
        # mismo motivo que debe respetarlo el modelo real: es la única señal
        # que hay.
        low, high = _bounds_from_description(schema.get("description", ""))
        value = 78 if schema_type == "integer" else 0.78
        if low is not None:
            value = max(value, low)
        if high is not None:
            value = min(value, high)
        return int(value) if schema_type == "integer" else float(value)

    if schema_type == "boolean":
        return False

    if schema_type == "null":
        return None

    return schema.get("description", "texto sintético del proveedor simulado")


# ─────────────────────────────────────────────────────────────────────────────
# Selección
# ─────────────────────────────────────────────────────────────────────────────

_provider: LLMProvider | None = None


def get_provider(*, force_mock: bool = False) -> LLMProvider:
    """Devuelve el proveedor activo.

    Sin `ANTHROPIC_API_KEY` se degrada al mock en lugar de fallar. Es
    deliberado: un proyecto de demostración que no arranca sin credencial no se
    puede enseñar. La degradación queda registrada en el arranque y es visible
    en `/readyz` y en la pantalla de estado del sistema, para que nadie confunda
    una demo con mock con una integración real.
    """
    global _provider
    if force_mock:
        return MockProvider()
    if _provider is None:
        if settings.llm_uses_real_provider:
            _provider = AnthropicProvider()
        else:
            log.info(
                "llm_provider_degraded_to_mock",
                reason="ANTHROPIC_API_KEY no configurada",
            )
            _provider = MockProvider()
    return _provider


def reset_provider() -> None:
    """Fuerza la reselección. Para pruebas y para el Failure Lab."""
    global _provider
    _provider = None
