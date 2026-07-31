import Link from "next/link";

import { Card, CardHeader, EmptyState, Mono } from "@/components/ui";
import { api } from "@/lib/api";
import { getProfile } from "@/lib/session";
import { guard } from "@/components/Guard";

export const dynamic = "force-dynamic";

type Event = {
  id: string;
  occurred_at: string;
  trace_id: string;
  action: string;
  outcome: string;
  decision_code: string | null;
  resource_type: string | null;
  resource_id: string | null;
  policy_code: string | null;
  exposed_field_count: number;
  actor_name: string | null;
  actor_role: string | null;
  cross_tenant: boolean;
};

const OUTCOME_STYLE: Record<string, string> = {
  success: "text-teal-500",
  denied: "text-red-500",
  blocked: "text-amber-500",
  error: "text-red-500",
};

export default async function AuditPage({
  searchParams,
}: {
  searchParams: Promise<{ view?: string }>;
}) {
  const denied = await guard("audit.read");
  if (denied) return denied;

  const { view = "all" } = await searchParams;
  const profile = (await getProfile())!;
  const canExport = profile.permissions.includes("audit.export");

  const path =
    view === "security"
      ? "/api/v1/audit/security?since_hours=8760&limit=150"
      : "/api/v1/audit?limit=150";

  const data = await api<{
    items: Event[];
    count: number;
    leaked_rows?: number;
  }>(path);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[22px] font-semibold tracking-tight">Auditoría</h2>
          <p className="text-[14px] text-slate-500 mt-1">
            Registro append-only. Ni la API ni el rol de aplicación pueden
            modificarlo: hay un trigger que rechaza UPDATE y DELETE.
          </p>
        </div>
        {canExport ? (
          <a
            href="/api/v1/audit/export?since_hours=720"
            className="shrink-0 text-[13px] rounded-[10px] border border-slate-300 bg-white px-3 py-2 hover:bg-slate-100"
          >
            Exportar CSV
          </a>
        ) : null}
      </div>

      {/* La afirmación comprobable: en un intento denegado no salió ni un campo.
          Si alguna fila la contradijera, hay que verlo en pantalla y no solo en
          una prueba que quizá nadie ejecuta hoy. */}
      {view === "security" && data.leaked_rows !== undefined ? (
        <Card
          className={`p-4 ${
            data.leaked_rows === 0
              ? "border-teal-500/30 bg-teal-50/40"
              : "border-red-500/40 bg-red-50"
          }`}
        >
          <p className="text-[13px] font-medium text-slate-950">
            {data.leaked_rows === 0
              ? "Ningún intento denegado expuso campos"
              : `${data.leaked_rows} intentos denegados expusieron campos`}
          </p>
          <p className="text-[12px] text-slate-500 mt-1">
            Cada evento denegado declara cuántos campos salieron hacia el
            cliente. Es un dato del registro, no una deducción a partir del
            código de estado.
          </p>
        </Card>
      ) : null}

      <div className="flex gap-2">
        {[
          ["all", "Todos los eventos"],
          ["security", "Solo denegados y bloqueados"],
        ].map(([value, label]) => (
          <Link
            key={value}
            href={`/audit?view=${value}`}
            className={`text-[13px] rounded-[10px] px-3 py-1.5 border transition-colors ${
              view === value
                ? "border-blue-600 bg-blue-50 text-blue-600"
                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
            }`}
          >
            {label}
          </Link>
        ))}
      </div>

      <Card>
        <CardHeader title={`${data.count} eventos`} />
        {data.items.length === 0 ? (
          <EmptyState title="Sin eventos con ese filtro" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="text-[12px] text-slate-500 border-b border-slate-200">
                  <th className="font-medium px-5 py-2.5">Momento</th>
                  <th className="font-medium px-3 py-2.5">Acción</th>
                  <th className="font-medium px-3 py-2.5">Resultado</th>
                  <th className="font-medium px-3 py-2.5">Actor</th>
                  <th className="font-medium px-3 py-2.5">Campos expuestos</th>
                  <th className="font-medium px-5 py-2.5">Traza</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {data.items.map((event) => (
                  <tr key={event.id} className="hover:bg-slate-100/50">
                    <td className="px-5 py-2.5">
                      <Mono className="text-slate-500">
                        {new Date(event.occurred_at).toLocaleString("es", {
                          day: "2-digit",
                          month: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                      </Mono>
                    </td>
                    <td className="px-3 py-2.5">
                      <Mono className="text-slate-950">{event.action}</Mono>
                      {event.cross_tenant ? (
                        <span className="ml-2 text-[11px] text-red-500 font-medium">
                          entre organizaciones
                        </span>
                      ) : null}
                    </td>
                    <td className="px-3 py-2.5">
                      <span
                        className={`text-[12px] font-medium ${
                          OUTCOME_STYLE[event.outcome] ?? "text-slate-500"
                        }`}
                      >
                        {event.outcome}
                      </span>
                      {event.decision_code ? (
                        <p className="mono text-[11px] text-slate-500">
                          {event.decision_code}
                        </p>
                      ) : null}
                    </td>
                    <td className="px-3 py-2.5 text-[13px] text-slate-700">
                      {event.actor_name ?? "—"}
                      {event.actor_role ? (
                        <p className="text-[11px] text-slate-500">
                          {event.actor_role}
                        </p>
                      ) : null}
                    </td>
                    <td className="px-3 py-2.5">
                      <Mono
                        className={
                          event.outcome !== "success" &&
                          event.exposed_field_count > 0
                            ? "text-red-500 font-semibold"
                            : "text-slate-500"
                        }
                      >
                        {event.exposed_field_count}
                      </Mono>
                    </td>
                    <td className="px-5 py-2.5">
                      <Link
                        href={`/audit/trace/${event.trace_id}`}
                        className="mono text-[12px] text-blue-600 hover:underline"
                      >
                        {event.trace_id}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
