"use client";

import { useState } from "react";

import {
  Button,
  Card,
  CardHeader,
  Field,
  Mono,
  PolicyTag,
  inputClass,
} from "@/components/ui";

import {
  endSimulation,
  startSimulation,
  takeTurn,
  type Debrief,
  type StartResult,
} from "./actions";

/**
 * Simulador conversacional.
 *
 * El panel lateral enseña el riesgo **en vivo**, turno a turno. Es la razón por
 * la que el backend evalúa cada intervención del comercial en el momento en vez
 * de reconstruirlo al final: una infracción señalada tres turnos después ya se
 * convirtió en costumbre.
 *
 * Lo que no se enseña: si la pregunta del médico era una de las que no se
 * pueden responder. Avisarlo destruiría justo lo que se está midiendo.
 */

type Turn = {
  speaker: "hcp" | "rep";
  content: string;
  flag?: string | null;
  risk?: string;
  policyCodes?: string[];
};

const SCENARIOS = [
  "Duda sobre la evidencia de eficacia",
  "Objeción por el perfil de seguridad",
  "Comparación con el tratamiento actual",
];

export function SimulatorClient({
  hcps,
  products,
}: {
  hcps: { id: string; full_name: string; specialty: string }[];
  products: { id: string; name: string }[];
}) {
  const [session, setSession] = useState<StartResult | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [utterance, setUtterance] = useState("");
  const [debrief, setDebrief] = useState<Debrief | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [hcpId, setHcpId] = useState(hcps[0]?.id ?? "");
  const [productId, setProductId] = useState(products[0]?.id ?? "");
  const [scenario, setScenario] = useState(SCENARIOS[0]);
  const [attitude, setAttitude] = useState("escéptico");

  const flagged = turns.filter((t) => t.speaker === "rep" && t.flag).length;
  const repTurns = turns.filter((t) => t.speaker === "rep").length;
  const currentRisk = turns.findLast((t) => t.speaker === "rep")?.risk ?? "low";

  async function start(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    const response = await startSimulation({
      hcp_id: hcpId,
      product_id: productId,
      scenario,
      objective: "Explicar la evidencia aprobada sin hacer recomendaciones clínicas",
      attitude,
    });
    if (response.ok) {
      setSession(response.data);
      setTurns([{ speaker: "hcp", content: response.data.opening_turn.content }]);
    } else {
      setError(response.message);
    }
    setPending(false);
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!session || !utterance.trim() || pending) return;
    setPending(true);
    setError(null);

    const text = utterance.trim();
    setUtterance("");
    const response = await takeTurn(session.id, text);

    if (response.ok) {
      setTurns((prev) => [
        ...prev,
        {
          speaker: "rep",
          content: text,
          flag: response.data.rep_turn.compliance_flag,
          risk: response.data.rep_turn.risk_level,
          policyCodes: response.data.rep_turn.policy_codes,
        },
        { speaker: "hcp", content: response.data.hcp_turn.content },
      ]);
    } else {
      setError(response.message);
      setUtterance(text);
    }
    setPending(false);
  }

  async function finish() {
    if (!session) return;
    setPending(true);
    const response = await endSimulation(session.id);
    if (response.ok) setDebrief(response.data);
    else setError(response.message);
    setPending(false);
  }

  /* ── Informe final ─────────────────────────────────────────────────────── */
  if (debrief) {
    return (
      <div className="space-y-6">
        <h2 className="text-[22px] font-semibold tracking-tight">
          Resultado de la simulación
        </h2>

        <div className="grid sm:grid-cols-3 gap-4">
          <Card className="p-5">
            <p className="text-[13px] text-slate-500">Puntuación</p>
            <p className="text-[32px] font-semibold mono leading-tight mt-1">
              {debrief.score}
              <span className="text-[16px] text-slate-500"> / 100</span>
            </p>
            <p className="text-[12px] text-slate-500 mt-1">
              {debrief.score_breakdown}
            </p>
          </Card>
          <Card className="p-5">
            <p className="text-[13px] text-slate-500">Comunicación</p>
            <p className="text-[32px] font-semibold mono leading-tight mt-1">
              {debrief.communication.score}
            </p>
            <p className="text-[12px] text-slate-500 mt-1">
              {debrief.communication.summary}
            </p>
          </Card>
          <Card className="p-5">
            <p className="text-[13px] text-slate-500">Cumplimiento</p>
            <p
              className={`text-[32px] font-semibold mono leading-tight mt-1 ${
                debrief.compliance.flagged_turns > 0 ? "text-red-500" : "text-teal-500"
              }`}
            >
              {debrief.compliance.score}
            </p>
            <p className="text-[12px] text-slate-500 mt-1">
              {debrief.compliance.flagged_turns} de{" "}
              {debrief.compliance.total_rep_turns} turnos marcados
            </p>
          </Card>
        </div>

        {/* El techo, cuando actúa, se explica. Sin esto el comercial ve una
            nota que no cuadra con la aritmética que le acaban de enseñar. */}
        {debrief.score_cap !== null ? (
          <Card className="p-4 border-red-500/30 bg-red-50">
            <p className="text-[13px] font-medium text-slate-950">
              Tu nota está limitada a {debrief.score_cap} por una infracción de
              cumplimiento
            </p>
            <p className="text-[12px] text-slate-700 mt-1">
              El cumplimiento no pondera: limita. Ninguna cantidad de elocuencia
              compensa una afirmación que no se puede sostener, porque en la
              realidad tampoco lo hace.
            </p>
          </Card>
        ) : null}

        {/* El desglose. Un número sin desglose es una opinión. */}
        {debrief.compliance.penalties.length > 0 ? (
          <Card>
            <CardHeader title="De dónde sale la nota de cumplimiento" />
            <ul className="divide-y divide-slate-200">
              {debrief.compliance.penalties.map((p, i) => (
                <li key={i} className="px-5 py-3 flex items-center gap-3">
                  <Mono className="text-red-500 font-semibold">−{p.penalty}</Mono>
                  <span className="text-[13px] text-slate-700">
                    Turno {p.turn_ordinal}
                  </span>
                  <PolicyTag code={p.policy_code} />
                  <span className="ml-auto text-[12px] text-slate-500">
                    {p.severity}
                  </span>
                </li>
              ))}
              {debrief.compliance.bonus_applied > 0 ? (
                <li className="px-5 py-3 flex items-center gap-3">
                  <Mono className="text-teal-500 font-semibold">
                    +{debrief.compliance.bonus_applied}
                  </Mono>
                  <span className="text-[13px] text-slate-700">
                    Reconociste un límite en vez de improvisar
                  </span>
                </li>
              ) : null}
            </ul>
          </Card>
        ) : null}

        {debrief.communication.strengths.length > 0 ? (
          <Card>
            <CardHeader title="Lo que hiciste bien" />
            <ul className="p-5 space-y-2">
              {debrief.communication.strengths.map((s, i) => (
                <li key={i} className="text-[14px] text-slate-700 flex gap-2">
                  <span className="text-teal-500 shrink-0" aria-hidden>✓</span>
                  {s}
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        {debrief.improvable_answers.length > 0 ? (
          <Card>
            <CardHeader
              title="Respuestas mejorables"
              subtitle="Con la reformulación lista para usar"
            />
            <ul className="divide-y divide-slate-200">
              {debrief.improvable_answers.map((a, i) => (
                <li key={i} className="px-5 py-4">
                  {a.turn_ordinal !== null ? (
                    <Mono className="text-slate-500">Turno {a.turn_ordinal}</Mono>
                  ) : null}
                  <p className="text-[13px] text-slate-700 mt-1">{a.why}</p>
                  <div className="bg-teal-50/60 rounded-[10px] px-3 py-2 mt-2">
                    <p className="text-[12px] text-teal-500 font-medium">
                      Dilo así
                    </p>
                    <p className="text-[14px] text-slate-950 mt-0.5">
                      {a.suggested_rewrite}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        {debrief.sources_you_could_have_used.length > 0 ? (
          <Card>
            <CardHeader
              title="Fuentes que podrías haber citado"
              subtitle="Es lo que convierte el informe en enseñanza y no en una nota"
            />
            <ul className="divide-y divide-slate-200">
              {debrief.sources_you_could_have_used.slice(0, 5).map((s) => (
                <li key={s.source_id + s.section} className="px-5 py-3">
                  <p className="text-[13px] font-medium text-slate-950">
                    {s.title}
                    {s.section ? (
                      <span className="text-slate-500 font-normal"> · {s.section}</span>
                    ) : null}
                  </p>
                  <p className="text-[12px] text-slate-500 mt-0.5 line-clamp-2">
                    {s.excerpt}
                  </p>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        <Button
          variant="secondary"
          onClick={() => {
            setSession(null);
            setTurns([]);
            setDebrief(null);
          }}
        >
          Practicar otro escenario
        </Button>
      </div>
    );
  }

  /* ── Configuración ─────────────────────────────────────────────────────── */
  if (!session) {
    return (
      <div className="space-y-6">
        <div>
          <h2 className="text-[22px] font-semibold tracking-tight">Simulador</h2>
          <p className="text-[14px] text-slate-500 mt-1">
            Practica una visita. Lo que digas se evalúa con el mismo motor de
            políticas que gobierna al asistente en producción.
          </p>
        </div>

        <Card className="p-5">
          <form onSubmit={start} className="grid md:grid-cols-2 gap-4">
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
            <Field label="Escenario">
              <select value={scenario} onChange={(e) => setScenario(e.target.value)} className={inputClass}>
                {SCENARIOS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </Field>
            <Field
              label="Actitud del profesional"
              hint="Practicar con alguien escéptico y con alguien receptivo son dos entrenamientos distintos"
            >
              <select value={attitude} onChange={(e) => setAttitude(e.target.value)} className={inputClass}>
                {["receptivo", "escéptico", "con prisa", "hostil"].map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </select>
            </Field>
            <div className="md:col-span-2">
              <Button type="submit" disabled={pending || !hcpId || !productId}>
                {pending ? "Preparando…" : "Empezar simulación"}
              </Button>
            </div>
          </form>
          {error ? (
            <p role="alert" className="mt-4 text-[13px] text-red-500 bg-red-50 rounded-[10px] px-3 py-2">
              {error}
            </p>
          ) : null}
        </Card>
      </div>
    );
  }

  /* ── Conversación ──────────────────────────────────────────────────────── */
  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[18px] font-semibold tracking-tight">
            {session.hcp.full_name}
          </h2>
          <p className="text-[13px] text-slate-500">
            {session.hcp.specialty} · {session.hcp.institution} · {scenario}
          </p>
        </div>
        <Button variant="secondary" onClick={finish} disabled={pending || repTurns === 0}>
          Finalizar simulación
        </Button>
      </div>

      <div className="grid lg:grid-cols-[1fr_260px] gap-4">
        <Card className="flex flex-col">
          <div className="flex-1 p-5 space-y-4 max-h-[540px] overflow-y-auto">
            {turns.map((turn, i) => (
              <div key={i}>
                <p className="text-[12px] text-slate-500 mb-1">
                  {turn.speaker === "hcp" ? session.hcp.full_name : "Tú"}
                </p>
                <div
                  className={`rounded-[12px] px-4 py-2.5 max-w-[85%] ${
                    turn.speaker === "hcp"
                      ? "bg-slate-100 text-slate-950"
                      : turn.flag
                        ? "bg-red-50 border border-red-500/30 ml-auto"
                        : "bg-blue-50 ml-auto"
                  }`}
                >
                  <p className="text-[14px] leading-relaxed">{turn.content}</p>
                </div>
                {turn.flag ? (
                  <div className="flex justify-end mt-1.5">
                    <PolicyTag code={turn.flag} />
                  </div>
                ) : null}
              </div>
            ))}
          </div>

          <form onSubmit={send} className="border-t border-slate-200 p-4 flex gap-2">
            <input
              value={utterance}
              onChange={(e) => setUtterance(e.target.value)}
              className={inputClass}
              placeholder="Escribe tu respuesta…"
              disabled={pending}
            />
            <Button type="submit" disabled={pending || !utterance.trim()}>
              {pending ? "…" : "Enviar"}
            </Button>
          </form>
        </Card>

        {/* Panel de práctica: el riesgo en vivo */}
        <Card className="p-4 h-fit space-y-4">
          <div>
            <p className="text-[12px] text-slate-500">Objetivo</p>
            <p className="text-[13px] text-slate-950 mt-0.5">{session.objective}</p>
          </div>

          <div>
            <p className="text-[12px] text-slate-500">Riesgo del último turno</p>
            <div className="flex items-center gap-2 mt-1">
              <span
                className={`w-2 h-2 rounded-full ${
                  currentRisk === "low"
                    ? "bg-teal-500"
                    : currentRisk === "medium"
                      ? "bg-amber-500"
                      : "bg-red-500"
                }`}
              />
              <span className="text-[13px] text-slate-950 capitalize">{currentRisk}</span>
            </div>
          </div>

          <div>
            <p className="text-[12px] text-slate-500">Turnos marcados</p>
            <p className="text-[20px] font-semibold mono mt-0.5">
              {flagged}
              <span className="text-[13px] text-slate-500"> / {repTurns}</span>
            </p>
          </div>

          <p className="text-[11px] text-slate-500 leading-relaxed border-t border-slate-200 pt-3">
            Se evalúa con el mismo motor de políticas que el asistente en
            producción. Lo que aquí se marca, allí también.
          </p>
        </Card>
      </div>

      {error ? (
        <p role="alert" className="text-[13px] text-red-500 bg-red-50 rounded-[10px] px-3 py-2">
          {error}
        </p>
      ) : null}
    </div>
  );
}
