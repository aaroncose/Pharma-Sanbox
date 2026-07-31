import Link from "next/link";

import { Card, CardHeader, EmptyState, Mono } from "@/components/ui";
import { api } from "@/lib/api";
import { guard } from "@/components/Guard";

export const dynamic = "force-dynamic";

type Result = {
  ref: string;
  category: string;
  notes: string | null;
  input: { question?: string; product_code?: string | null; kind?: string };
  expectation: Record<string, unknown>;
  passed: boolean;
  score: number;
  actual: Record<string, unknown>;
  failure_note: string | null;
  latency_ms: number;
  cost_eur: number;
};

type RunDetail = {
  run: {
    id: string;
    prompt_name: string;
    prompt_version: string;
    model: string;
    provider: string;
    started_at: string;
    metrics: Record<string, unknown>;
  };
  results: Result[];
};

const CATEGORY_LABEL: Record<string, string> = {
  correctness: "Respuesta correcta",
  faithfulness: "Fidelidad a las fuentes",
  safety: "Seguridad clínica",
  policy: "Política promocional",
  isolation: "Aislamiento entre organizaciones",
  injection: "Inyección de prompt",
  tools: "Restricción de herramientas",
};

export default async function EvalRunPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const denied = await guard("eval.read");
  if (denied) return denied;

  const { runId } = await params;
  const data = await api<RunDetail>(`/api/v1/evals/runs/${runId}`);

  const failed = data.results.filter((r) => !r.passed);
  const grouped = new Map<string, Result[]>();
  for (const result of data.results) {
    const list = grouped.get(result.category) ?? [];
    list.push(result);
    grouped.set(result.category, list);
  }

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/evals"
          className="text-[13px] text-blue-600 hover:underline"
        >
          ← Evaluaciones
        </Link>
        <h2 className="text-[22px] font-semibold tracking-tight mt-2">
          <span className="mono">
            {data.run.prompt_name}.{data.run.prompt_version}
          </span>
        </h2>
        <p className="text-[14px] text-slate-500 mt-1">
          {new Date(data.run.started_at).toLocaleString("es")} · modelo{" "}
          <Mono className="text-slate-700">{data.run.model}</Mono> · proveedor{" "}
          <Mono className="text-slate-700">{data.run.provider}</Mono>
        </p>
      </div>

      <Card
        className={`p-4 ${
          failed.length === 0
            ? "border-teal-500/30 bg-teal-50/40"
            : "border-amber-500/40 bg-amber-50"
        }`}
      >
        <p className="text-[13px] font-medium text-slate-950">
          {failed.length === 0
            ? `Los ${data.results.length} casos se comportaron como debían`
            : `${failed.length} de ${data.results.length} casos no se comportaron como debían`}
        </p>
        {failed.length > 0 ? (
          <p className="text-[12px] text-slate-700 mt-1">
            Los casos que fallaron aparecen primero dentro de su categoría, con
            el motivo exacto de cada comprobación.
          </p>
        ) : null}
      </Card>

      {data.results.length === 0 ? (
        <Card>
          <EmptyState title="Esta ejecución no registró casos" />
        </Card>
      ) : (
        [...grouped.entries()].map(([category, results]) => (
          <Card key={category}>
            <CardHeader
              title={CATEGORY_LABEL[category] ?? category}
              subtitle={`${results.filter((r) => r.passed).length} de ${results.length} superados`}
            />
            <ul className="divide-y divide-slate-200">
              {results.map((result) => (
                <li key={result.ref} className="px-5 py-4">
                  <div className="flex items-start gap-3">
                    <span
                      className={`mt-1 w-2 h-2 rounded-full shrink-0 ${
                        result.passed ? "bg-teal-500" : "bg-red-500"
                      }`}
                      aria-label={result.passed ? "Superado" : "Fallido"}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Mono className="text-slate-950 font-medium">
                          {result.ref}
                        </Mono>
                        {result.input.kind && result.input.kind !== "agent" ? (
                          <span className="text-[11px] text-slate-500 bg-slate-100 rounded px-1.5 py-0.5">
                            sin llamada al modelo
                          </span>
                        ) : null}
                      </div>

                      <p className="text-[13px] text-slate-700 mt-1">
                        {result.input.question}
                      </p>

                      {result.notes ? (
                        <p className="text-[12px] text-slate-500 mt-1">
                          {result.notes}
                        </p>
                      ) : null}

                      {!result.passed && result.failure_note ? (
                        <p className="text-[12px] text-red-500 mt-2">
                          {result.failure_note}
                        </p>
                      ) : null}

                      <div className="flex flex-wrap gap-1.5 mt-2">
                        {Object.entries(
                          (result.actual.checks as Record<string, boolean>) ?? {},
                        ).map(([check, ok]) => (
                          <span
                            key={check}
                            className={`mono text-[11px] rounded px-1.5 py-0.5 ${
                              ok
                                ? "bg-teal-50 text-teal-500"
                                : "bg-red-50 text-red-500"
                            }`}
                          >
                            {ok ? "✓" : "✕"} {check}
                          </span>
                        ))}
                      </div>

                      {result.input.kind === "agent" ? (
                        <div className="flex flex-wrap items-center gap-3 mt-2 text-[11px] text-slate-500">
                          <span>{result.latency_ms} ms</span>
                          <span>€{result.cost_eur.toFixed(4)}</span>
                          <Link
                            href={`/audit/trace/ev_${result.ref.slice(0, 24)}`}
                            className="text-blue-600 hover:underline"
                          >
                            Ver traza
                          </Link>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        ))
      )}
    </div>
  );
}
