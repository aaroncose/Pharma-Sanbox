import Link from "next/link";

import { Card, CardHeader, EmptyState, Mono, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { guard } from "@/components/Guard";

export const dynamic = "force-dynamic";

type Item = {
  id: string;
  subject_type: string;
  reason: string;
  policy_code: string | null;
  priority: string;
  status: string;
  original_content: string;
  created_at: string;
  requested_by_name: string;
  decided_by_name: string | null;
  confidence: number | null;
  risk: string | null;
  model: string | null;
  waiting_hours: number;
};

const PRIORITY: Record<string, { dot: string; label: string }> = {
  high: { dot: "bg-red-500", label: "Alta" },
  medium: { dot: "bg-amber-500", label: "Media" },
  low: { dot: "bg-slate-300", label: "Baja" },
};

export default async function CompliancePage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const denied = await guard("review.read");
  if (denied) return denied;

  const { status = "pending" } = await searchParams;
  const { items, totals } = await api<{
    items: Item[];
    totals: Record<string, number>;
  }>(`/api/v1/review?status=${encodeURIComponent(status)}&limit=100`);

  const oldest = items.length ? Math.max(...items.map((i) => i.waiting_hours)) : 0;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-[22px] font-semibold tracking-tight">
          Cola de revisión
        </h2>
        <p className="text-[14px] text-slate-500 mt-1">
          Todo lo que el sistema marcó llega aquí. Cada decisión exige un motivo
          escrito y no se puede reescribir después.
        </p>
      </div>

      <div className="grid sm:grid-cols-3 gap-4">
        <Card className="p-4">
          <p className="text-[13px] text-slate-500">Pendientes</p>
          <p className="text-[24px] font-semibold mt-1 mono text-amber-500">
            {totals.pending ?? 0}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-[13px] text-slate-500">Lo que más lleva esperando</p>
          <p className="text-[24px] font-semibold mt-1 mono">
            {oldest.toFixed(1)} h
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-[13px] text-slate-500">Resueltas</p>
          <p className="text-[24px] font-semibold mt-1 mono">
            {(totals.approved ?? 0) +
              (totals.rejected ?? 0) +
              (totals.edited ?? 0) +
              (totals.regeneration_requested ?? 0)}
          </p>
        </Card>
      </div>

      <div className="flex flex-wrap gap-2">
        {[
          ["pending", "Pendientes"],
          ["approved", "Aprobadas"],
          ["edited", "Corregidas"],
          ["rejected", "Rechazadas"],
          ["all", "Todas"],
        ].map(([value, label]) => (
          <Link
            key={value}
            href={`/compliance?status=${value}`}
            className={`text-[13px] rounded-[10px] px-3 py-1.5 border transition-colors ${
              status === value
                ? "border-blue-600 bg-blue-50 text-blue-600"
                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
            }`}
          >
            {label}
          </Link>
        ))}
      </div>

      <Card>
        <CardHeader
          title="Elementos"
          subtitle="Ordenados por prioridad y después por antigüedad, no solo por llegada"
        />
        {items.length === 0 ? (
          <EmptyState
            title="No hay nada esperando"
            description="Cuando el harness marque una salida para revisión aparecerá aquí, con el motivo por el que la marcó."
          />
        ) : (
          <ul className="divide-y divide-slate-200">
            {items.map((item) => {
              const priority = PRIORITY[item.priority] ?? PRIORITY.low;
              return (
                <li key={item.id}>
                  <Link
                    href={`/compliance/${item.id}`}
                    className="block px-5 py-4 hover:bg-slate-100/50 transition-colors"
                  >
                    <div className="flex items-start gap-3">
                      <span
                        className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${priority.dot}`}
                        aria-label={`Prioridad ${priority.label}`}
                      />
                      <div className="min-w-0 flex-1">
                        <p className="text-[13px] font-medium text-slate-950">
                          {item.reason}
                        </p>
                        <p className="text-[13px] text-slate-500 mt-1 line-clamp-2">
                          {item.original_content}
                        </p>
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2">
                          {item.policy_code ? (
                            <Mono className="text-slate-500">
                              {item.policy_code}
                            </Mono>
                          ) : null}
                          <span className="text-[12px] text-slate-500">
                            {item.requested_by_name}
                          </span>
                          <Mono className="text-slate-500">
                            {item.waiting_hours.toFixed(1)} h esperando
                          </Mono>
                          {item.status !== "pending" ? (
                            <StatusBadge status={item.status} />
                          ) : null}
                        </div>
                      </div>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
