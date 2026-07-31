/**
 * Vocabulario visual compartido.
 *
 * Los componentes de estado (`RiskBadge`, `StatusBadge`, `ConfidenceBar`) son el
 * núcleo: traducen los códigos del backend a algo legible **sin perder el
 * código**. Un badge que dice «Requiere revisión» y esconde
 * `NO_UNAPPROVED_CLAIMS` obliga a compliance a adivinar qué política saltó; uno
 * que solo muestra el código obliga al comercial a aprender el catálogo. Se
 * muestran los dos.
 */

import type { ReactNode } from "react";

/* ── Primitivas ──────────────────────────────────────────────────────────── */

export function Card({
  children,
  className = "",
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article";
}) {
  return (
    <Tag
      className={`bg-white border border-slate-200 rounded-[12px] shadow-[0_1px_2px_rgba(15,23,42,0.04)] ${className}`}
    >
      {children}
    </Tag>
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="flex items-start justify-between gap-4 px-5 py-4 border-b border-slate-200">
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold text-slate-950">{title}</h2>
        {subtitle ? (
          <p className="text-[13px] text-slate-500 mt-0.5">{subtitle}</p>
        ) : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </header>
  );
}

/** Identificador, versión de prompt, código de traza. Siempre monoespaciado. */
export function Mono({
  children,
  className = "",
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span className={`mono text-[12px] ${className}`} title={title}>
      {children}
    </span>
  );
}

/* ── Estado ──────────────────────────────────────────────────────────────── */

const RISK_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  low: { bg: "bg-teal-50", text: "text-teal-500", label: "Riesgo bajo" },
  medium: { bg: "bg-amber-50", text: "text-amber-500", label: "Riesgo medio" },
  high: { bg: "bg-red-50", text: "text-red-500", label: "Riesgo alto" },
  critical: { bg: "bg-red-50", text: "text-red-500", label: "Riesgo crítico" },
};

export function RiskBadge({ level }: { level: string }) {
  const style = RISK_STYLES[level] ?? RISK_STYLES.low;
  return (
    <span
      className={`inline-flex items-center gap-1.5 ${style.bg} ${style.text} rounded-full px-2.5 py-1 text-[12px] font-medium`}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current" aria-hidden />
      {style.label}
    </span>
  );
}

const DOC_STATUS: Record<string, { bg: string; text: string; label: string }> = {
  approved: { bg: "bg-teal-50", text: "text-teal-500", label: "Aprobado" },
  draft: { bg: "bg-slate-100", text: "text-slate-500", label: "Borrador" },
  pending_review: {
    bg: "bg-amber-50",
    text: "text-amber-500",
    label: "Pendiente de revisión",
  },
  withdrawn: { bg: "bg-red-50", text: "text-red-500", label: "Retirado" },
  expired: { bg: "bg-red-50", text: "text-red-500", label: "Caducado" },
  // Cola de revisión
  pending: { bg: "bg-amber-50", text: "text-amber-500", label: "Pendiente" },
  rejected: { bg: "bg-red-50", text: "text-red-500", label: "Rechazado" },
  edited: { bg: "bg-blue-50", text: "text-blue-600", label: "Corregido" },
  regeneration_requested: {
    bg: "bg-blue-50",
    text: "text-blue-600",
    label: "Devuelto",
  },
};

export function StatusBadge({ status }: { status: string }) {
  const style = DOC_STATUS[status] ?? {
    bg: "bg-slate-100",
    text: "text-slate-500",
    label: status,
  };
  return (
    <span
      className={`inline-flex items-center ${style.bg} ${style.text} rounded-md px-2 py-0.5 text-[12px] font-medium`}
    >
      {style.label}
    </span>
  );
}

/**
 * Confianza declarada por el harness.
 *
 * Se muestra el número además de la barra a propósito. Una barra sola invita a
 * leerla como «casi lleno = bien», y 45 sobre 100 en una afirmación sobre un
 * medicamento no es «casi bien»: es contenido que no debería entregarse sin que
 * alguien lo mire.
 */
export function ConfidenceBar({ value }: { value: number }) {
  const tone =
    value >= 70 ? "bg-teal-500" : value >= 40 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2">
      <div
        className="h-1.5 w-24 rounded-full bg-slate-200 overflow-hidden"
        role="img"
        aria-label={`Confianza ${value} sobre 100`}
      >
        <div className={`h-full ${tone}`} style={{ width: `${value}%` }} />
      </div>
      <Mono className="text-slate-700 font-medium">{value}</Mono>
    </div>
  );
}

/**
 * Código de política, con su nombre legible.
 *
 * Los dos, siempre. El código es lo que se busca en la auditoría y lo que
 * aparece en el catálogo; el nombre es lo que permite entenderlo sin
 * consultarlo.
 */
export function PolicyTag({ code, label }: { code: string; label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 bg-slate-100 rounded-md px-2 py-1">
      <Mono className="text-slate-700">{code}</Mono>
      {label ? <span className="text-[12px] text-slate-500">{label}</span> : null}
    </span>
  );
}

/* ── Estados vacíos y de error ───────────────────────────────────────────── */

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="text-center py-12 px-6">
      <p className="text-[14px] font-medium text-slate-700">{title}</p>
      {description ? (
        <p className="text-[13px] text-slate-500 mt-1 max-w-md mx-auto">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/**
 * Un bloqueo del sistema, mostrado como resultado y no como fallo.
 *
 * Es una distinción de producto, no de estilo. Cuando el agente se niega a
 * responder porque no hay documentación que lo respalde, **el sistema ha
 * funcionado**. Presentarlo con la estética de un error enseña al usuario que
 * el control es un estorbo, y de ahí a buscar cómo esquivarlo hay un paso.
 */
export function BlockedNotice({
  reason,
  policyCodes = [],
  gaps = [],
}: {
  reason: string;
  policyCodes?: string[];
  gaps?: string[];
}) {
  return (
    <div className="bg-amber-50 border border-amber-500/30 rounded-[12px] p-5">
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-amber-500" aria-hidden />
        <p className="text-[14px] font-semibold text-slate-950">
          No hay documentación aprobada que respalde una respuesta
        </p>
      </div>
      <p className="text-[13px] text-slate-700 mt-2">
        El sistema ha detenido la respuesta. Esto no es un error: es el
        comportamiento correcto cuando el material disponible no sostiene lo que
        se pregunta.
      </p>

      {gaps.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {gaps.map((gap, i) => (
            <li key={i} className="text-[13px] text-slate-700 flex gap-2">
              <span className="text-amber-500 shrink-0" aria-hidden>
                ·
              </span>
              {gap}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 mt-4">
        <Mono className="text-slate-500">{reason}</Mono>
        {policyCodes.map((code) => (
          <PolicyTag key={code} code={code} />
        ))}
      </div>
    </div>
  );
}

/* ── Controles ───────────────────────────────────────────────────────────── */

export function Button({
  children,
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
}) {
  const variants = {
    primary: "bg-blue-600 text-white hover:bg-blue-500 disabled:bg-slate-300",
    secondary:
      "bg-white text-slate-700 border border-slate-300 hover:bg-slate-100 disabled:text-slate-300",
    ghost: "text-slate-700 hover:bg-slate-100",
    danger: "bg-red-500 text-white hover:bg-red-500/90",
  };
  const sizes = { sm: "px-3 py-1.5 text-[13px]", md: "px-4 py-2 text-[14px]" };

  return (
    <button
      className={`rounded-[10px] font-medium transition-colors disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="block text-[13px] font-medium text-slate-700 mb-1.5">
        {label}
      </span>
      {children}
      {error ? (
        <span className="block text-[12px] text-red-500 mt-1">{error}</span>
      ) : hint ? (
        <span className="block text-[12px] text-slate-500 mt-1">{hint}</span>
      ) : null}
    </label>
  );
}

export const inputClass =
  "w-full rounded-[10px] border border-slate-300 px-3 py-2 text-[14px] " +
  "placeholder:text-slate-500/60 focus:border-blue-600 focus:outline-none " +
  "focus:ring-2 focus:ring-blue-600/20";
