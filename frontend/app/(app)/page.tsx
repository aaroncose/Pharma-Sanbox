import Link from "next/link";

import { Card, CardHeader, EmptyState, Mono, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { getProfile } from "@/lib/session";

export const dynamic = "force-dynamic";

type Output = {
  id: string;
  trace_id: string;
  kind: string;
  confidence: number;
  risk: string;
  requires_human_review: boolean;
  blocked_reason: string | null;
  created_at: string;
  hcp_name: string | null;
  product_name: string | null;
  source_count: number;
  cost_eur: number;
};

type Stats = {
  totals: {
    events: number;
    non_success: number;
    cross_tenant: number;
    blocked: number;
    cost_eur: number;
  };
  review_queue: Record<string, number>;
};

const KIND_LABELS: Record<string, string> = {
  briefing: "Briefing de visita",
  chat_answer: "Consulta al asistente",
  meeting_summary: "Resumen de visita",
  simulation_feedback: "Informe de simulación",
};

async function safe<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch {
    // Cada tarjeta falla por su cuenta. Un panel que se cae entero porque el
    // usuario no tiene permiso para una de sus consultas es peor que uno que
    // enseña lo que sí puede: obliga a adivinar qué faltó.
    return null;
  }
}

const ROW = "flex items-center gap-4 px-5 py-3";

function Row({
  traceId,
  children,
}: {
  traceId: string | null;
  children: React.ReactNode;
}) {
  if (!traceId) {
    return <div className={ROW}>{children}</div>;
  }
  return (
    <Link
      href={`/audit/trace/${traceId}`}
      className={`${ROW} hover:bg-slate-100/60 transition-colors`}
    >
      {children}
    </Link>
  );
}

export default async function Dashboard() {
  const profile = (await getProfile())!;
  const canSeeTraces = profile.permissions.includes("trace.read");

  const [outputs, stats] = await Promise.all([
    safe(
      api<{ items: Output[] }>("/api/v1/agent/outputs?limit=6"),
    ),
    safe(api<Stats>("/api/v1/audit/stats?since_hours=168")),
  ]);

  const recent = outputs?.items ?? [];
  const needingReview = recent.filter((o) => o.requires_human_review).length;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-[22px] font-semibold tracking-tight">
          Hola, {profile.fullName.split(" ")[0]}
        </h2>
        <p className="text-[14px] text-slate-500 mt-1">
          Todo lo que genera el asistente cita documentación aprobada y deja una
          traza que se puede reconstruir.
        </p>
      </div>

      {/* Accesos del recorrido comercial */}
      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          {
            href: "/briefing",
            title: "Preparar reunión",
            body: "Briefing con citas a material aprobado",
            permission: "briefing.create",
          },
          {
            href: "/chat",
            title: "Asistente documental",
            body: "Pregunta sobre la documentación vigente",
            permission: "chat.use",
          },
          {
            href: "/simulator",
            title: "Simulador",
            body: "Practica con las mismas reglas de producción",
            permission: "simulation.use",
          },
          {
            href: "/summary",
            title: "Resumen posterior",
            body: "Convierte notas en tareas verificadas",
            permission: "summary.create",
          },
        ]
          .filter((card) => profile.permissions.includes(card.permission))
          .map((card) => (
            <Link key={card.href} href={card.href}>
              <Card className="p-4 h-full hover:border-blue-600 transition-colors">
                <p className="text-[14px] font-semibold text-slate-950">
                  {card.title}
                </p>
                <p className="text-[13px] text-slate-500 mt-1">{card.body}</p>
              </Card>
            </Link>
          ))}
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        {/* Actividad reciente */}
        <Card className="lg:col-span-2">
          <CardHeader
            title="Actividad reciente"
            subtitle={
              needingReview > 0
                ? `${needingReview} de ${recent.length} esperan revisión humana`
                : "Generado por el asistente en tu organización"
            }
          />
          {recent.length === 0 ? (
            <EmptyState
              title="Todavía no hay actividad"
              description="Cuando generes un briefing o preguntes al asistente aparecerá aquí, con su traza y su coste."
            />
          ) : (
            <ul className="divide-y divide-slate-200">
              {recent.map((output) => (
                <li key={output.id}>
                  <Row traceId={canSeeTraces ? output.trace_id : null}>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[13px] font-medium text-slate-950">
                          {KIND_LABELS[output.kind] ?? output.kind}
                        </span>
                        {output.blocked_reason ? (
                          <StatusBadge status="rejected" />
                        ) : output.requires_human_review ? (
                          <StatusBadge status="pending" />
                        ) : null}
                      </div>
                      <p className="text-[12px] text-slate-500 mt-0.5 truncate">
                        {[output.hcp_name, output.product_name]
                          .filter(Boolean)
                          .join(" · ") || "Sin contexto asociado"}
                      </p>
                    </div>
                    <div className="text-right shrink-0">
                      <Mono className="text-slate-500">
                        {output.source_count} fuente
                        {output.source_count === 1 ? "" : "s"}
                      </Mono>
                    </div>
                  </Row>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {/* Estado del sistema */}
        <Card>
          <CardHeader
            title="Últimos 7 días"
            subtitle={stats ? "Registro de auditoría" : undefined}
          />
          {!stats ? (
            <EmptyState
              title="Sin visibilidad del registro"
              description="Tu rol no incluye el permiso audit.read. Compliance y auditoría sí lo tienen."
            />
          ) : (
            <dl className="p-5 space-y-4">
              {[
                ["Eventos registrados", stats.totals.events.toLocaleString("es")],
                [
                  "Intentos no exitosos",
                  stats.totals.non_success.toLocaleString("es"),
                ],
                [
                  "Accesos entre organizaciones",
                  stats.totals.cross_tenant.toLocaleString("es"),
                ],
                [
                  "Salidas bloqueadas por política",
                  stats.totals.blocked.toLocaleString("es"),
                ],
                [
                  "Coste de generación",
                  `${stats.totals.cost_eur.toFixed(4)} €`,
                ],
              ].map(([term, value]) => (
                <div key={term} className="flex items-baseline justify-between gap-3">
                  <dt className="text-[13px] text-slate-500">{term}</dt>
                  <dd className="text-[14px] font-medium text-slate-950 mono">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </Card>
      </div>
    </div>
  );
}
