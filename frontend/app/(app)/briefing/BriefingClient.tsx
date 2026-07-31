"use client";

import Link from "next/link";
import { useState } from "react";

import {
  BlockedNotice,
  Button,
  Card,
  CardHeader,
  ConfidenceBar,
  Field,
  Mono,
  PolicyTag,
  inputClass,
} from "@/components/ui";

import { generateBriefing, type Envelope } from "./actions";

type Hcp = {
  id: string;
  full_name: string;
  specialty: string;
  institution: string;
  consent_data_analysis: boolean;
};
type Product = { id: string; name: string; therapeutic_area: string };

type Briefing = {
  hcp_summary: string;
  history_highlights: string[];
  recommended_topics: { topic: string; rationale: string; source_ids: string[] }[];
  likely_questions: { question: string; suggested_answer: string; source_ids: string[] }[];
  permitted_information: { statement: string; source_ids: string[] }[];
  risks: { risk: string; mitigation: string }[];
  confidence: number;
  gaps: string[];
};

export function BriefingClient({
  hcps,
  products,
}: {
  hcps: Hcp[];
  products: Product[];
}) {
  const [hcpId, setHcpId] = useState(hcps[0]?.id ?? "");
  const [productId, setProductId] = useState(products[0]?.id ?? "");
  const [objective, setObjective] = useState(
    "Presentar el perfil de seguridad y resolver objeciones sobre la evidencia",
  );
  const [duration, setDuration] = useState(15);
  const [result, setResult] = useState<Envelope | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const hcp = hcps.find((h) => h.id === hcpId);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    setResult(null);

    const response = await generateBriefing({
      hcp_id: hcpId,
      product_id: productId,
      objective,
      duration_minutes: duration,
    });

    if (response.ok) setResult(response.data);
    else setError(response.message);
    setPending(false);
  }

  const briefing = result?.output as Briefing | null;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-[22px] font-semibold tracking-tight">
          Preparar reunión
        </h2>
        <p className="text-[14px] text-slate-500 mt-1">
          Briefing construido con documentación aprobada e historial autorizado.
        </p>
      </div>

      <Card className="p-5">
        <form onSubmit={submit} className="grid md:grid-cols-2 gap-4">
          <Field label="Profesional sanitario">
            <select
              value={hcpId}
              onChange={(e) => setHcpId(e.target.value)}
              className={inputClass}
            >
              {hcps.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.full_name} — {h.specialty}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Producto">
            <select
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
              className={inputClass}
            >
              {products.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>

          <div className="md:col-span-2">
            <Field label="Objetivo de la visita">
              <input
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                className={inputClass}
                required
              />
            </Field>
          </div>

          <Field label="Duración prevista (minutos)">
            <input
              type="number"
              min={5}
              max={90}
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value))}
              className={inputClass}
            />
          </Field>

          <div className="flex items-end">
            <Button type="submit" disabled={pending || !hcpId || !productId}>
              {pending ? "Generando…" : "Generar briefing"}
            </Button>
          </div>
        </form>

        {/* El aviso llega ANTES de generar. Sin él, el usuario recibe un
            briefing más pobre sin saber por qué y concluye que el sistema
            funciona mal, cuando lo que ocurre es que está respetando una
            decisión del profesional sanitario. */}
        {hcp && !hcp.consent_data_analysis ? (
          <p className="mt-4 text-[13px] text-slate-700 bg-amber-50 border border-amber-500/30 rounded-[10px] px-3 py-2">
            {hcp.full_name} no ha consentido el análisis de datos. El briefing se
            generará <strong>solo con documentación de producto</strong>, sin
            historial de interacciones. La comprobación se hace antes de leer el
            historial, no pidiéndole al modelo que lo ignore.
          </p>
        ) : null}

        {error ? (
          <p role="alert" className="mt-4 text-[13px] text-red-500 bg-red-50 rounded-[10px] px-3 py-2">
            {error}
          </p>
        ) : null}
      </Card>

      {result ? (
        <Card>
          <CardHeader
            title="Briefing"
            subtitle={
              <span className="flex flex-wrap gap-x-3">
                <Mono>{result.meta.prompt}</Mono>
                <Mono>{result.meta.model}</Mono>
                <Mono>{result.meta.cost_eur.toFixed(5)} €</Mono>
              </span>
            }
          />
          <div className="p-5 space-y-6">
            {result.consent && !result.consent.history_included ? (
              <p className="text-[13px] text-slate-700 bg-slate-100 rounded-[10px] px-3 py-2">
                {result.consent.reason}
              </p>
            ) : null}

            {result.blocked_reason ? (
              <BlockedNotice
                reason={result.blocked_reason}
                policyCodes={result.policy_codes}
                gaps={briefing?.gaps ?? []}
              />
            ) : briefing ? (
              <>
                {briefing.hcp_summary ? (
                  <section>
                    <h3 className="text-[13px] font-semibold text-slate-950">
                      Perfil
                    </h3>
                    <p className="text-[14px] text-slate-700 mt-1.5 leading-relaxed">
                      {briefing.hcp_summary}
                    </p>
                  </section>
                ) : null}

                {briefing.history_highlights.length > 0 ? (
                  <section>
                    <h3 className="text-[13px] font-semibold text-slate-950">
                      Historial relevante
                    </h3>
                    <ul className="mt-1.5 space-y-1">
                      {briefing.history_highlights.map((item, i) => (
                        <li key={i} className="text-[14px] text-slate-700 flex gap-2">
                          <span className="text-slate-300" aria-hidden>·</span>
                          {item}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                {briefing.recommended_topics.length > 0 ? (
                  <section>
                    <h3 className="text-[13px] font-semibold text-slate-950">
                      Temas propuestos
                    </h3>
                    <ul className="mt-1.5 space-y-2">
                      {briefing.recommended_topics.map((t, i) => (
                        <li key={i} className="border-l-2 border-blue-600 pl-3">
                          <p className="text-[14px] font-medium text-slate-950">
                            {t.topic}
                          </p>
                          <p className="text-[13px] text-slate-500">{t.rationale}</p>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                {briefing.likely_questions.length > 0 ? (
                  <section>
                    <h3 className="text-[13px] font-semibold text-slate-950">
                      Preguntas previsibles
                    </h3>
                    <ul className="mt-1.5 space-y-3">
                      {briefing.likely_questions.map((q, i) => (
                        <li key={i}>
                          <p className="text-[14px] font-medium text-slate-950">
                            {q.question}
                          </p>
                          <p className="text-[13px] text-slate-700 mt-0.5">
                            {q.suggested_answer}
                          </p>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                {briefing.permitted_information.length > 0 ? (
                  <section>
                    <h3 className="text-[13px] font-semibold text-slate-950">
                      Lo que puedes afirmar
                      <span className="ml-2 font-normal text-slate-500">
                        cada frase con la fuente que la sostiene
                      </span>
                    </h3>
                    <ul className="mt-1.5 space-y-2">
                      {briefing.permitted_information.map((s, i) => (
                        <li key={i} className="bg-teal-50/50 rounded-[10px] px-3 py-2">
                          <p className="text-[14px] text-slate-950">{s.statement}</p>
                          <div className="flex flex-wrap gap-1.5 mt-1">
                            {s.source_ids.map((id) => (
                              <Mono key={id} className="text-teal-500">{id}</Mono>
                            ))}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                {briefing.risks.length > 0 ? (
                  <section>
                    <h3 className="text-[13px] font-semibold text-slate-950">
                      Riesgos de esta visita
                    </h3>
                    <ul className="mt-1.5 space-y-2">
                      {briefing.risks.map((r, i) => (
                        <li key={i} className="bg-amber-50 rounded-[10px] px-3 py-2">
                          <p className="text-[13px] text-slate-950">{r.risk}</p>
                          {r.mitigation ? (
                            <p className="text-[13px] text-slate-700 mt-0.5">
                              → {r.mitigation}
                            </p>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}

                <div className="flex flex-wrap items-center gap-4 pt-3 border-t border-slate-200">
                  <div>
                    <p className="text-[12px] text-slate-500 mb-1">Confianza</p>
                    <ConfidenceBar value={briefing.confidence} />
                  </div>
                  {result.policy_codes.map((c) => (
                    <PolicyTag key={c} code={c} />
                  ))}
                  <Link
                    href={`/audit/trace/${result.meta.trace_id}`}
                    className="ml-auto text-[13px] text-blue-600 hover:underline"
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
