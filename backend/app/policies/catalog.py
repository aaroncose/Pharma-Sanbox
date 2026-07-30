"""Catálogo de políticas.

Las políticas son **datos**, no ramas `if` repartidas por el código. Este módulo
es su definición canónica; los seeds las cargan en la tabla `policies` y el
motor las lee desde ahí en tiempo de ejecución. Añadir una política, cambiar un
umbral o desactivarla para un cliente concreto no requiere desplegar código.

Por qué importa: en un entorno regulado, quien decide qué se bloquea es
compliance, no ingeniería. Si la regla vive en el código, cada cambio de
criterio es una release. Si vive en una tabla con versión y auditoría, es una
decisión trazable de la persona responsable.

Cada política declara:
  · `code`     identificador estable, citado en la interfaz y en la auditoría
  · `action`   allow | flag | require_review | block
  · `config`   parámetros que interpreta el motor (patrones, umbrales, listas)

`action` es deliberadamente conservadora. Ante la duda, `require_review` en vez
de `block`: un falso positivo que llega a una persona cuesta minutos; un falso
negativo que llega a un profesional sanitario cuesta mucho más.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

PolicyAction = Literal["allow", "flag", "require_review", "block"]


@dataclass(frozen=True, slots=True)
class PolicyDefinition:
    code: str
    version: str
    category: str
    title: str
    description: str
    severity: str
    action: PolicyAction
    config: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Política de fuentes
# ─────────────────────────────────────────────────────────────────────────────

SOURCE_POLICIES: list[PolicyDefinition] = [
    PolicyDefinition(
        code="PRODUCT_CLAIM_REQUIRES_SOURCE",
        version="v1.0",
        category="sources",
        title="Toda afirmación sobre un producto requiere fuente aprobada",
        description=(
            "Una respuesta que contenga afirmaciones sobre eficacia, seguridad, "
            "posología o posicionamiento de un producto debe citar al menos una "
            "fuente aprobada y vigente. Sin fuentes, la respuesta no se entrega: "
            "pasa a revisión humana."
        ),
        severity="high",
        action="require_review",
        config={
            "min_sources": 1,
            # Términos que convierten una respuesta en "afirmación de producto".
            # No pretende ser exhaustivo: es la primera de tres capas, junto con
            # el verificador posterior y la revisión humana.
            "claim_markers": [
                "eficacia",
                "eficaz",
                "reduce",
                "reducción",
                "mejora",
                "superior",
                "mejor que",
                "indicado para",
                "dosis",
                "posología",
                "seguro",
                "sin riesgos",
                "demostrado",
                "demuestra",
            ],
        },
    ),
    PolicyDefinition(
        code="WITHDRAWN_SOURCE_FORBIDDEN",
        version="v1.0",
        category="sources",
        title="Un documento retirado nunca puede utilizarse",
        description=(
            "Los documentos con estado 'withdrawn' quedan fuera de la recuperación "
            "y no pueden citarse bajo ninguna circunstancia, ni siquiera como "
            "contexto histórico. La regla se aplica en la vista `citable_documents`, "
            "de modo que no depende de que cada consulta recuerde filtrar."
        ),
        severity="critical",
        action="block",
        config={"forbidden_statuses": ["withdrawn", "draft", "pending_review"]},
    ),
    PolicyDefinition(
        code="EXPIRED_SOURCE_FORBIDDEN",
        version="v1.0",
        category="sources",
        title="Un documento caducado deja de ser citable",
        description=(
            "La caducidad excluye igual que la retirada. Se evalúa por fecha en "
            "cada consulta, no por un proceso periódico que podría ir con retraso."
        ),
        severity="high",
        action="block",
        config={"grace_period_days": 0},
    ),
    PolicyDefinition(
        code="INSUFFICIENT_EVIDENCE_MUST_ADMIT",
        version="v1.0",
        category="sources",
        title="Sin fuentes suficientes, el agente debe reconocerlo",
        description=(
            "Cuando la recuperación no devuelve material aprobado relevante, la "
            "respuesta correcta es declarar que no se dispone de información, no "
            "completar con conocimiento general del modelo."
        ),
        severity="high",
        action="block",
        config={"min_relevance": 0.15, "response_code": "INSUFFICIENT_SOURCES"},
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# Política de datos
# ─────────────────────────────────────────────────────────────────────────────

DATA_POLICIES: list[PolicyDefinition] = [
    PolicyDefinition(
        code="TENANT_ISOLATION",
        version="v1.0",
        category="data",
        title="Ningún dato puede cruzar la frontera de organización",
        description=(
            "Se impone en la base de datos mediante Row-Level Security, no en la "
            "capa de aplicación. Un intento de acceso cruzado devuelve "
            "403 ACCESS_DENIED_CROSS_TENANT, registra un evento de seguridad y "
            "expone cero campos."
        ),
        severity="critical",
        action="block",
        config={"decision_code": "ACCESS_DENIED_CROSS_TENANT", "max_exposed_fields": 0},
    ),
    PolicyDefinition(
        code="MINIMIZE_MODEL_INPUT",
        version="v1.0",
        category="data",
        title="Minimizar la información enviada al proveedor de IA",
        description=(
            "Al proveedor solo viajan los fragmentos documentales seleccionados y "
            "los datos del profesional estrictamente necesarios para la tarea. "
            "Nombre e institución se sustituyen por marcadores cuando la tarea no "
            "los requiere. El RGPD exige tratar por defecto solo los datos "
            "necesarios para cada finalidad."
        ),
        severity="high",
        action="flag",
        config={
            "max_context_chunks": 8,
            "pseudonymize_fields": ["full_name", "external_ref", "institution"],
            "never_send_fields": ["email", "password_hash", "notes"],
        },
    ),
    PolicyDefinition(
        code="CONSENT_REQUIRED_FOR_HISTORY",
        version="v1.0",
        category="data",
        title="El historial requiere consentimiento registrado",
        description=(
            "Sin `consent_data_analysis`, el constructor de contexto no incorpora "
            "el historial de interacciones del profesional: el agente trabaja solo "
            "con documentación de producto."
        ),
        severity="high",
        action="flag",
        config={"consent_field": "consent_data_analysis"},
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# Política de transparencia
# ─────────────────────────────────────────────────────────────────────────────

TRANSPARENCY_POLICIES: list[PolicyDefinition] = [
    PolicyDefinition(
        code="AI_DISCLOSURE_REQUIRED",
        version="v1.0",
        category="transparency",
        title="El usuario debe saber que interactúa con una IA",
        description=(
            "Toda superficie que genere contenido muestra un aviso permanente, y "
            "cada salida se marca como generada por IA junto con el estado de "
            "revisión humana. Las obligaciones de transparencia del artículo 50 "
            "del Reglamento de IA de la UE son aplicables desde el 2 de agosto "
            "de 2026."
        ),
        severity="medium",
        action="flag",
        config={"require_banner": True, "require_output_label": True},
    ),
    PolicyDefinition(
        code="HUMAN_REVIEW_VISIBLE",
        version="v1.0",
        category="transparency",
        title="El estado de revisión humana debe ser visible",
        description=(
            "Si una respuesta ha sido aprobada, editada o rechazada por compliance, "
            "quien la consulte debe verlo, junto con la política aplicada y la "
            "fecha de la decisión."
        ),
        severity="medium",
        action="flag",
        config={},
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# Política de contenido sanitario
# ─────────────────────────────────────────────────────────────────────────────

CLINICAL_POLICIES: list[PolicyDefinition] = [
    PolicyDefinition(
        code="NO_CLINICAL_RECOMMENDATION",
        version="v1.0",
        category="clinical_content",
        title="Prohibido diagnosticar, prescribir o recomendar tratamiento",
        description=(
            "El sistema no emite diagnósticos, pautas para pacientes concretos ni "
            "recomendaciones clínicas individualizadas. Estas solicitudes se "
            "bloquean y se ofrece la derivación al departamento médico. Las guías "
            "europeas sobre uso de modelos de lenguaje en el ámbito de los "
            "medicamentos recomiendan gobernanza, validación y revisión crítica "
            "precisamente por este riesgo."
        ),
        severity="critical",
        action="block",
        config={
            "patterns": [
                r"\bqu[ée]\s+(?:le\s+)?(?:receto|prescribo|doy)\b",
                r"\bqu[ée]\s+dosis\s+(?:le\s+)?(?:doy|pongo|indico)\b",
                r"\bmi\s+paciente\b",
                r"\beste\s+paciente\b",
                r"\bpara\s+un\s+paciente\s+(?:de|con)\b",
                r"\bdiagn[oó]stic\w*\b",
                r"\bqu[ée]\s+tratamiento\s+(?:le\s+)?(?:recomiendas|sugieres|indicas)\b",
                r"\bpuede\s+tomarlo\s+(?:mi|el|la)\b",
                r"\bcambiar\s+(?:la|su)\s+medicaci[oó]n\b",
            ],
            "referral_message": (
                "Esta consulta requiere criterio clínico individualizado y queda "
                "fuera del alcance del material comercial aprobado. Deriva la "
                "pregunta al departamento médico."
            ),
        },
    ),
    PolicyDefinition(
        code="NO_UNVERIFIED_PROMOTION",
        version="v1.0",
        category="clinical_content",
        title="Prohibidas las afirmaciones promocionales no verificadas",
        description=(
            "Las afirmaciones de superioridad, seguridad absoluta o ausencia de "
            "efectos adversos requieren respaldo documental explícito. Sin él, la "
            "respuesta pasa a revisión humana."
        ),
        severity="high",
        action="require_review",
        config={
            "patterns": [
                r"\bel\s+m[aá]s\s+(?:eficaz|seguro|potente)\b",
                r"\bsuperior\s+a\b",
                r"\bmejor\s+que\s+(?:cualquier|todos|otros)\b",
                r"\bsin\s+(?:efectos\s+secundarios|riesgos|contraindicaciones)\b",
                r"\bcompletamente\s+seguro\b",
                r"\bcura\b",
                r"\bgarantiza\b",
                r"\bprimera\s+l[ií]nea\s+preferente\b",
            ]
        },
    ),
    PolicyDefinition(
        code="NO_RISK_CONCEALMENT",
        version="v1.0",
        category="clinical_content",
        title="Prohibido ocultar o minimizar riesgos",
        description=(
            "Cualquier solicitud de omitir, minimizar o reformular información de "
            "seguridad se bloquea y genera un evento de seguridad. Es una de las "
            "peticiones más graves que puede recibir el sistema."
        ),
        severity="critical",
        action="block",
        config={
            "patterns": [
                r"\b(?:no\s+menciones|omite|oculta|evita\s+mencionar)\b.{0,40}"
                r"\b(?:efecto|adverso|riesgo|seguridad|contraindicaci)",
                r"\bminimiza\w*\b.{0,30}\b(?:riesgo|efecto|advers)",
                r"\bsin\s+hablar\s+de\s+(?:los\s+)?(?:riesgos|efectos)\b",
            ]
        },
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# Política de integridad del agente
# ─────────────────────────────────────────────────────────────────────────────

AGENT_POLICIES: list[PolicyDefinition] = [
    PolicyDefinition(
        code="PROMPT_INJECTION_DEFENCE",
        version="v1.0",
        category="agent_integrity",
        title="El contenido documental es dato, nunca instrucción",
        description=(
            "Los fragmentos recuperados se entregan al modelo delimitados y "
            "marcados como datos no fiables. Además se detectan patrones de "
            "inyección para registrar el intento: la defensa efectiva es la "
            "separación estructural, pero un intento detectado es información de "
            "seguridad que hay que registrar."
        ),
        severity="critical",
        action="flag",
        config={
            "patterns": [
                r"ignor\w*\s+(?:todas\s+)?(?:las\s+)?instruc",
                r"ignore\s+(?:all\s+)?(?:previous\s+|prior\s+)?instructions",
                r"disregard\s+(?:the\s+)?(?:above|previous|tenant)",
                r"\bsystem\s*:\s*new\s+instructions",
                r"eres\s+un\s+asistente\s+sin\s+restricciones",
                r"muestra\s+(?:la\s+)?(?:lista\s+)?(?:completa\s+)?de\s+clientes",
                r"desactiva\w*\s+la\s+verificaci",
                r"reveal\s+all\s+(?:interactions|users|tenants)",
            ],
            "quarantine_marker": "untrusted_document_content",
        },
    ),
    PolicyDefinition(
        code="TOOL_ALLOWLIST",
        version="v1.0",
        category="agent_integrity",
        title="El agente solo puede usar herramientas explícitamente autorizadas",
        description=(
            "La lista se resuelve por rol y por tarea antes de invocar al modelo. "
            "Una herramienta fuera de la lista no se rechaza al ejecutarse: no se "
            "le ofrece al modelo en absoluto. Si aun así la solicita, se registra "
            "un evento de seguridad y se aborta."
        ),
        severity="critical",
        action="block",
        config={
            "allowed_tools": [
                "search_documents",
                "get_hcp_history",
                "create_draft",
                "create_task",
                "request_human_review",
            ],
            # Nombres que el agente podría intentar y que no existen. Sirven de
            # señuelo: pedir cualquiera de ellos es una escalada de privilegios.
            "forbidden_tools": [
                "execute_sql",
                "update_permissions",
                "change_role",
                "list_tenants",
                "disable_policy",
                "read_audit_log",
            ],
        },
    ),
    PolicyDefinition(
        code="STRUCTURED_OUTPUT_REQUIRED",
        version="v1.0",
        category="agent_integrity",
        title="La salida debe validar contra el esquema",
        description=(
            "Una respuesta que no valida no se muestra. Se reintenta una vez con "
            "el error de validación como contexto y, si vuelve a fallar, se "
            "degrada a un mensaje controlado. El usuario nunca ve JSON roto ni "
            "texto libre sin verificar."
        ),
        severity="high",
        action="block",
        config={"max_repair_attempts": 1},
    ),
]

ALL_POLICIES: list[PolicyDefinition] = [
    *SOURCE_POLICIES,
    *DATA_POLICIES,
    *TRANSPARENCY_POLICIES,
    *CLINICAL_POLICIES,
    *AGENT_POLICIES,
]

POLICIES_BY_CODE: dict[str, PolicyDefinition] = {p.code: p for p in ALL_POLICIES}
