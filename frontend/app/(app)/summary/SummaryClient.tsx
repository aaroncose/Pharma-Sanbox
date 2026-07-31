"use client";

import Link from "next/link";
import { useState } from "react";

import {
  BlockedNotice,
  Button,
  Card,
  CardHeader,
  Field,
  Mono,
  inputClass,
} from "@/components/ui";

import { generateSummary, type Envelope } from "../briefing/actions";

type Summary = {
  summary: string;
  hcp_statements: string[];
  rep_commitments: {
    statement: string;
    source_ids: string[];
    is_supported: boolean;
    concern: string;
  }[];
  open_questions: string[];
  follow_up_tasks: { title: string; detail: string; priority: string; due_in_days: number }[];
  gaps: string[];
};

const EXAMPLE =
  "Visita de 20 min. La doctora preguntó por tolerabilidad en mayores de 75. " +
  "Le dije que CardioX reduce el riesgo cardiovascular un 37% según el estudio " +
  "y que va muy bien en diabéticos con insuficiencia renal. Quedamos en que le " +
  "envío la ficha técnica esta semana.";

export function SummaryClient({
  hcps,
  products,
}: {
  hcps: { id: string; full_name: string; specialty: string }[];
  products: { id: string; name: string }[];
}) {
  const [hcpId, setHcpId] = useState(hcps[0]?.id ?? "");
  const [productId, setProductId] = useState(products[0]?.id ?? "");
  const [notes, setNotes] = useState("");
  const [result, setResult] = useState<Envelope | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    setResult(null);
    const response = await generateSummary({
      hcp_id: hcpId,
      product_id: productId,
      notes,
      channel: "in_person",
    });
    if (response.ok) setResult(response.data);
    else setError(response.message);
    setPending(false);
  }

  const summary = result?.output as Summary | null;
  const unsupported =
    summary?.rep_commitments.filter((c) => !c.is_supported) ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-[22px] font-semibold tracking-tight">
          Resumen posterior
        </h2>
        <p className="text-[14px] text-slate-500 mt-1">
          Separa lo que dijo el profesional de lo que prometiste tú, y contrasta
          cada compromiso con la documentación aprobada.
        </p>
      </div>

      <Card className="p-5">
        <form onSubmit={submit} className="space-y-4">
          <div className="grid md:grid-cols-2 gap-4">
            <Field label="Profesional sanitario">
              <select value={hcpId} onChange={(e) => setHcpId(e.target.value)} className={inputClass}>
                {hcps.map((h) => (
                  <option key={h.id} value={h.id}>{h.full_name} — {h.specialty}</option>
                ))}
              </select>
            </Field>
            <Field label="Producto">
              <select value={productId} onChange={(e) => setProductId(e.target.value)} className={inputClass}>
                {products.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </Field>
          </div>

          <Field
            label="Notas de la visita"
            hint="Tal como las escribirías. El sistema extrae lo que afirmaste y comprueba si se sostiene."
          >
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={6}
              className={`${inputClass} resize-y`}
              required
              minLength={20}
            />
          </Field>

          <div className="flex items-center gap-3">
            <Button type="submit" disabled={pending || notes.trim().length < 20}>
              {pending ? "Procesando…" : "Generar resumen"}
            </Button>
            <button
              type="button"
              onClick={() => setNotes(EXAMPLE)}
              className="text-[13px] text-blue-600 hover:underline"
            >
              Usar unas notas de ejemplo con un compromiso sin respaldo
            </button>
          </div>
        </form>

        {error ? (
          <p role="alert" className="mt-4 text-[13px] text-red-500 bg-red-50 rounded-[10px] px-3 py-2">
            {error}
          </p>
        ) : null}
      </Card>

      {result ? (
        <Card>
          <CardHeader
            title="Resumen"
            subtitle={<Mono>{result.meta.cost_eur.toFixed(5)} €</Mono>}
          />
          <div className="p-5 space-y-6">
            {result.blocked_reason ? (
              <BlockedNotice
                reason={result.blocked_reason}
                policyCodes={result.policy_codes}
                gaps={summary?.gaps ?? []}
              />
            ) : summary ? (
              <>
                {/* Lo primero que se ve es lo que importa: los compromisos que
                    ningún documento aprobado sostiene. Un resumen que solo
                    condensa pierde exactamente eso. */}
                {unsupported.length > 0 ? (
                  <div className="bg-red-50 border border-red-500/30 rounded-[12px] p-4">
                    <p className="text-[14px] font-semibold text-slate-950">
                      {unsupported.length}{" "}
                      {unsupported.length === 1 ? "compromiso" : "compromisos"} sin
                      respaldo documental
                    </p>
                    <ul className="mt-2 space-y-2">
                      {unsupported.map((c, i) => (
                        <li key={i}>
                          <p className="text-[13px] text-slate-950">«{c.statement}»</p>
                          {c.concern ? (
                            <p className="text-[12px] text-slate-700 mt-0.5">{c.concern}</p>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <section>
                  <h3 className="text-[13px] font-semibold text-slate-950">Resumen</h3>
                  <p className="text-[14px] text-slate-700 mt-1.5 leading-relaxed">
                    {summary.summary}
                  </p>
                </section>

                {summary.hcp_statements.length > 0 ? (
                  <section>
                    <h3 className="text-[13px] font-semibold text-slate-950">
                      Lo que planteó el profesional
                    </h3>
                    <ul className="mt-1.5 space-y-1">
                      {summary.hcp_statements.map((s, i) => (
                        <li key={i} className="text-[14px] text-slate-700 flex gap-2">
                          <span className="text-slate-300" aria-hidden>·</span>{s}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                {summary.rep_commitments.length > 0 ? (
                  <section>
                    <h3 className="text-[13px] font-semibold text-slate-950">
                      Lo que afirmaste o prometiste
                    </h3>
                    <ul className="mt-1.5 space-y-2">
                      {summary.rep_commitments.map((c, i) => (
                        <li
                          key={i}
                          className={`rounded-[10px] px-3 py-2 ${
                            c.is_supported ? "bg-teal-50/60" : "bg-red-50"
                          }`}
                        >
                          <div className="flex items-start gap-2">
                            <span
                              className={`text-[11px] font-medium shrink-0 mt-0.5 ${
                                c.is_supported ? "text-teal-500" : "text-red-500"
                              }`}
                            >
                              {c.is_supported ? "RESPALDADO" : "SIN RESPALDO"}
                            </span>
                            <p className="text-[13px] text-slate-950">{c.statement}</p>
                          </div>
                          {c.source_ids.length > 0 ? (
                            <div className="flex flex-wrap gap-1.5 mt-1 ml-[86px]">
                              {c.source_ids.map((id) => (
                                <Mono key={id} className="text-teal-500">{id}</Mono>
                              ))}
                            </div>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                {summary.follow_up_tasks.length > 0 ? (
                  <section>
                    <h3 className="text-[13px] font-semibold text-slate-950">
                      Tareas creadas
                      <span className="ml-2 font-normal text-slate-500">
                        asignadas a ti, nunca a otra persona
                      </span>
                    </h3>
                    <ul className="mt-1.5 space-y-1.5">
                      {summary.follow_up_tasks.map((t, i) => (
                        <li key={i} className="flex items-baseline gap-2">
                          <span className="text-[13px] text-slate-950">{t.title}</span>
                          <Mono className="text-slate-500">{t.due_in_days} d</Mono>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                <div className="pt-3 border-t border-slate-200">
                  <Link
                    href={`/audit/trace/${result.meta.trace_id}`}
                    className="text-[13px] text-blue-600 hover:underline"
                  >
                    Ver traza →
                  </Link>
                </div>
              </>
            ) : null}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
