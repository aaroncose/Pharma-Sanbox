import Link from "next/link";

import {
  Card,
  CardHeader,
  EmptyState,
  Mono,
  StatusBadge,
} from "@/components/ui";
import { api } from "@/lib/api";
import { guard } from "@/components/Guard";

export const dynamic = "force-dynamic";

type Step = {
  step: number;
  step_type: string;
  name: string;
  status: string;
  input_summary: Record<string, unknown>;
  output_summary: Record<string, unknown>;
  latency_ms: number;
};

type Trace = {
  trace_id: string;
  found: boolean;
  steps: Step[];
  events: {
    id: string;
    occurred_at: string;
    action: string;
    outcome: string;
    exposed_field_count: number;
    actor_name: string | null;
  }[];
  output: {
    id: string;
    kind: string;
    answer_text: string | null;
    confidence: number;
    blocked_reason: string | null;
    model: string;
    prompt_name: string;
    prompt_version: string;
    cost_eur: number;
    input_tokens: number;
    output_tokens: number;
  } | null;
  sources: {
    document_id: string;
    title: string;
    document_version: string;
    status_at_use: string;
    status_now: string;
    status_changed: boolean;
  }[];
  cites_changed_documents: boolean;
  total_latency_ms: number;
};

/**
 * Cada tipo de paso responde a una pregunta distinta cuando algo sale mal.
 * Etiquetarlos con lenguaje llano es lo que convierte la traza en diagnóstico
 * en vez de en un volcado.
 */
const STEP_LABELS: Record<string, { label: string; question: string }> = {
  policy_check: {
    label: "Política",
    question: "¿Debía llegar esto al modelo?",
  },
  retrieval: {
    label: "Recuperación",
    question: "¿Se encontró el material?",
  },
  llm_call: {
    label: "Modelo",
    question: "¿Qué generó?",
  },
  verify: {
    label: "Verificación",
    question: "¿Un segundo modelo pudo refutarlo?",
  },
  repair: {
    label: "Reparación",
    question: "¿La salida cumplió el esquema?",
  },
  tool_call: { label: "Herramienta", question: "¿Qué se invocó?" },
  context_build: { label: "Contexto", question: "¿Qué se le pasó?" },
};

const STATUS_TONE: Record<string, string> = {
  ok: "border-teal-500",
  allow: "border-teal-500",
  block: "border-red-500",
  error: "border-red-500",
  retrying: "border-amber-500",
  require_review: "border-amber-500",
  flag: "border-amber-500",
};

export default async function TracePage({
  params,
}: {
  params: Promise<{ traceId: string }>;
}) {
  const denied = await guard("trace.read");
  if (denied) return denied;

  const { traceId } = await params;
  const trace = await api<Trace>(`/api/v1/audit/trace/${traceId}`);

  return (
    <div className="space-y-6">
      <div>
        <Link href="/audit" className="text-[13px] text-blue-600 hover:underline">
          ← Auditoría
        </Link>
        <h2 className="text-[22px] font-semibold tracking-tight mt-2">
          Reconstrucción de una decisión
        </h2>
        <p className="text-[14px] text-slate-500 mt-1">
          Sin esto, cuando una respuesta sale mal solo se puede mirar el
          resultado y especular. Aquí se señala el paso concreto.
        </p>
        <Mono className="text-slate-500 mt-2 inline-block">{trace.trace_id}</Mono>
      </div>

      {!trace.found ? (
        <Card>
          <EmptyState
            title="No hay nada bajo ese identificador"
            description="O la traza pertenece a otra organización, o no existe. Las dos posibilidades se responden igual: un identificador de traza no es adivinable, así que el conjunto vacío no permite enumerar nada."
          />
        </Card>
      ) : (
        <>
          {trace.cites_changed_documents ? (
            <Card className="p-4 border-amber-500/40 bg-amber-50">
              <p className="text-[13px] font-medium text-slate-950">
                Esta salida citó material que después cambió de estado
              </p>
              <p className="text-[12px] text-slate-700 mt-1">
                Las fuentes conservan el estado que tenían al citarse. Sin esa
                copia congelada, esta diferencia no se podría ni formular: al
                consultarla, el documento ya aparecería retirado y parecería que
                el agente citó material inválido.
              </p>
            </Card>
          ) : null}

          <div className="grid lg:grid-cols-3 gap-6">
            <Card className="lg:col-span-2">
              <CardHeader
                title={`${trace.steps.length} pasos`}
                subtitle={`${trace.total_latency_ms} ms en total`}
              />
              {trace.steps.length === 0 ? (
                <EmptyState title="Sin pasos registrados" />
              ) : (
                <ol className="p-5 space-y-3">
                  {trace.steps.map((step) => {
                    const meta =
                      STEP_LABELS[step.step_type] ?? {
                        label: step.step_type,
                        question: "",
                      };
                    const tone = STATUS_TONE[step.status] ?? "border-slate-300";
                    const summary = {
                      ...step.input_summary,
                      ...step.output_summary,
                    };

                    return (
                      <li
                        key={step.step}
                        className={`border-l-2 ${tone} pl-4 py-1`}
                      >
                        <div className="flex items-baseline gap-2 flex-wrap">
                          <Mono className="text-slate-500">{step.step}</Mono>
                          <span className="text-[13px] font-medium text-slate-950">
                            {meta.label}
                          </span>
                          <Mono className="text-slate-500">{step.name}</Mono>
                          <span
                            className={`text-[12px] ml-auto ${
                              step.status === "block" || step.status === "error"
                                ? "text-red-500"
                                : "text-slate-500"
                            }`}
                          >
                            {step.status}
                            {step.latency_ms ? ` · ${step.latency_ms} ms` : ""}
                          </span>
                        </div>
                        {meta.question ? (
                          <p className="text-[12px] text-slate-500">
                            {meta.question}
                          </p>
                        ) : null}
                        {Object.keys(summary).length > 0 ? (
                          <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
                            {Object.entries(summary).map(([key, value]) => (
                              <div key={key} className="flex gap-1.5">
                                <dt className="text-[12px] text-slate-500">
                                  {key}
                                </dt>
                                <dd className="mono text-[12px] text-slate-700">
                                  {Array.isArray(value)
                                    ? value.length === 0
                                      ? "—"
                                      : value.length <= 2
                                        ? value.join(", ")
                                        : `${value.length} elementos`
                                    : String(value)}
                                </dd>
                              </div>
                            ))}
                          </dl>
                        ) : null}
                      </li>
                    );
                  })}
                </ol>
              )}
            </Card>

            <div className="space-y-6">
              {trace.output ? (
                <Card>
                  <CardHeader title="Salida producida" />
                  <dl className="p-5 space-y-3">
                    {[
                      ["Modelo", trace.output.model],
                      [
                        "Prompt",
                        `${trace.output.prompt_name}@${trace.output.prompt_version}`,
                      ],
                      [
                        "Tokens",
                        `${trace.output.input_tokens} entrada · ${trace.output.output_tokens} salida`,
                      ],
                      ["Coste", `${trace.output.cost_eur.toFixed(5)} €`],
                    ].map(([term, value]) => (
                      <div key={term}>
                        <dt className="text-[12px] text-slate-500">{term}</dt>
                        <dd className="mt-0.5">
                          <Mono className="text-slate-950">{value}</Mono>
                        </dd>
                      </div>
                    ))}
                    {trace.output.blocked_reason ? (
                      <div>
                        <dt className="text-[12px] text-slate-500">Bloqueo</dt>
                        <dd className="mt-0.5">
                          <Mono className="text-amber-500">
                            {trace.output.blocked_reason}
                          </Mono>
                        </dd>
                      </div>
                    ) : null}
                  </dl>
                </Card>
              ) : null}

              {trace.sources.length > 0 ? (
                <Card>
                  <CardHeader
                    title="Fuentes citadas"
                    subtitle="Estado al citarse frente al de ahora"
                  />
                  <ul className="divide-y divide-slate-200">
                    {trace.sources.map((source, i) => (
                      <li key={i} className="px-5 py-3">
                        <p className="text-[13px] font-medium text-slate-950">
                          {source.title}
                        </p>
                        <div className="flex items-center gap-2 mt-1">
                          <StatusBadge status={source.status_at_use} />
                          {source.status_changed ? (
                            <>
                              <span className="text-slate-300" aria-hidden>
                                →
                              </span>
                              <StatusBadge status={source.status_now} />
                            </>
                          ) : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                </Card>
              ) : null}

              {trace.events.length > 0 ? (
                <Card>
                  <CardHeader title="Eventos de auditoría" />
                  <ul className="divide-y divide-slate-200">
                    {trace.events.map((event) => (
                      <li key={event.id} className="px-5 py-2.5">
                        <Mono className="text-slate-950">{event.action}</Mono>
                        <p className="text-[12px] text-slate-500 mt-0.5">
                          {event.outcome} · {event.exposed_field_count} campos
                          expuestos
                        </p>
                      </li>
                    ))}
                  </ul>
                </Card>
              ) : null}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
