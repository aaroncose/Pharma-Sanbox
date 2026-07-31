"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, Field, inputClass } from "@/components/ui";

import { approve, edit, reject, requestRegeneration } from "../actions";

/**
 * Panel de decisión.
 *
 * Tres cosas que la interfaz hace explícitas porque el backend las exige y
 * ocultarlas produciría errores que el usuario no entiende:
 *
 * **El motivo se pide antes de habilitar el botón.** El backend exige 20
 * caracteres reales. Dejar pulsar y devolver un 422 enseña que la validación es
 * un obstáculo; pedirlo antes enseña que es parte de decidir.
 *
 * **Aprobar cuesta lo mismo que rechazar.** Si aprobar fuera un clic y rechazar
 * exigiera justificarse, la cola se vaciaría aprobando. Los cuatro desenlaces
 * piden lo mismo.
 *
 * **Se avisa de que la decisión es definitiva.** El backend devuelve 409 al
 * segundo intento porque `decided_by` registra quién se hizo responsable, y eso
 * no puede reasignarse después. Decirlo antes evita que alguien decida rápido
 * pensando que puede corregirlo luego.
 */

const MIN_RATIONALE = 20;

type Outcome = "approve" | "edit" | "reject" | "regenerate";

const OUTCOMES: { value: Outcome; label: string; help: string }[] = [
  {
    value: "approve",
    label: "Aprobar",
    help: "El contenido es correcto tal cual. Queda registrado como falso positivo del sistema, que es el único dato con el que se pueden relajar los umbrales.",
  },
  {
    value: "edit",
    label: "Corregir y aprobar",
    help: "Reescribes el contenido. El original se conserva: el par (lo que dijo, lo que debía decir) es un caso de evaluación.",
  },
  {
    value: "reject",
    label: "Rechazar",
    help: "El contenido no se entrega. Se conserva como ejemplo de lo que hay que seguir deteniendo.",
  },
  {
    value: "regenerate",
    label: "Devolver al comercial",
    help: "El caso es respondible pero esta respuesta no vale. No se regenera aquí: hacerlo te convertiría en autor del contenido que revisas.",
  },
];

export function DecisionPanel({
  reviewItemId,
  originalContent,
}: {
  reviewItemId: string;
  originalContent: string;
}) {
  const router = useRouter();
  const [outcome, setOutcome] = useState<Outcome>("approve");
  const [rationale, setRationale] = useState("");
  const [editedContent, setEditedContent] = useState(originalContent);
  const [expected, setExpected] = useState("");
  const [guidance, setGuidance] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rationaleOk = rationale.trim().length >= MIN_RATIONALE;
  const editOk = outcome !== "edit" || editedContent.trim().length >= 10;
  const canSubmit = rationaleOk && editOk && !pending;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    setPending(true);
    setError(null);

    const result =
      outcome === "approve"
        ? await approve(reviewItemId, rationale)
        : outcome === "reject"
          ? await reject(reviewItemId, rationale)
          : outcome === "edit"
            ? await edit(reviewItemId, rationale, editedContent, expected)
            : await requestRegeneration(reviewItemId, rationale, guidance);

    if (result.ok) {
      router.push("/compliance");
      router.refresh();
      return;
    }

    setError(
      result.rule === "REVIEW_DECISION_IS_FINAL"
        ? "Otra persona ya resolvió este elemento. Una decisión de compliance no se sobrescribe."
        : result.message,
    );
    setPending(false);
  }

  const selected = OUTCOMES.find((o) => o.value === outcome)!;

  return (
    <form onSubmit={submit} className="space-y-5">
      <fieldset>
        <legend className="text-[13px] font-medium text-slate-700 mb-2">
          Decisión
        </legend>
        <div className="grid sm:grid-cols-2 gap-2">
          {OUTCOMES.map((option) => (
            <label
              key={option.value}
              className={`cursor-pointer rounded-[10px] border px-3 py-2.5 transition-colors ${
                outcome === option.value
                  ? "border-blue-600 bg-blue-50"
                  : "border-slate-200 hover:border-slate-300"
              }`}
            >
              <input
                type="radio"
                name="outcome"
                value={option.value}
                checked={outcome === option.value}
                onChange={() => setOutcome(option.value)}
                className="sr-only"
              />
              <span className="text-[13px] font-medium text-slate-950">
                {option.label}
              </span>
            </label>
          ))}
        </div>
        <p className="text-[12px] text-slate-500 mt-2">{selected.help}</p>
      </fieldset>

      {outcome === "edit" ? (
        <>
          <Field
            label="Contenido corregido"
            hint="El original se conserva sin tocar. La comparación entre los dos es lo que hace útil la corrección."
          >
            <textarea
              value={editedContent}
              onChange={(e) => setEditedContent(e.target.value)}
              rows={6}
              className={`${inputClass} resize-y`}
            />
          </Field>
          <Field
            label="Qué debería hacer el sistema la próxima vez"
            hint="Opcional. Es lo que convierte la corrección en un caso de evaluación y no solo en un arreglo puntual."
          >
            <input
              value={expected}
              onChange={(e) => setExpected(e.target.value)}
              className={inputClass}
              placeholder="p. ej. No debe convertir mmHg en porcentaje de reducción de riesgo"
            />
          </Field>
        </>
      ) : null}

      {outcome === "regenerate" ? (
        <Field label="Indicaciones para quien lo vuelva a intentar" hint="Opcional">
          <input
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            className={inputClass}
            placeholder="p. ej. Reformular sin la cifra porcentual"
          />
        </Field>
      ) : null}

      <Field
        label="Motivo de la decisión"
        hint={`Mínimo ${MIN_RATIONALE} caracteres. Es lo que leerá quien audite esto dentro de seis meses.`}
        error={
          rationale.length > 0 && !rationaleOk
            ? `Faltan ${MIN_RATIONALE - rationale.trim().length} caracteres`
            : undefined
        }
      >
        <textarea
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={3}
          className={`${inputClass} resize-y`}
          placeholder="Por qué tomas esta decisión"
        />
      </Field>

      {error ? (
        <p role="alert" className="text-[13px] text-red-500 bg-red-50 rounded-[10px] px-3 py-2">
          {error}
        </p>
      ) : null}

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={!canSubmit}>
          {pending ? "Registrando…" : "Registrar decisión"}
        </Button>
        <p className="text-[12px] text-slate-500">
          Definitiva: queda tu nombre y no se puede reescribir.
        </p>
      </div>
    </form>
  );
}
