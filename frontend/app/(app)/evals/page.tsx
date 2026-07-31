import Link from "next/link";

import { Card, CardHeader, EmptyState, Mono } from "@/components/ui";
import { api } from "@/lib/api";
import { getProfile } from "@/lib/session";

import { RunControls } from "./RunControls";

export const dynamic = "force-dynamic";

type Target = { min?: number; max?: number; label: string; unit: string };

type Run = {
  id: string;
  prompt_name: string;
  prompt_version: string;
  model: string;
  provider: string;
  started_at: string;
  finished_at: string | null;
  metrics: Record<string, unknown>;
};

type Overview = {
  dataset: {
    slug: string;
    case_count: number;
    categories: string[];
    target_corpus: string;
  };
  targets: Record<string, Target>;
  runs: Run[];
  count: number;
};

const METRIC_ORDER = [
  "valid_sources_pct",
  "supported_claims_pct",
  "correct_blocks_pct",
  "cross_tenant_leaks",
  "injection_resistance_pct",
  "hallucination_pct",
  "mean_latency_ms",
  "mean_cost_eur",
  "review_required_pct",
];

function formatMetric(key: string, value: unknown): string {
  if (typeof value !== "number") return "—";
  if (key === "mean_latency_ms") return `${(value / 1000).toFixed(1)} s`;
  if (key === "mean_cost_eur") return `€${value.toFixed(4)}`;
  if (key.endsWith("_pct")) return `${value}%`;
  return String(value);
}

function formatTarget(target: Target): string {
  if (target.min !== undefined) {
    return `≥ ${target.min}${target.unit === "%" ? "%" : ""}`;
  }
  if (target.max !== undefined) {
    if (target.unit === "ms") return `< ${(target.max / 1000).toFixed(0)} s`;
    if (target.unit === "€") return `< €${target.max}`;
    return `≤ ${target.max}${target.unit === "%" ? "%" : ""}`;
  }
  return "—";
}

function meetsTarget(key: string, value: unknown, target?: Target): boolean | null {
  if (!target || typeof value !== "number") return null;
  if (target.min !== undefined) return value >= target.min;
  if (target.max !== undefined) return value <= target.max;
  return null;
}

export default async function EvalsPage() {
  const profile = (await getProfile())!;
  const canRun = profile.permissions.includes("eval.run");

  const data = await api<Overview>("/api/v1/evals?limit=10");

  const byVersion = new Map<string, Run>();
  for (const run of data.runs) {
    if (!byVersion.has(run.prompt_version)) byVersion.set(run.prompt_version, run);
  }
  const columns = [...byVersion.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .slice(-3);

  const latest = data.runs[0];
  const corpusMismatch =
    latest && latest.metrics.corpus_match === false;

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-6">
        <div>
          <h2 className="text-[22px] font-semibold tracking-tight">
            Evaluaciones
          </h2>
          <p className="text-[14px] text-slate-500 mt-1 max-w-2xl">
            Control de calidad del agente. Cada ejecución pasa los mismos{" "}
            {data.dataset.case_count} casos y guarda el resultado, de modo que
            dos versiones del prompt se puedan comparar en lugar de valorarse por
            separado.
          </p>
        </div>
        <RunControls versions={[...byVersion.keys()].sort()} canRun={canRun} />
      </div>

      {corpusMismatch ? (
        <Card className="p-4 border-amber-500/40 bg-amber-50">
          <p className="text-[13px] font-medium text-slate-950">
            La suite se ejecutó sobre el corpus de{" "}
            {String(latest.metrics.executed_for_tenant)} y los casos preguntan
            por el de {data.dataset.target_corpus}
          </p>
          <p className="text-[12px] text-slate-700 mt-1">
            Los casos de respuesta correcta suspenden en bloque porque el
            aislamiento entre organizaciones impide leer ese material. No es un
            fallo del agente: es la misma propiedad que verifica el Failure Lab,
            afectando aquí a la medición.
          </p>
        </Card>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-slate-500">
        <span>
          Dataset <Mono className="text-slate-700">{data.dataset.slug}</Mono>
        </span>
        <span>·</span>
        <span>{data.dataset.case_count} casos</span>
        <span>·</span>
        <span>{data.dataset.categories.join(", ")}</span>
        {latest ? (
          <>
            <span>·</span>
            <span>
              Última ejecución{" "}
              {new Date(latest.started_at).toLocaleString("es", {
                day: "2-digit",
                month: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
            <span>·</span>
            <span>
              proveedor <Mono className="text-slate-700">{latest.provider}</Mono>
            </span>
          </>
        ) : null}
      </div>

      {columns.length === 0 ? (
        <Card>
          <EmptyState
            title="La suite todavía no se ha ejecutado"
            description={
              canRun
                ? "Pulsa «Ejecutar suite» para pasar los 25 casos y obtener la primera medición."
                : "Cuando alguien con permiso eval.run la ejecute, los resultados aparecerán aquí."
            }
          />
        </Card>
      ) : (
        <Card>
          <CardHeader
            title="Métricas por versión del prompt"
            subtitle="Los objetivos los define el servidor junto al conjunto, no esta pantalla."
          />
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="text-[12px] text-slate-500 border-b border-slate-200">
                  <th className="font-medium px-5 py-2.5">Métrica</th>
                  {columns.map(([version, run]) => (
                    <th key={version} className="font-medium px-3 py-2.5">
                      <span className="mono text-slate-950">
                        {run.prompt_name}.{version}
                      </span>
                    </th>
                  ))}
                  <th className="font-medium px-5 py-2.5">Objetivo</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {METRIC_ORDER.filter((key) => data.targets[key]).map((key) => {
                  const target = data.targets[key];
                  return (
                    <tr key={key} className="hover:bg-slate-100/50">
                      <td className="px-5 py-2.5 text-[13px] text-slate-700">
                        {target.label}
                      </td>
                      {columns.map(([version, run]) => {
                        const value = run.metrics[key];
                        const ok = meetsTarget(key, value, target);
                        return (
                          <td key={version} className="px-3 py-2.5">
                            <span
                              className={`mono text-[13px] font-medium ${
                                ok === null
                                  ? "text-slate-700"
                                  : ok
                                    ? "text-teal-500"
                                    : "text-red-500"
                              }`}
                            >
                              {formatMetric(key, value)}
                            </span>
                          </td>
                        );
                      })}
                      <td className="px-5 py-2.5">
                        <Mono className="text-slate-500">
                          {formatTarget(target)}
                        </Mono>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {data.runs.length > 0 ? (
        <Card>
          <CardHeader title={`${data.count} ejecuciones registradas`} />
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="text-[12px] text-slate-500 border-b border-slate-200">
                  <th className="font-medium px-5 py-2.5">Momento</th>
                  <th className="font-medium px-3 py-2.5">Prompt</th>
                  <th className="font-medium px-3 py-2.5">Modelo</th>
                  <th className="font-medium px-3 py-2.5">Casos superados</th>
                  <th className="font-medium px-5 py-2.5">Detalle</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {data.runs.map((run) => {
                  const passed = Number(run.metrics.cases_passed ?? 0);
                  const total = Number(run.metrics.cases_total ?? 0);
                  const allGreen = total > 0 && passed === total;
                  return (
                    <tr key={run.id} className="hover:bg-slate-100/50">
                      <td className="px-5 py-2.5">
                        <Mono className="text-slate-500">
                          {new Date(run.started_at).toLocaleString("es", {
                            day: "2-digit",
                            month: "2-digit",
                            hour: "2-digit",
                            minute: "2-digit",
                          })}
                        </Mono>
                      </td>
                      <td className="px-3 py-2.5">
                        <Mono className="text-slate-950">
                          {run.prompt_name}.{run.prompt_version}
                        </Mono>
                      </td>
                      <td className="px-3 py-2.5">
                        <Mono className="text-slate-500">{run.model}</Mono>
                      </td>
                      <td className="px-3 py-2.5">
                        <Mono
                          className={
                            allGreen
                              ? "text-teal-500 font-semibold"
                              : "text-slate-700"
                          }
                        >
                          {passed}/{total}
                        </Mono>
                      </td>
                      <td className="px-5 py-2.5">
                        <Link
                          href={`/evals/${run.id}`}
                          className="text-[13px] text-blue-600 hover:underline"
                        >
                          Ver casos
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
