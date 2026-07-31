import Link from "next/link";

import { Card, CardHeader, EmptyState, Mono, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { guard } from "@/components/Guard";

export const dynamic = "force-dynamic";

type Doc = {
  id: string;
  title: string;
  doc_type: string;
  status: string;
  version: string;
  confidentiality: string;
  approved_at: string | null;
  expires_at: string | null;
  withdrawn_at: string | null;
  withdrawn_reason: string | null;
  product_name: string | null;
  chunk_count: number;
  citable: boolean;
};

const DOC_TYPES: Record<string, string> = {
  ficha_producto: "Ficha de producto",
  faq: "FAQ comercial",
  estudio: "Estudio",
  politica: "Política interna",
  material: "Material comercial",
  seguridad: "Información de seguridad",
};

export default async function LibraryPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const denied = await guard("document.read");
  if (denied) return denied;

  const { status } = await searchParams;
  const query = status ? `?status=${encodeURIComponent(status)}&limit=100` : "?limit=100";
  const { items } = await api<{ items: Doc[] }>(`/api/v1/library/documents${query}`);

  const citable = items.filter((d) => d.citable).length;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-[22px] font-semibold tracking-tight">
          Biblioteca documental
        </h2>
        <p className="text-[14px] text-slate-500 mt-1">
          El agente solo puede citar documentos aprobados, vigentes y no
          retirados. Esta columna lo dice documento a documento.
        </p>
      </div>

      {/* El dato que resume el gobierno documental: cuántos de los que hay
          puede realmente usar el agente. */}
      <div className="grid sm:grid-cols-3 gap-4">
        <Card className="p-4">
          <p className="text-[13px] text-slate-500">Documentos</p>
          <p className="text-[24px] font-semibold mt-1 mono">{items.length}</p>
        </Card>
        <Card className="p-4">
          <p className="text-[13px] text-slate-500">Citables por el agente</p>
          <p className="text-[24px] font-semibold mt-1 mono text-teal-500">
            {citable}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-[13px] text-slate-500">Fuera de alcance</p>
          <p className="text-[24px] font-semibold mt-1 mono text-slate-500">
            {items.length - citable}
          </p>
          <p className="text-[12px] text-slate-500 mt-1">
            Borradores, retirados o caducados
          </p>
        </Card>
      </div>

      <div className="flex flex-wrap gap-2">
        {[
          ["", "Todos"],
          ["approved", "Aprobados"],
          ["draft", "Borradores"],
          ["pending_review", "Pendientes"],
          ["withdrawn", "Retirados"],
        ].map(([value, label]) => (
          <Link
            key={value}
            href={value ? `/library?status=${value}` : "/library"}
            className={`text-[13px] rounded-[10px] px-3 py-1.5 border transition-colors ${
              (status ?? "") === value
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
          title="Documentos"
          subtitle="Ordenados por última modificación"
        />
        {items.length === 0 ? (
          <EmptyState title="No hay documentos con ese filtro" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="text-[12px] text-slate-500 border-b border-slate-200">
                  <th className="font-medium px-5 py-2.5">Documento</th>
                  <th className="font-medium px-3 py-2.5">Tipo</th>
                  <th className="font-medium px-3 py-2.5">Estado</th>
                  <th className="font-medium px-3 py-2.5">Versión</th>
                  <th className="font-medium px-3 py-2.5">Fragmentos</th>
                  <th className="font-medium px-5 py-2.5">Citable</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {items.map((doc) => (
                  <tr key={doc.id} className="hover:bg-slate-100/50">
                    <td className="px-5 py-3">
                      <Link
                        href={`/library/${doc.id}`}
                        className="text-[13px] font-medium text-slate-950 hover:text-blue-600"
                      >
                        {doc.title}
                      </Link>
                      {doc.product_name ? (
                        <p className="text-[12px] text-slate-500">
                          {doc.product_name}
                        </p>
                      ) : null}
                    </td>
                    <td className="px-3 py-3 text-[13px] text-slate-700">
                      {DOC_TYPES[doc.doc_type] ?? doc.doc_type}
                    </td>
                    <td className="px-3 py-3">
                      <StatusBadge status={doc.status} />
                    </td>
                    <td className="px-3 py-3">
                      <Mono className="text-slate-700">{doc.version}</Mono>
                    </td>
                    <td className="px-3 py-3">
                      <Mono className="text-slate-500">{doc.chunk_count}</Mono>
                    </td>
                    <td className="px-5 py-3">
                      {/* Se indexa todo y se filtra al leer: un borrador tiene
                          fragmentos y no es citable. La columna hace visible
                          esa diferencia, que es donde vive el control. */}
                      {doc.citable ? (
                        <span className="text-[12px] font-medium text-teal-500">
                          Sí
                        </span>
                      ) : (
                        <span
                          className="text-[12px] text-slate-500"
                          title={
                            doc.withdrawn_reason ??
                            "El estado del documento lo deja fuera del alcance del agente"
                          }
                        >
                          No
                        </span>
                      )}
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
