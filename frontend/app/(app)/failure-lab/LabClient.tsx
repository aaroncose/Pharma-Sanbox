"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { Button, Card, CardHeader, Mono } from "@/components/ui";

import { runScenario, type ScenarioOutcome } from "./actions";

export type Scenario = {
  id: string;
  slug: string;
  name: string;
  description: string;
  expectation: string;
  ordinal: number;
  last_passed: boolean | null;
  last_executed_at: string | null;
  last_trace_id: string | null;
};

function StateCell({ passed }: { passed: boolean | null }) {
  if (passed === null) {
    return <span className="text-[12px] text-slate-500">Sin ejecutar</span>;
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[12px] font-medium ${
        passed ? "text-teal-500" : "text-red-500"
      }`}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current" aria-hidden />
      {passed ? "Passed" : "Failed"}
    </span>
  );
}

function ResultPanel({ outcome }: { outcome: ScenarioOutcome }) {
  if (!outcome.ok) {
    return (
      <Card className="p-5 border-red-500/40 bg-red-50">
        <p className="text-[13px] text-red-500">{outcome.message}</p>
      </Card>
    );
  }

  return (
    <Card
      className={
        outcome.passed ? "border-teal-500/30" : "border-red-500/40"
      }
    >
      <CardHeader
        title={
          <span className="flex items-center gap-3">
            <span>Resultado</span>
            <span
              className={`text-[12px] font-semibold ${
                outcome.passed ? "text-teal-500" : "text-red-500"
              }`}
            >
              {outcome.passed ? "PASSED" : "FAILED"}
            </span>
          </span>
        }
        subtitle={
          outcome.passed
            ? "El sistema se comportó como debía."
            : "El sistema no se comportó como debía. Esto es un hallazgo real."
        }
      />

      <div className="px-5 py-4 space-y-4">
        <section>
          <p className="text-[11px] uppercase tracking-wider text-slate-500 mb-1.5">
            Request
          </p>
          <dl className="space-y-1">
            {Object.entries(outcome.request).map(([key, value]) => (
              <div key={key} className="flex gap-3 text-[12px]">
                <dt className="text-slate-500 w-44 shrink-0">{key}</dt>
                <dd className="mono text-slate-950 break-all">
                  {String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </section>

        <section>
          <p className="text-[11px] uppercase tracking-wider text-slate-500 mb-1.5">
            Result
          </p>
          <dl className="space-y-1">
            {Object.entries(outcome.result).map(([key, value]) => (
              <div key={key} className="flex gap-3 text-[12px]">
                <dt className="text-slate-500 w-44 shrink-0">{key}</dt>
                <dd className="mono text-slate-950 break-all">
                  {typeof value === "object" && value !== null
                    ? JSON.stringify(value)
                    : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </section>

        <div className="flex flex-wrap items-center gap-3 pt-1">
          <Link
            href={`/audit/trace/${outcome.traceId}`}
            className="text-[13px] text-blue-600 hover:underline"
          >
            Ver traza completa
          </Link>
          <Mono className="text-slate-500">{outcome.traceId}</Mono>
        </div>
      </div>
    </Card>
  );
}

export function LabClient({
  scenarios,
  canRun,
}: {
  scenarios: Scenario[];
  canRun: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [running, setRunning] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ScenarioOutcome | null>(null);

  const execute = (slug: string) => {
    setRunning(slug);
    setOutcome(null);
    startTransition(async () => {
      const result = await runScenario(slug);
      setOutcome(result);
      setRunning(null);
      router.refresh();
    });
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Escenarios"
          subtitle="Cada uno ejecuta el camino real del sistema. Ningún resultado está escrito de antemano."
        />
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="text-[12px] text-slate-500 border-b border-slate-200">
                <th className="font-medium px-5 py-2.5">Escenario</th>
                <th className="font-medium px-3 py-2.5">Descripción</th>
                <th className="font-medium px-3 py-2.5">Estado</th>
                <th className="font-medium px-5 py-2.5">Acción</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {scenarios.map((scenario) => (
                <tr key={scenario.id} className="hover:bg-slate-100/50">
                  <td className="px-5 py-3 align-top">
                    <p className="text-[13px] font-medium text-slate-950">
                      {scenario.name}
                    </p>
                    <Mono className="text-slate-500">{scenario.slug}</Mono>
                  </td>
                  <td className="px-3 py-3 align-top max-w-md">
                    <p className="text-[13px] text-slate-700">
                      {scenario.description}
                    </p>
                    <p className="text-[12px] text-slate-500 mt-1">
                      Se espera: {scenario.expectation}
                    </p>
                  </td>
                  <td className="px-3 py-3 align-top">
                    <StateCell passed={scenario.last_passed} />
                    {scenario.last_executed_at ? (
                      <p className="text-[11px] text-slate-500 mt-0.5">
                        {new Date(scenario.last_executed_at).toLocaleString(
                          "es",
                          {
                            day: "2-digit",
                            month: "2-digit",
                            hour: "2-digit",
                            minute: "2-digit",
                          },
                        )}
                      </p>
                    ) : null}
                  </td>
                  <td className="px-5 py-3 align-top">
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={!canRun || pending}
                      onClick={() => execute(scenario.slug)}
                      title={
                        canRun
                          ? undefined
                          : "Requiere el permiso failure_lab.run"
                      }
                    >
                      {running === scenario.slug ? "Ejecutando…" : "Ejecutar"}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {outcome ? <ResultPanel outcome={outcome} /> : null}
    </div>
  );
}
