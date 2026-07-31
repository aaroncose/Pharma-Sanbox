"use client";

import Link from "next/link";
import { useState } from "react";

import {
  BlockedNotice,
  Button,
  Card,
  CardHeader,
  ConfidenceBar,
  Mono,
  PolicyTag,
  inputClass,
} from "@/components/ui";

import { ask, type AgentEnvelope } from "./actions";

/**
 * Asistente documental.
 *
 * La pantalla está construida alrededor de un caso que en la mayoría de
 * productos se trata como fallo: **que el sistema se niegue a responder**.
 * Cuando la documentación no sostiene la pregunta, la negativa se presenta con
 * la misma jerarquía visual que una respuesta correcta, con los huecos
 * explicados. Enseñarla como un error entrenaría al usuario a verla como un
 * estorbo, y de ahí a buscar cómo esquivarla hay un paso.
 *
 * Cada respuesta llega acompañada de sus fuentes, del veredicto del verificador
 * y del coste. No como adorno técnico: son las tres cosas que permiten decidir
 * si fiarse de lo que se acaba de leer.
 */

const SUGGESTED = [
  {
    label: "Pregunta que la documentación sí respalda",
    text: "¿Qué información de seguridad aprobada hay sobre CardioX?",
  },
  {
    label: "Pregunta que ningún documento aprobado sostiene",
    text: "¿Cuál es el porcentaje exacto de reducción de riesgo cardiovascular de CardioX?",
  },
  {
    label: "Consulta clínica: se bloquea antes de llegar al modelo",
    text: "¿Qué dosis le pongo a un paciente de 78 años con insuficiencia renal?",
  },
];

type Entry = { question: string; result: AgentEnvelope };

export function ChatClient() {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Entry[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = question.trim();
    if (!text || pending) return;

    setPending(true);
    setError(null);

    const response = await ask(text);
    if (response.ok) {
      setHistory((prev) => [{ question: text, result: response.data }, ...prev]);
      setQuestion("");
    } else {
      setError(response.message);
    }
    setPending(false);
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-[22px] font-semibold tracking-tight">
          Asistente documental
        </h2>
        <p className="text-[14px] text-slate-500 mt-1">
          Responde solo con documentación aprobada y vigente. Cada turno se
          responde por separado: no hay historial de conversación.
        </p>
      </div>

      <Card className="p-5">
        <form onSubmit={submit} className="space-y-3">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={3}
            className={`${inputClass} resize-y`}
            placeholder="¿Qué quieres consultar sobre la documentación aprobada?"
            disabled={pending}
          />
          <div className="flex items-center justify-between gap-4">
            <p className="text-[12px] text-slate-500">
              Las respuestas citan el documento, la versión y la sección.
            </p>
            <Button type="submit" disabled={pending || !question.trim()}>
              {pending ? "Consultando…" : "Preguntar"}
            </Button>
          </div>
        </form>

        {error ? (
          <p role="alert" className="mt-3 text-[13px] text-red-500 bg-red-50 rounded-[10px] px-3 py-2">
            {error}
          </p>
        ) : null}

        {history.length === 0 ? (
          <div className="mt-5 pt-5 border-t border-slate-200">
            <p className="text-[12px] font-semibold uppercase tracking-wider text-slate-500">
              Para ver cómo se comporta
            </p>
            <ul className="mt-2 space-y-2">
              {SUGGESTED.map((suggestion) => (
                <li key={suggestion.text}>
                  <button
                    type="button"
                    onClick={() => setQuestion(suggestion.text)}
                    className="w-full text-left border border-slate-200 rounded-[10px] px-3 py-2 hover:border-blue-600 hover:bg-blue-50/40 transition-colors"
                  >
                    <p className="text-[12px] text-slate-500">
                      {suggestion.label}
                    </p>
                    <p className="text-[13px] text-slate-950 mt-0.5">
                      {suggestion.text}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Card>

      {history.map((entry, index) => (
        <Answer key={index} question={entry.question} result={entry.result} />
      ))}
    </div>
  );
}

function Answer({ question, result }: { question: string; result: AgentEnvelope }) {
  const { output, verifier, sources, meta } = result;
  const cited = new Set(output?.sources ?? []);

  return (
    <Card>
      <CardHeader
        title={question}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <Mono>{meta.prompt}</Mono>
            <Mono>{meta.model}</Mono>
            <Mono>{meta.latency_ms} ms</Mono>
            <Mono>{meta.cost_eur.toFixed(5)} €</Mono>
          </span>
        }
      />

      <div className="p-5 space-y-5">
        {result.degraded ? (
          <div className="bg-amber-50 border border-amber-500/30 rounded-[10px] px-4 py-3">
            <p className="text-[13px] text-slate-950">
              El proveedor de IA no respondió. La operación no se ha perdido,
              pero el contenido <strong>no se ha verificado</strong> y requiere
              revisión antes de usarse.
            </p>
          </div>
        ) : null}

        {result.blocked_reason ? (
          <BlockedNotice
            reason={result.blocked_reason}
            policyCodes={result.policy_codes}
            gaps={output?.gaps ?? []}
          />
        ) : (
          <div className="prose-sm">
            <p className="text-[14px] leading-relaxed text-slate-950 whitespace-pre-wrap">
              {output?.answer}
            </p>
          </div>
        )}

        {/* Confianza y verificación */}
        {output && !result.blocked_reason ? (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3 pt-1">
            <div>
              <p className="text-[12px] text-slate-500 mb-1">Confianza</p>
              <ConfidenceBar value={output.confidence} />
            </div>

            {verifier ? (
              <div>
                <p className="text-[12px] text-slate-500 mb-1">Verificador</p>
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] font-medium ${
                    verifier.verdict === "supported"
                      ? "bg-teal-50 text-teal-500"
                      : verifier.verdict === "partially_supported"
                        ? "bg-amber-50 text-amber-500"
                        : "bg-red-50 text-red-500"
                  }`}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-current" aria-hidden />
                  {verifier.verdict === "supported"
                    ? "Respaldado"
                    : verifier.verdict === "partially_supported"
                      ? "Parcialmente respaldado"
                      : "Sin respaldo"}
                </span>
              </div>
            ) : null}

            {result.requires_human_review ? (
              <div>
                <p className="text-[12px] text-slate-500 mb-1">Estado</p>
                <span className="inline-flex items-center gap-1.5 bg-amber-50 text-amber-500 rounded-full px-2.5 py-1 text-[12px] font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-current" aria-hidden />
                  En cola de compliance
                </span>
              </div>
            ) : null}
          </div>
        ) : null}

        {/* Afirmaciones que el verificador no pudo sostener */}
        {verifier && verifier.unsupported_claims.length > 0 ? (
          <div className="bg-amber-50 rounded-[10px] p-4">
            <p className="text-[13px] font-medium text-slate-950">
              El verificador no pudo sostener{" "}
              {verifier.unsupported_claims.length}{" "}
              {verifier.unsupported_claims.length === 1
                ? "afirmación"
                : "afirmaciones"}
            </p>
            <ul className="mt-2 space-y-2">
              {verifier.unsupported_claims.map((claim, i) => (
                <li key={i} className="text-[13px] text-slate-700">
                  <span className="italic">«{claim.claim}»</span>
                  <span className="text-slate-500"> — {claim.why}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {output && output.gaps.length > 0 && !result.blocked_reason ? (
          <div>
            <p className="text-[12px] font-semibold uppercase tracking-wider text-slate-500">
              Lo que la documentación no cubre
            </p>
            <ul className="mt-2 space-y-1.5">
              {output.gaps.map((gap, i) => (
                <li key={i} className="text-[13px] text-slate-700 flex gap-2">
                  <span className="text-slate-300 shrink-0" aria-hidden>
                    ·
                  </span>
                  {gap}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {/* Fuentes: se distingue lo citado de lo solo recuperado */}
        {sources.length > 0 ? (
          <div>
            <p className="text-[12px] font-semibold uppercase tracking-wider text-slate-500">
              Material recuperado
              <span className="ml-2 font-normal normal-case tracking-normal">
                {cited.size} de {sources.length} citado
                {cited.size === 1 ? "" : "s"} en la respuesta
              </span>
            </p>
            <ul className="mt-2 space-y-2">
              {sources.map((source) => {
                const wasCited = cited.has(source.source_id);
                return (
                  <li
                    key={source.source_id + source.section}
                    className={`rounded-[10px] border px-3 py-2.5 ${
                      wasCited
                        ? "border-teal-500/30 bg-teal-50/40"
                        : "border-slate-200"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-[13px] font-medium text-slate-950">
                          {source.title}{" "}
                          <Mono className="text-slate-500">{source.version}</Mono>
                        </p>
                        {source.section ? (
                          <p className="text-[12px] text-slate-500">
                            {source.section}
                          </p>
                        ) : null}
                      </div>
                      <div className="shrink-0 text-right">
                        {wasCited ? (
                          <span className="text-[11px] text-teal-500 font-medium">
                            Citado
                          </span>
                        ) : (
                          <span className="text-[11px] text-slate-500">
                            Recuperado
                          </span>
                        )}
                        {/* Los dos rangos por separado: es lo que hace
                            comprobable que la búsqueda sea híbrida y no solo
                            vectorial. */}
                        <p className="text-[11px] text-slate-500 mono mt-0.5">
                          {source.semantic_rank ? `v${source.semantic_rank}` : "v—"}
                          {" · "}
                          {source.lexical_rank ? `l${source.lexical_rank}` : "l—"}
                        </p>
                      </div>
                    </div>
                    <p className="text-[12px] text-slate-500 mt-1.5 line-clamp-2">
                      {source.excerpt}
                    </p>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-slate-200 mt-1 pt-4">
          {result.policy_codes.map((code) => (
            <PolicyTag key={code} code={code} />
          ))}
          <Link
            href={`/audit/trace/${meta.trace_id}`}
            className="ml-auto text-[13px] text-blue-600 hover:underline"
          >
            Ver traza completa →
          </Link>
        </div>
      </div>
    </Card>
  );
}
