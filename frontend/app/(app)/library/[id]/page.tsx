import Link from "next/link";
import { notFound } from "next/navigation";

import { Card, CardHeader, EmptyState, Mono, StatusBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { guard } from "@/components/Guard";

export const dynamic = "force-dynamic";

type Chunk = {
  ordinal: number;
  section: string | null;
  length: number;
};

type Document = {
  id: string;
  title: string;
  doc_type: string;
  status: string;
  version: string;
  confidentiality: string;
  body: string | null;
  approved_at: string | null;
  approved_by: string | null;
  expires_at: string | null;
  withdrawn_at: string | null;
  withdrawn_reason: string | null;
  product_name: string | null;
  created_at: string | null;
  updated_at: string | null;
  citable: boolean;
  chunks: Chunk[];
};

const DOC_TYPES: Record<string, string> = {
  ficha_producto: "Ficha de producto",
  faq: "FAQ comercial",
  estudio: "Estudio",
  politica: "Política interna",
  material: "Material comercial",
  seguridad: "Información de seguridad",
};

function fecha(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("es", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default async function DocumentPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const denied = await guard("document.read");
  if (denied) return denied;

  const { id } = await params;

  let doc: Document;
  try {
    doc = await api<Document>(`/api/v1/library/documents/${id}`);
  } catch {
    notFound();
  }

  const motivo = doc.withdrawn_at
    ? "Retirado: el agente no puede citarlo."
    : doc.status !== "approved"
      ? "No está aprobado todavía."
      : doc.expires_at && new Date(doc.expires_at) < new Date()
        ? "Aprobado pero caducado."
        : null;

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/library"
          className="text-[13px] text-blue-600 hover:underline"
        >
          ← Biblioteca documental
        </Link>
        <h2 className="text-[22px] font-semibold tracking-tight mt-2">
          {doc.title}
        </h2>
        <div className="flex items-center gap-2 mt-2">
          <StatusBadge status={doc.status} />
          <Mono className="text-slate-500">{doc.version}</Mono>
          <span className="text-[13px] text-slate-500">
            {DOC_TYPES[doc.doc_type] ?? doc.doc_type}
          </span>
        </div>
      </div>

      <Card className="p-5">
        <p className="text-[13px] font-semibold text-slate-950">
          {doc.citable ? "El agente puede citarlo" : "El agente no puede citarlo"}
        </p>
        <p className="text-[13px] text-slate-500 mt-1">
          {doc.citable
            ? "Aprobado, vigente y no retirado: cumple las tres condiciones que exige la recuperación."
            : (motivo ?? "No cumple alguna de las condiciones de citabilidad.")}
        </p>
      </Card>

      <div className="grid lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader title="Ficha" />
          <dl className="p-5 space-y-3">
            {(
              [
                ["Producto", doc.product_name ?? "—"],
                ["Confidencialidad", doc.confidentiality],
                ["Aprobado", fecha(doc.approved_at)],
                ["Caduca", fecha(doc.expires_at)],
                ["Retirado", fecha(doc.withdrawn_at)],
                ["Motivo de retirada", doc.withdrawn_reason ?? "—"],
                ["Última modificación", fecha(doc.updated_at)],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className="flex justify-between gap-4">
                <dt className="text-[13px] text-slate-500">{label}</dt>
                <dd className="text-[13px] text-slate-900 text-right">
                  {value}
                </dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card>
          <CardHeader
            title="Fragmentos indexados"
            subtitle={`${doc.chunks.length} fragmento${doc.chunks.length === 1 ? "" : "s"} recuperables`}
          />
          {doc.chunks.length === 0 ? (
            <EmptyState
              title="Sin fragmentos"
              description="El documento no se ha indexado, así que la recuperación nunca lo devolverá."
            />
          ) : (
            <ul className="divide-y divide-slate-200">
              {doc.chunks.map((chunk) => (
                <li
                  key={chunk.ordinal}
                  className="flex items-center gap-4 px-5 py-2.5"
                >
                  <Mono className="text-slate-500 w-8 shrink-0">
                    {chunk.ordinal}
                  </Mono>
                  <span className="text-[13px] text-slate-900 flex-1 truncate">
                    {chunk.section ?? "Sin título de sección"}
                  </span>
                  <Mono className="text-slate-500 shrink-0">
                    {chunk.length} car.
                  </Mono>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {doc.body ? (
        <Card>
          <CardHeader title="Contenido" subtitle="Tal y como se indexó" />
          <pre className="px-5 pb-5 text-[13px] text-slate-800 whitespace-pre-wrap font-sans">
            {doc.body}
          </pre>
        </Card>
      ) : null}
    </div>
  );
}
