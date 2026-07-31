-- ═════════════════════════════════════════════════════════════════════════════
-- Pharma Commercial AI Sandbox — esquema y políticas de aislamiento
--
-- Se ejecuta con el rol propietario (`pharma_owner`). La API se conecta con
-- `pharma_app`, que no es propietario ni tiene BYPASSRLS y por tanto queda
-- sujeto a todas las políticas de este fichero.
--
-- Convenciones
-- ────────────
--  · Toda tabla con datos de cliente lleva `tenant_id NOT NULL`.
--  · Toda tabla con `tenant_id` tiene RLS habilitada y una política que compara
--    contra `current_setting('app.tenant_id', true)`.
--  · Las claves foráneas dentro de un tenant son compuestas `(tenant_id, id)`
--    siempre que sea posible. Esto impide, a nivel de motor, crear una fila que
--    referencie un recurso de otro tenant: un fallo de lógica no puede coser
--    datos de dos clientes.
--  · Los identificadores son UUID v4 generados por la base de datos.
--  · Nada se borra físicamente salvo por el proceso de retención: hay
--    `deleted_at` para el borrado lógico.
-- ═════════════════════════════════════════════════════════════════════════════

-- Las extensiones `vector` y `pgcrypto` viven en el esquema `extensions` y las
-- instala `scripts/bootstrap-db.sql` con un superusuario, una sola vez.
-- No se crean aquí a propósito: este fichero debe poder aplicarse íntegro con
-- un rol sin privilegios, y `make reset-db` no debe exigir un superusuario.

-- ─────────────────────────────────────────────────────────────────────────────
-- Tipos
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TYPE user_role AS ENUM (
    'platform_superadmin',  -- gestiona la plataforma, no lee datos comerciales
    'org_admin',            -- administra su organización
    'compliance_officer',   -- aprueba documentos y revisa contenido generado
    'sales_rep',            -- comercial
    'auditor'               -- solo lectura sobre logs y decisiones
);

CREATE TYPE user_status AS ENUM ('active', 'suspended', 'deleted');

CREATE TYPE tenant_status AS ENUM ('active', 'suspended');

CREATE TYPE document_status AS ENUM (
    'draft',            -- en elaboración; nunca visible para el agente
    'pending_review',   -- esperando decisión de compliance
    'approved',         -- única categoría que el agente puede citar
    'expired',          -- caducado por fecha; deja de ser citable automáticamente
    'withdrawn'         -- retirado explícitamente; nunca vuelve a ser citable
);

CREATE TYPE confidentiality_level AS ENUM ('public', 'internal', 'restricted');

CREATE TYPE risk_level AS ENUM ('low', 'medium', 'high', 'critical');

CREATE TYPE review_status AS ENUM (
    'pending', 'approved', 'rejected', 'edited', 'regeneration_requested'
);

CREATE TYPE review_subject AS ENUM (
    'briefing', 'chat_answer', 'meeting_summary', 'simulation_feedback', 'document'
);

CREATE TYPE audit_outcome AS ENUM ('success', 'denied', 'blocked', 'error');

CREATE TYPE task_status AS ENUM ('open', 'done', 'cancelled');

CREATE TYPE task_priority AS ENUM ('low', 'medium', 'high');

CREATE TYPE interaction_channel AS ENUM ('in_person', 'video_call', 'phone', 'email', 'congress');

-- ─────────────────────────────────────────────────────────────────────────────
-- Nivel de plataforma
-- ─────────────────────────────────────────────────────────────────────────────

-- `tenants` es la única tabla sin `tenant_id`: es el catálogo de organizaciones.
-- Su política deja ver a cada usuario únicamente su propia organización; el
-- superadministrador de plataforma las ve todas, porque su función es
-- precisamente administrarlas.
CREATE TABLE tenants (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL UNIQUE,
    name        text NOT NULL,
    status      tenant_status NOT NULL DEFAULT 'active',
    region      text NOT NULL DEFAULT 'EU',
    -- Retención configurable por cliente: el RGPD no fija un plazo único, lo
    -- ata a la finalidad. Se guarda como dato, no como constante en el código.
    audit_retention_days int NOT NULL DEFAULT 365 CHECK (audit_retention_days BETWEEN 30 AND 3650),
    created_at  timestamptz NOT NULL DEFAULT now(),
    deleted_at  timestamptz
);

-- Prompts versionados. Son de plataforma, no de cliente: la misma versión de
-- prompt se evalúa contra todos los tenants para que las métricas sean
-- comparables.
CREATE TABLE prompt_versions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,              -- p. ej. 'briefing'
    version     text NOT NULL,              -- p. ej. 'v1.3'
    template    text NOT NULL,
    notes       text,
    is_active   boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

-- Un único prompt activo por nombre. Evita la ambigüedad de "¿qué versión
-- estaba corriendo cuando se generó este briefing?".
CREATE UNIQUE INDEX prompt_versions_one_active
    ON prompt_versions (name) WHERE is_active;

-- ─────────────────────────────────────────────────────────────────────────────
-- Identidad y permisos
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    -- El correo se normaliza a minúsculas en la aplicación; la unicidad se
    -- garantiza con un índice sobre lower(email) más abajo.
    email           text NOT NULL,
    password_hash   text NOT NULL,
    full_name       text NOT NULL,
    role            user_role NOT NULL,
    status          user_status NOT NULL DEFAULT 'active',
    last_login_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    UNIQUE (tenant_id, id)
);

CREATE UNIQUE INDEX users_email_unique ON users (lower(email)) WHERE deleted_at IS NULL;
CREATE INDEX users_tenant_idx ON users (tenant_id);

CREATE TABLE products (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    code              text NOT NULL,
    name              text NOT NULL,
    therapeutic_area  text NOT NULL,
    description       text,
    is_active         boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code),
    UNIQUE (tenant_id, id)
);

-- Asignación de productos a comerciales. La clave foránea compuesta contra
-- `(tenant_id, id)` de ambos lados impide asignar a un usuario un producto de
-- otra organización, incluso si la capa de aplicación tuviera un fallo.
CREATE TABLE user_products (
    tenant_id   uuid NOT NULL,
    user_id     uuid NOT NULL,
    product_id  uuid NOT NULL,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, product_id),
    FOREIGN KEY (tenant_id, user_id)    REFERENCES users(tenant_id, id)    ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, product_id) REFERENCES products(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX user_products_tenant_idx ON user_products (tenant_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Profesionales sanitarios
-- ─────────────────────────────────────────────────────────────────────────────

-- Todos los datos son sintéticos. Aun así el modelo se diseña como si no lo
-- fueran: minimización de datos, sin campos que no tengan una finalidad
-- explícita en el producto, y marca de consentimiento para el tratamiento.
CREATE TABLE healthcare_professionals (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    external_ref  text NOT NULL,                    -- identificador de CRM, sintético
    full_name     text NOT NULL,
    specialty     text NOT NULL,
    institution   text NOT NULL,
    city          text NOT NULL,
    -- Sin consentimiento, el agente no puede incorporar historial de
    -- interacciones al contexto: solo documentación de producto.
    consent_contact       boolean NOT NULL DEFAULT true,
    consent_data_analysis boolean NOT NULL DEFAULT false,
    notes         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    deleted_at    timestamptz,
    UNIQUE (tenant_id, external_ref),
    UNIQUE (tenant_id, id)
);

CREATE INDEX hcp_tenant_idx ON healthcare_professionals (tenant_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Biblioteca documental
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE documents (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    product_id       uuid,
    title            text NOT NULL,
    doc_type         text NOT NULL,   -- ficha_producto, faq, estudio, politica, material, seguridad
    status           document_status NOT NULL DEFAULT 'draft',
    version          text NOT NULL DEFAULT 'v1.0',
    confidentiality  confidentiality_level NOT NULL DEFAULT 'internal',
    body             text NOT NULL,
    storage_uri      text,
    approved_at      timestamptz,
    approved_by      uuid,
    expires_at       timestamptz,
    withdrawn_at     timestamptz,
    withdrawn_reason text,
    created_by       uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz,

    FOREIGN KEY (tenant_id, product_id) REFERENCES products(tenant_id, id),
    FOREIGN KEY (tenant_id, approved_by) REFERENCES users(tenant_id, id),
    FOREIGN KEY (tenant_id, created_by)  REFERENCES users(tenant_id, id),
    UNIQUE (tenant_id, id),

    -- Un documento aprobado sin fecha de aprobación ni aprobador es un agujero
    -- de trazabilidad: la restricción lo hace imposible, no improbable.
    CONSTRAINT documents_approved_needs_provenance CHECK (
        status <> 'approved' OR (approved_at IS NOT NULL AND approved_by IS NOT NULL)
    ),
    CONSTRAINT documents_withdrawn_needs_reason CHECK (
        status <> 'withdrawn' OR withdrawn_at IS NOT NULL
    )
);

CREATE INDEX documents_tenant_idx  ON documents (tenant_id);
CREATE INDEX documents_product_idx ON documents (tenant_id, product_id);
CREATE INDEX documents_status_idx  ON documents (tenant_id, status);

-- Vista de lo que el agente puede citar. Existe para que la regla viva en un
-- único sitio: aprobado, no retirado, no borrado y no caducado a día de hoy.
-- Cualquier consulta del harness pasa por aquí; si mañana cambia la regla,
-- cambia en un punto.
CREATE VIEW citable_documents AS
SELECT *
  FROM documents
 WHERE status = 'approved'
   AND deleted_at IS NULL
   AND withdrawn_at IS NULL
   AND (expires_at IS NULL OR expires_at > now());

-- Fragmentos indexados para recuperación. Se guarda el vector y también el
-- vector de texto completo: la búsqueda es híbrida (semántica + léxica) y se
-- fusiona por rango recíproco. Solo con embeddings se pierden coincidencias
-- exactas de nombre de producto o código de estudio, que en este dominio son
-- justamente las que más importan.
CREATE TABLE document_chunks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL,
    document_id  uuid NOT NULL,
    ordinal      int  NOT NULL,
    section      text,
    content      text NOT NULL,
    embedding    vector(384),
    content_tsv  tsvector GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED,
    created_at   timestamptz NOT NULL DEFAULT now(),

    FOREIGN KEY (tenant_id, document_id) REFERENCES documents(tenant_id, id) ON DELETE CASCADE,
    UNIQUE (document_id, ordinal)
);

CREATE INDEX document_chunks_tenant_idx ON document_chunks (tenant_id);
CREATE INDEX document_chunks_tsv_idx    ON document_chunks USING gin (content_tsv);
-- HNSW sobre distancia coseno. Con el volumen de la demo un escaneo secuencial
-- bastaría; el índice está para que la elección sea la correcta a escala real.
CREATE INDEX document_chunks_embedding_idx
    ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- ─────────────────────────────────────────────────────────────────────────────
-- Actividad comercial
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE interactions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    hcp_id       uuid NOT NULL,
    user_id      uuid NOT NULL,
    product_id   uuid,
    occurred_at  timestamptz NOT NULL,
    channel      interaction_channel NOT NULL DEFAULT 'in_person',
    topics       text[] NOT NULL DEFAULT '{}',
    summary      text NOT NULL,
    open_questions text[] NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz,

    FOREIGN KEY (tenant_id, hcp_id)     REFERENCES healthcare_professionals(tenant_id, id),
    FOREIGN KEY (tenant_id, user_id)    REFERENCES users(tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES products(tenant_id, id),
    UNIQUE (tenant_id, id)
);

CREATE INDEX interactions_tenant_idx ON interactions (tenant_id);
CREATE INDEX interactions_hcp_idx    ON interactions (tenant_id, hcp_id, occurred_at DESC);

CREATE TABLE tasks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    user_id      uuid NOT NULL,
    hcp_id       uuid,
    product_id   uuid,
    title        text NOT NULL,
    detail       text,
    status       task_status NOT NULL DEFAULT 'open',
    priority     task_priority NOT NULL DEFAULT 'medium',
    due_date     date,
    source_type  text,          -- briefing, meeting_summary, simulation
    source_id    uuid,
    created_at   timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,

    FOREIGN KEY (tenant_id, user_id)    REFERENCES users(tenant_id, id),
    FOREIGN KEY (tenant_id, hcp_id)     REFERENCES healthcare_professionals(tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES products(tenant_id, id),
    UNIQUE (tenant_id, id)
);

CREATE INDEX tasks_tenant_idx ON tasks (tenant_id, user_id, status);

-- ─────────────────────────────────────────────────────────────────────────────
-- Salidas del agente
-- ─────────────────────────────────────────────────────────────────────────────

-- Tabla común a todo lo que genera el agente. Cada fila conserva la
-- trazabilidad completa exigida por el módulo de auditoría: qué prompt, qué
-- modelo, qué fuentes, cuánto costó y cuánto tardó.
CREATE TABLE agent_outputs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    kind              review_subject NOT NULL,
    user_id           uuid NOT NULL,
    hcp_id            uuid,
    product_id        uuid,

    -- Cuerpo estructurado validado contra el esquema de salida del harness.
    payload           jsonb NOT NULL,
    answer_text       text,
    confidence        int  NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 100),
    risk              risk_level NOT NULL DEFAULT 'low',
    requires_human_review boolean NOT NULL DEFAULT false,
    blocked_reason    text,

    -- Trazabilidad
    trace_id          text NOT NULL,
    prompt_name       text NOT NULL,
    prompt_version    text NOT NULL,
    model             text NOT NULL,
    provider          text NOT NULL,
    latency_ms        int  NOT NULL DEFAULT 0,
    cost_eur          numeric(10, 6) NOT NULL DEFAULT 0,
    input_tokens      int NOT NULL DEFAULT 0,
    output_tokens     int NOT NULL DEFAULT 0,

    created_at        timestamptz NOT NULL DEFAULT now(),
    deleted_at        timestamptz,

    FOREIGN KEY (tenant_id, user_id)    REFERENCES users(tenant_id, id),
    FOREIGN KEY (tenant_id, hcp_id)     REFERENCES healthcare_professionals(tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES products(tenant_id, id),
    UNIQUE (tenant_id, id),

    -- Una salida bloqueada no puede presentarse como respuesta válida.
    CONSTRAINT agent_outputs_blocked_has_no_confidence CHECK (
        blocked_reason IS NULL OR confidence = 0
    )
);

CREATE INDEX agent_outputs_tenant_idx ON agent_outputs (tenant_id, created_at DESC);
CREATE INDEX agent_outputs_kind_idx   ON agent_outputs (tenant_id, kind, created_at DESC);
CREATE INDEX agent_outputs_trace_idx  ON agent_outputs (trace_id);

-- Fuentes citadas por cada salida. Se guarda la versión y el estado del
-- documento *en el momento de la cita*: si el documento se retira después, la
-- auditoría debe seguir mostrando lo que el agente vio entonces.
CREATE TABLE agent_output_sources (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL,
    agent_output_id   uuid NOT NULL,
    document_id       uuid NOT NULL,
    chunk_id          uuid,
    quoted_excerpt    text,
    document_version  text NOT NULL,
    document_status_at_use document_status NOT NULL,
    relevance         real,

    FOREIGN KEY (tenant_id, agent_output_id) REFERENCES agent_outputs(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, document_id)     REFERENCES documents(tenant_id, id)
);

CREATE INDEX agent_output_sources_tenant_idx ON agent_output_sources (tenant_id);
CREATE INDEX agent_output_sources_output_idx ON agent_output_sources (agent_output_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Simulador conversacional
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE simulations (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    user_id       uuid NOT NULL,
    hcp_id        uuid NOT NULL,
    product_id    uuid NOT NULL,
    scenario      text NOT NULL,
    objective     text NOT NULL,
    modality      text NOT NULL DEFAULT 'text' CHECK (modality IN ('text', 'voice')),
    started_at    timestamptz NOT NULL DEFAULT now(),
    ended_at      timestamptz,
    score         int CHECK (score BETWEEN 0 AND 100),
    feedback      jsonb,

    FOREIGN KEY (tenant_id, user_id)    REFERENCES users(tenant_id, id),
    FOREIGN KEY (tenant_id, hcp_id)     REFERENCES healthcare_professionals(tenant_id, id),
    FOREIGN KEY (tenant_id, product_id) REFERENCES products(tenant_id, id),
    UNIQUE (tenant_id, id)
);

CREATE INDEX simulations_tenant_idx ON simulations (tenant_id, user_id, started_at DESC);

CREATE TABLE simulation_turns (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL,
    simulation_id  uuid NOT NULL,
    ordinal        int  NOT NULL,
    speaker        text NOT NULL CHECK (speaker IN ('hcp', 'rep', 'system')),
    content        text NOT NULL,
    -- En modalidad de voz: milisegundos desde el inicio y si el turno fue
    -- interrumpido. Sin esto no se puede diagnosticar una conversación hablada.
    started_ms     int,
    duration_ms    int,
    was_interrupted boolean NOT NULL DEFAULT false,
    compliance_flag text,
    created_at     timestamptz NOT NULL DEFAULT now(),

    FOREIGN KEY (tenant_id, simulation_id) REFERENCES simulations(tenant_id, id) ON DELETE CASCADE,
    UNIQUE (simulation_id, ordinal)
);

CREATE INDEX simulation_turns_tenant_idx ON simulation_turns (tenant_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Políticas y revisión humana
-- ─────────────────────────────────────────────────────────────────────────────

-- Las políticas son datos, no código. `tenant_id` nulo significa política
-- global de plataforma; una fila con tenant la sobrescribe para ese cliente.
CREATE TABLE policies (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid REFERENCES tenants(id) ON DELETE CASCADE,
    code        text NOT NULL,          -- p. ej. PRODUCT_CLAIM_REQUIRES_SOURCE
    version     text NOT NULL DEFAULT 'v1.0',
    category    text NOT NULL,          -- sources, data, transparency, clinical_content
    title       text NOT NULL,
    description text NOT NULL,
    severity    risk_level NOT NULL DEFAULT 'medium',
    -- Acción cuando la política se dispara.
    action      text NOT NULL CHECK (action IN ('allow', 'flag', 'require_review', 'block')),
    -- Parámetros de la regla: patrones, umbrales, listas. El motor los
    -- interpreta; añadir una política no requiere desplegar código.
    config      jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_enabled  boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code, version)
);

CREATE INDEX policies_lookup_idx ON policies (code) WHERE is_enabled;

CREATE TABLE review_items (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    subject_type    review_subject NOT NULL,
    agent_output_id uuid,
    document_id     uuid,
    requested_by    uuid NOT NULL,
    reason          text NOT NULL,
    policy_code     text,
    priority        task_priority NOT NULL DEFAULT 'medium',

    status          review_status NOT NULL DEFAULT 'pending',
    original_content text NOT NULL,
    edited_content   text,
    decision_rationale text,
    decided_by      uuid,
    decided_at      timestamptz,

    created_at      timestamptz NOT NULL DEFAULT now(),

    FOREIGN KEY (tenant_id, requested_by)    REFERENCES users(tenant_id, id),
    FOREIGN KEY (tenant_id, decided_by)      REFERENCES users(tenant_id, id),
    FOREIGN KEY (tenant_id, agent_output_id) REFERENCES agent_outputs(tenant_id, id),
    FOREIGN KEY (tenant_id, document_id)     REFERENCES documents(tenant_id, id),
    UNIQUE (tenant_id, id),

    -- Ninguna decisión de compliance puede quedar sin motivo escrito. Es la
    -- diferencia entre supervisión humana real y un botón de aprobar.
    CONSTRAINT review_decision_needs_rationale CHECK (
        status = 'pending'
        OR (decided_by IS NOT NULL AND decided_at IS NOT NULL
            AND decision_rationale IS NOT NULL AND length(trim(decision_rationale)) >= 10)
    ),
    CONSTRAINT review_edited_needs_content CHECK (
        status <> 'edited' OR edited_content IS NOT NULL
    )
);

CREATE INDEX review_items_queue_idx ON review_items (tenant_id, status, created_at DESC);

-- Correcciones de compliance convertidas en ejemplos de evaluación.
-- Este es el ciclo de feedback: cada vez que una persona corrige al agente, el
-- sistema gana un caso de prueba que antes no tenía.
CREATE TABLE feedback_examples (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    review_item_id   uuid NOT NULL,
    original_answer  text NOT NULL,
    corrected_answer text,
    reason           text NOT NULL,
    policy_code      text,
    expected_behaviour text NOT NULL,
    -- Solo los ejemplos promovidos entran en la suite de evaluación. Que una
    -- corrección sea válida no la convierte automáticamente en caso de prueba.
    promoted_to_eval boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),

    FOREIGN KEY (tenant_id, review_item_id) REFERENCES review_items(tenant_id, id) ON DELETE CASCADE
);

CREATE INDEX feedback_examples_tenant_idx ON feedback_examples (tenant_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Auditoría
-- ─────────────────────────────────────────────────────────────────────────────

-- Append-only por política: hay un trigger que impide UPDATE y DELETE.
-- Un log de auditoría que se puede editar no es un log de auditoría.
CREATE TABLE audit_log (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nulo solo para eventos de plataforma anteriores a conocer el tenant
    -- (por ejemplo, un login fallido con un correo inexistente).
    tenant_id         uuid REFERENCES tenants(id) ON DELETE RESTRICT,
    trace_id          text NOT NULL,
    occurred_at       timestamptz NOT NULL DEFAULT now(),

    actor_user_id     uuid,
    actor_role        user_role,
    action            text NOT NULL,        -- p. ej. agent.briefing.generated
    outcome           audit_outcome NOT NULL,
    decision_code     text,                 -- p. ej. ACCESS_DENIED_CROSS_TENANT

    resource_type     text,
    resource_id       text,
    -- Cuando alguien intenta alcanzar un recurso de otro cliente, se registra a
    -- qué tenant pertenecía. Es el dato que convierte un 403 en un incidente
    -- investigable.
    resource_tenant_id uuid,

    policy_code       text,
    model             text,
    prompt_name       text,
    prompt_version    text,
    tools_called      jsonb NOT NULL DEFAULT '[]'::jsonb,
    documents_used    jsonb NOT NULL DEFAULT '[]'::jsonb,
    review_item_id    uuid,

    latency_ms        int,
    cost_eur          numeric(10, 6),
    -- Cuántos campos de datos salieron efectivamente hacia el cliente. En un
    -- acceso denegado debe ser 0, y el Failure Lab lo comprueba.
    exposed_field_count int NOT NULL DEFAULT 0,

    -- Metadatos de red redactados por defecto: se guarda un hash con sal para
    -- poder correlacionar sesiones sin almacenar la dirección.
    client_fingerprint text,
    detail            jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_log_tenant_time_idx ON audit_log (tenant_id, occurred_at DESC);
CREATE INDEX audit_log_trace_idx       ON audit_log (trace_id);
CREATE INDEX audit_log_action_idx      ON audit_log (tenant_id, action, occurred_at DESC);
CREATE INDEX audit_log_outcome_idx     ON audit_log (tenant_id, outcome) WHERE outcome <> 'success';

CREATE OR REPLACE FUNCTION audit_log_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log es append-only: % no está permitido', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();

CREATE TRIGGER audit_log_no_delete
    BEFORE DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();

-- Trazas del agente: una fila por llamada a herramienta o al modelo.
CREATE TABLE agent_traces (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid REFERENCES tenants(id) ON DELETE RESTRICT,
    trace_id      text NOT NULL,
    step          int  NOT NULL,
    step_type     text NOT NULL,   -- context_build, policy_check, tool_call, llm_call, verify
    name          text NOT NULL,
    status        text NOT NULL,   -- ok, blocked, error, retried
    input_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    output_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    latency_ms    int NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trace_id, step)
);

CREATE INDEX agent_traces_trace_idx  ON agent_traces (trace_id, step);
CREATE INDEX agent_traces_tenant_idx ON agent_traces (tenant_id, created_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- Evaluaciones
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE eval_datasets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL UNIQUE,
    name        text NOT NULL,
    description text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE eval_cases (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id   uuid NOT NULL REFERENCES eval_datasets(id) ON DELETE CASCADE,
    ref          text NOT NULL,
    category     text NOT NULL,  -- correctness, faithfulness, safety, isolation, policy, injection, tools
    input        jsonb NOT NULL,
    expectation  jsonb NOT NULL, -- qué debe cumplirse para considerarlo correcto
    notes        text,
    UNIQUE (dataset_id, ref)
);

CREATE TABLE eval_runs (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id     uuid NOT NULL REFERENCES eval_datasets(id) ON DELETE CASCADE,
    prompt_name    text NOT NULL,
    prompt_version text NOT NULL,
    model          text NOT NULL,
    provider       text NOT NULL,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    metrics        jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE eval_results (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       uuid NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
    case_id      uuid NOT NULL REFERENCES eval_cases(id) ON DELETE CASCADE,
    passed       boolean NOT NULL,
    score        real,
    actual       jsonb NOT NULL DEFAULT '{}'::jsonb,
    failure_note text,
    latency_ms   int NOT NULL DEFAULT 0,
    cost_eur     numeric(10, 6) NOT NULL DEFAULT 0,
    UNIQUE (run_id, case_id)
);

CREATE INDEX eval_results_run_idx ON eval_results (run_id, passed);

-- ─────────────────────────────────────────────────────────────────────────────
-- Failure Lab
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE failure_scenarios (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text NOT NULL UNIQUE,
    name        text NOT NULL,
    description text NOT NULL,
    expectation text NOT NULL,
    ordinal     int  NOT NULL
);

CREATE TABLE failure_runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scenario_id  uuid NOT NULL REFERENCES failure_scenarios(id) ON DELETE CASCADE,
    tenant_id    uuid REFERENCES tenants(id) ON DELETE SET NULL,
    executed_by  uuid,
    trace_id     text NOT NULL,
    passed       boolean NOT NULL,
    request      jsonb NOT NULL DEFAULT '{}'::jsonb,
    result       jsonb NOT NULL DEFAULT '{}'::jsonb,
    audit_log_id uuid,
    executed_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX failure_runs_scenario_idx ON failure_runs (scenario_id, executed_at DESC);

-- ═════════════════════════════════════════════════════════════════════════════
-- Row-Level Security
--
-- Aquí es donde el aislamiento entre clientes deja de depender del código de la
-- aplicación. Un endpoint que olvide filtrar por tenant no filtra datos: la
-- consulta devuelve cero filas.
--
-- Contrato de sesión: cada transacción de la API fija
--     app.tenant_id  ·  app.user_id  ·  app.role
-- mediante `set_config(..., true)`. Ver `app/db/session.py`.
--
-- Sobre FORCE ROW LEVEL SECURITY
-- ──────────────────────────────
-- No se activa deliberadamente. En PostgreSQL el propietario de una tabla evita
-- sus propias políticas salvo que se fuerce. Ese propietario (`pharma_owner`)
-- se usa exclusivamente para migraciones y carga de datos sintéticos, procesos
-- que corren fuera de línea y nunca atienden peticiones HTTP.
-- La API se conecta siempre con `pharma_app`, que no es propietario ni tiene
-- BYPASSRLS, y `assert_rls_enforced()` aborta el arranque si eso deja de ser
-- cierto. Alternativa descartada: forzar RLS y dar a los seeds una política de
-- excepción, que sería una puerta trasera permanente en el esquema a cambio de
-- una comodidad de desarrollo. Ver docs/adr/0002.
-- ═════════════════════════════════════════════════════════════════════════════

-- Funciones de contexto. Centralizan la lectura de las variables de sesión para
-- que las políticas se lean como reglas de negocio y no como SQL repetido.
-- STABLE, no IMMUTABLE: el valor cambia entre transacciones.

CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid AS $$
    SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION app_current_role() RETURNS text AS $$
    SELECT COALESCE(NULLIF(current_setting('app.role', true), ''), 'anonymous');
$$ LANGUAGE sql STABLE;

-- El superadministrador de plataforma administra organizaciones y modelos.
-- No es un comodín: las políticas de contenido comercial no lo mencionan, de
-- modo que no puede leer documentos, interacciones ni salidas del agente de
-- ningún cliente. Para eso existe el procedimiento extraordinario registrado.
CREATE OR REPLACE FUNCTION app_is_platform_admin() RETURNS boolean AS $$
    SELECT app_current_role() = 'platform_superadmin';
$$ LANGUAGE sql STABLE;

-- ── Catálogo de organizaciones ───────────────────────────────────────────────

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenants_self_or_platform ON tenants
    FOR SELECT
    USING (app_is_platform_admin() OR id = app_current_tenant());

-- Crear y desactivar organizaciones es exclusivo de la plataforma.
CREATE POLICY tenants_platform_write ON tenants
    FOR ALL
    USING (app_is_platform_admin())
    WITH CHECK (app_is_platform_admin());

-- ── Tablas de cliente ────────────────────────────────────────────────────────
--
-- Todas comparten la misma forma de política. Se genera en bucle para que
-- añadir una tabla nueva sin su política sea imposible por descuido: la lista
-- está en un único sitio y `assert_rls_coverage()` comprueba al arrancar que no
-- queda ninguna tabla con `tenant_id` desprotegida.

DO $$
DECLARE
    t text;
    tenant_tables text[] := ARRAY[
        'users', 'products', 'user_products', 'healthcare_professionals',
        'documents', 'document_chunks', 'interactions', 'tasks',
        'agent_outputs', 'agent_output_sources', 'simulations',
        'simulation_turns', 'review_items', 'feedback_examples'
    ];
BEGIN
    FOREACH t IN ARRAY tenant_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);

        -- USING filtra lo que se puede leer, actualizar y borrar.
        -- WITH CHECK filtra lo que se puede escribir: sin esta mitad, un
        -- usuario podría insertar filas marcadas con el tenant_id de otro
        -- cliente, contaminando sus datos sin llegar a leerlos nunca.
        EXECUTE format($p$
            CREATE POLICY %1$I_tenant_isolation ON %1$I
                FOR ALL
                USING (tenant_id = app_current_tenant())
                WITH CHECK (tenant_id = app_current_tenant())
        $p$, t);
    END LOOP;
END $$;

-- ── Políticas configurables ──────────────────────────────────────────────────
-- Una fila con `tenant_id` nulo es una política global de plataforma: todos los
-- clientes la ven, ninguno puede modificarla.

ALTER TABLE policies ENABLE ROW LEVEL SECURITY;

CREATE POLICY policies_read ON policies
    FOR SELECT
    USING (tenant_id IS NULL OR tenant_id = app_current_tenant());

CREATE POLICY policies_write_own ON policies
    FOR ALL
    USING (tenant_id = app_current_tenant())
    WITH CHECK (tenant_id = app_current_tenant());

-- ── Auditoría ────────────────────────────────────────────────────────────────
--
-- Lectura restringida al propio tenant. Escritura sin restricción de lectura
-- previa: el sistema debe poder registrar un intento de acceso cruzado incluso
-- cuando el actor no tiene derecho a ver nada del tenant afectado. Esa es
-- justamente la fila más importante del log.

ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY audit_log_read_own ON audit_log
    FOR SELECT
    USING (tenant_id = app_current_tenant());

CREATE POLICY audit_log_append ON audit_log
    FOR INSERT
    WITH CHECK (true);

ALTER TABLE agent_traces ENABLE ROW LEVEL SECURITY;

CREATE POLICY agent_traces_read_own ON agent_traces
    FOR SELECT
    USING (tenant_id = app_current_tenant());

CREATE POLICY agent_traces_append ON agent_traces
    FOR INSERT
    WITH CHECK (true);

-- ── Catálogos de plataforma ──────────────────────────────────────────────────
-- Prompts, evaluaciones y escenarios del Failure Lab son comunes: las métricas
-- solo son comparables si todos los clientes se evalúan contra el mismo
-- conjunto. Lectura para cualquier sesión autenticada, escritura solo desde la
-- plataforma.

DO $$
DECLARE
    t text;
    platform_tables text[] := ARRAY[
        'prompt_versions', 'failure_scenarios'
    ];
    -- Las tablas de evaluación admiten escritura también de compliance, que es
    -- quien tiene `eval.run` en la matriz de permisos. Dejarlas solo para
    -- plataforma haría que el permiso existiera en la aplicación y fallara en la
    -- base de datos, que es la peor combinación: la interfaz ofrece el botón y
    -- la operación revienta.
    --
    -- No son contenido de cliente: el conjunto es sintético y compartido, y por
    -- eso la lectura es abierta y no hay columna de tenant que aislar.
    quality_tables text[] := ARRAY[
        'eval_datasets', 'eval_cases', 'eval_runs', 'eval_results'
    ];
BEGIN
    FOREACH t IN ARRAY platform_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format($p$
            CREATE POLICY %1$I_read ON %1$I FOR SELECT USING (true)
        $p$, t);
        EXECUTE format($p$
            CREATE POLICY %1$I_platform_write ON %1$I
                FOR ALL USING (app_is_platform_admin())
                WITH CHECK (app_is_platform_admin())
        $p$, t);
    END LOOP;

    FOREACH t IN ARRAY quality_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format($p$
            CREATE POLICY %1$I_read ON %1$I FOR SELECT USING (true)
        $p$, t);
        EXECUTE format($p$
            CREATE POLICY %1$I_quality_write ON %1$I
                FOR ALL
                USING (app_is_platform_admin()
                       OR app_current_role() = 'compliance_officer')
                WITH CHECK (app_is_platform_admin()
                            OR app_current_role() = 'compliance_officer')
        $p$, t);
    END LOOP;
END $$;

-- Las ejecuciones del Failure Lab sí llevan tenant: cada cliente ve las suyas.
ALTER TABLE failure_runs ENABLE ROW LEVEL SECURITY;

CREATE POLICY failure_runs_own ON failure_runs
    FOR ALL
    USING (tenant_id = app_current_tenant() OR app_is_platform_admin())
    WITH CHECK (tenant_id = app_current_tenant() OR app_is_platform_admin());

-- ── Permisos del rol de aplicación ───────────────────────────────────────────
-- Se conceden explícitamente y no incluyen DDL. `pharma_app` no puede crear ni
-- alterar tablas, luego tampoco puede eliminar una política RLS.

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pharma_app;
GRANT SELECT ON citable_documents TO pharma_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pharma_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- El log de auditoría no admite UPDATE ni DELETE ni siquiera por permisos,
-- además del trigger. Defensa en dos capas.
REVOKE UPDATE, DELETE ON audit_log FROM pharma_app;

-- ── Verificación de cobertura ────────────────────────────────────────────────
--
-- Comprueba que no queda ninguna tabla con columna `tenant_id` sin RLS
-- habilitada. La llama `assert_rls_enforced()` al arrancar la API: el modo de
-- fallo que previene es añadir una tabla en una migración futura y olvidar su
-- política, que no rompe nada visible y expone datos entre clientes.

CREATE OR REPLACE FUNCTION assert_rls_coverage()
RETURNS TABLE (table_name text, rls_enabled boolean, policy_count bigint) AS $$
    SELECT c.relname::text,
           c.relrowsecurity,
           (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relkind = 'r'
       AND EXISTS (
             SELECT 1 FROM information_schema.columns col
              WHERE col.table_schema = 'public'
                AND col.table_name = c.relname
                AND col.column_name = 'tenant_id')
       AND (NOT c.relrowsecurity
            OR (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) = 0);
$$ LANGUAGE sql STABLE;

-- ─────────────────────────────────────────────────────────────────────────────
-- Atribución de recursos para auditoría
--
-- Problema: cuando alguien intenta alcanzar un recurso de otro cliente, el log
-- debe registrar contra qué organización iba el intento. Pero el rol de la API
-- no puede leer esa fila —para eso está RLS— y por tanto tampoco su tenant_id.
-- Sin ese dato, el evento dice "acceso denegado a un identificador" y no
-- "acceso denegado a un recurso de BioHealth", que es lo investigable.
--
-- Solución: una función SECURITY DEFINER que devuelve **exclusivamente** el
-- tenant propietario. No devuelve ninguna otra columna, así que no es un canal
-- de exfiltración: quien la llama ya conocía el identificador, y lo único que
-- obtiene es la confirmación de que pertenece a otro cliente.
--
-- La lista de tablas está fijada dentro de la función y se valida antes de
-- construir el SQL. Una SECURITY DEFINER que interpola un nombre de tabla
-- recibido de fuera sería una escalada de privilegios directa.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION audit_resource_owner(p_table text, p_id uuid)
RETURNS uuid AS $$
DECLARE
    allowed text[] := ARRAY[
        'documents', 'interactions', 'agent_outputs', 'review_items',
        'healthcare_professionals', 'products', 'tasks', 'simulations', 'users'
    ];
    owner uuid;
BEGIN
    IF NOT (p_table = ANY(allowed)) THEN
        RAISE EXCEPTION 'tabla no permitida para atribución: %', p_table
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    EXECUTE format('SELECT tenant_id FROM %I WHERE id = $1', p_table)
       INTO owner USING p_id;

    RETURN owner;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
   -- search_path fijo: sin esto, quien llame a la función podría anteponer un
   -- esquema propio con una tabla `documents` falsa y hacer que se ejecute con
   -- los privilegios del propietario.
   SET search_path = public, pg_temp;

REVOKE ALL ON FUNCTION audit_resource_owner(text, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION audit_resource_owner(text, uuid) TO pharma_app;

-- ─────────────────────────────────────────────────────────────────────────────
-- Búsqueda de credenciales
--
-- El inicio de sesión es la única operación que ocurre legítimamente antes de
-- saber a qué organización pertenece quien llama. RLS, correctamente, no deja
-- leer `users` sin un tenant fijado, así que hace falta una vía acotada.
--
-- Alternativa descartada: conectar el endpoint de login con el rol propietario.
-- Habría creado un camino permanente por el que la aplicación puede saltarse
-- RLS, y bastaría un error futuro para que ese camino se usara para otra cosa.
--
-- Esta función es lo más estrecho posible:
--   · Coincidencia exacta por correo normalizado. No admite patrones ni
--     comodines, luego no sirve para enumerar la base de usuarios.
--   · Devuelve una única fila y solo las columnas que el flujo necesita.
--   · No filtra por estado: devuelve también usuarios suspendidos, para que la
--     aplicación pueda distinguir "credenciales incorrectas" de "cuenta
--     desactivada" en la auditoría, respondiendo lo mismo hacia fuera.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION auth_lookup_user(p_email text)
RETURNS TABLE (
    id            uuid,
    tenant_id     uuid,
    email         text,
    full_name     text,
    role          user_role,
    status        user_status,
    password_hash text,
    tenant_slug   text,
    tenant_name   text,
    tenant_status tenant_status
) AS $$
    SELECT u.id, u.tenant_id, u.email, u.full_name, u.role, u.status,
           u.password_hash, t.slug, t.name, t.status
      FROM users u
      JOIN tenants t ON t.id = u.tenant_id
     WHERE lower(u.email) = lower(p_email)
       AND u.deleted_at IS NULL
     LIMIT 1;
$$ LANGUAGE sql STABLE SECURITY DEFINER
   SET search_path = public, pg_temp;

REVOKE ALL ON FUNCTION auth_lookup_user(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_lookup_user(text) TO pharma_app;

-- Registro del último acceso. Misma razón: ocurre durante el login, cuando la
-- sesión todavía no tiene tenant.
CREATE OR REPLACE FUNCTION auth_touch_last_login(p_user_id uuid)
RETURNS void AS $$
    UPDATE users SET last_login_at = now() WHERE id = p_user_id;
$$ LANGUAGE sql SECURITY DEFINER
   SET search_path = public, pg_temp;

REVOKE ALL ON FUNCTION auth_touch_last_login(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_touch_last_login(uuid) TO pharma_app;

-- Búsqueda por identificador, para la renovación de sesión.
--
-- Mismo problema que `auth_lookup_user`: la renovación ocurre antes de fijar el
-- tenant, porque el tenant se deriva del propio usuario que se está validando.
-- Sin esta función, RLS devuelve cero filas y la renovación rechaza sesiones
-- legítimas — un fallo que se manifiesta como "tu sesión ya no es válida" cada
-- treinta minutos y que es muy difícil de diagnosticar desde fuera.
--
-- No devuelve `password_hash`: la renovación no lo necesita.
CREATE OR REPLACE FUNCTION auth_lookup_user_by_id(p_user_id uuid)
RETURNS TABLE (
    id            uuid,
    tenant_id     uuid,
    email         text,
    full_name     text,
    role          user_role,
    status        user_status,
    tenant_slug   text,
    tenant_name   text,
    tenant_status tenant_status
) AS $$
    SELECT u.id, u.tenant_id, u.email, u.full_name, u.role, u.status,
           t.slug, t.name, t.status
      FROM users u
      JOIN tenants t ON t.id = u.tenant_id
     WHERE u.id = p_user_id
       AND u.deleted_at IS NULL;
$$ LANGUAGE sql STABLE SECURITY DEFINER
   SET search_path = public, pg_temp;

REVOKE ALL ON FUNCTION auth_lookup_user_by_id(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_lookup_user_by_id(uuid) TO pharma_app;
