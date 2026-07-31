import Link from "next/link";

import {
  Card,
  CardHeader,
  ConfidenceBar,
  Mono,
  PolicyTag,
  RiskBadge,
  StatusBadge,
} from "@/components/ui";
import { api } from "@/lib/api";

import { DecisionPanel } from "./DecisionPanel";
import { guard } from "@/components/Guard";

export const dynamic = "force-dynamic";

type Detail = {
  id: string;
  subject_type: string;
  reason: string;
  policy_code: string | null;
  priority: string;
  status: string;
  original_content: string;
  edited_content: string | null;
  decision_rationale: string | null;
  decided_at: string | null;
  created_at: string;
  agent_output: {
    id: string;
    kind: string;
    confidence: number;
    risk: string;
    blocked_reason: string | null;
    trace_id: string;
    prompt_name: string;
    prompt_version: string;
    model: string;
    cost_eur: number;
  } | null;
  sources: {
    document_id: string;
    title: string;
    document_version: string;
    document_status_at_use: string;
    status_now: string;
    status_changed: boolean;
    quoted_excerpt: string | null;
  }[];
};

export default async function ReviewDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const denied = await guard("review.read");
  if (denied) return denied;

  const { id } = await params;
  const item = await api<Detail>(`/api/v1/review/${id}`);
  const decided = item.status !== "pending";

  return (
    <div className="space-y-6">
      <div>
        <Link href="/compliance" className="text-[13px] text-blue-600 hover:underline">
          ← Cola de revisión
        </Link>
        <h2 className="text-[22px] font-semibold tracking-tight mt-2">
          {item.reason}
        </h2>
        <div className="flex flex-wrap items-center gap-3 mt-2">
          <StatusBadge status={item.status} />
          {item.policy_code ? <PolicyTag code={item.policy_code} /> : null}
          {item.agent_output ? (
            <RiskBadge level={item.agent_output.risk} />
          ) : null}
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <Card>
            <CardHeader title="Contenido generado" />
            <div className="p-5">
              <p className="text-[14px] leading-relaxed whitespace-pre-wrap text-slate-950">
                {item.original_content}
              </p>
            </div>
          </Card>

          {item.edited_content ? (
            <Card>
              <CardHeader
                title="Contenido corregido"
                subtitle="El original se conserva arriba, sin tocar"
              />
              <div className="p-5">
                <p className="text-[14px] leading-relaxed whitespace-pre-wrap text-slate-950">
                  {item.edited_content}
                </p>
              </div>
            </Card>
          ) : null}

          {item.sources.length > 0 ? (
            <Card>
              <CardHeader
                title="Fuentes citadas"
                subtitle="Con el estado que tenían al citarse, no el de ahora"
              />
              <ul className="divide-y divide-slate-200">
                {item.sources.map((source, i) => (
                  <li key={i} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-[13px] font-medium text-slate-950">
                          {source.title}{" "}
                          <Mono className="text-slate-500">
                            {source.document_version}
                          </Mono>
                        </p>
                        {source.quoted_excerpt ? (
                          <p className="text-[12px] text-slate-500 mt-1 line-clamp-2">
                            {source.quoted_excerpt}
                          </p>
                        ) : null}
                      </div>
                      <div className="shrink-0 text-right">
                        <StatusBadge status={source.document_status_at_use} />
                        {/* La comparación que hace auditable una retirada. */}
                        {source.status_changed ? (
                          <p className="text-[11px] text-red-500 mt-1">
                            Ahora: {source.status_now}
                          </p>
                        ) : null}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader title={decided ? "Decisión tomada" : "Decidir"} />
            <div className="p-5">
              {decided ? (
                <div className="space-y-3">
                  <StatusBadge status={item.status} />
                  <div>
                    <p className="text-[12px] text-slate-500">Motivo registrado</p>
                    <p className="text-[13px] text-slate-950 mt-1">
                      {item.decision_rationale}
                    </p>
                  </div>
                  <p className="text-[12px] text-slate-500">
                    Una decisión de compliance no se sobrescribe: quien la tomó
                    consta como responsable.
                  </p>
                </div>
              ) : (
                <DecisionPanel
                  reviewItemId={item.id}
                  originalContent={item.original_content}
                />
              )}
            </div>
          </Card>

          {item.agent_output ? (
            <Card>
              <CardHeader title="Procedencia" />
              <dl className="p-5 space-y-3">
                <div>
                  <dt className="text-[12px] text-slate-500">Confianza declarada</dt>
                  <dd className="mt-1">
                    <ConfidenceBar value={item.agent_output.confidence} />
                  </dd>
                </div>
                {[
                  ["Modelo", item.agent_output.model],
                  [
                    "Prompt",
                    `${item.agent_output.prompt_name}@${item.agent_output.prompt_version}`,
                  ],
                  ["Coste", `${item.agent_output.cost_eur.toFixed(5)} €`],
                ].map(([term, value]) => (
                  <div key={term}>
                    <dt className="text-[12px] text-slate-500">{term}</dt>
                    <dd className="mt-0.5">
                      <Mono className="text-slate-950">{value}</Mono>
                    </dd>
                  </div>
                ))}
                <Link
                  href={`/audit/trace/${item.agent_output.trace_id}`}
                  className="block text-[13px] text-blue-600 hover:underline pt-1"
                >
                  Ver traza completa →
                </Link>
              </dl>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
