"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { Button } from "@/components/ui";

import { compareVersions, runSuite } from "./actions";

export function RunControls({
  versions,
  canRun,
}: {
  versions: string[];
  canRun: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  if (!canRun) {
    return (
      <p className="text-[12px] text-slate-500 max-w-xs text-right">
        Tu rol puede consultar los resultados pero no ejecutar la suite. Requiere
        el permiso <span className="mono">eval.run</span>.
      </p>
    );
  }

  const launch = (action: () => Promise<{ ok: boolean; message?: string }>) => {
    setError(null);
    startTransition(async () => {
      const result = await action();
      if (!result.ok) setError(result.message ?? "Error");
      else router.refresh();
    });
  };

  const comparable = versions.length >= 2;

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex gap-2">
        <Button
          variant="secondary"
          size="sm"
          disabled={pending || !comparable}
          onClick={() => launch(() => compareVersions(versions.slice(-2)))}
          title={
            comparable
              ? `Compara ${versions.slice(-2).join(" con ")}`
              : "Hace falta más de una versión del prompt"
          }
        >
          {pending ? "Ejecutando…" : "Comparar dos versiones"}
        </Button>
        <Button
          size="sm"
          disabled={pending}
          onClick={() => launch(() => runSuite(null))}
        >
          {pending ? "Ejecutando…" : "Ejecutar suite"}
        </Button>
      </div>
      {error ? <p className="text-[12px] text-red-500">{error}</p> : null}
      <p className="text-[11px] text-slate-500">
        Se ejecuta contra el proveedor determinista: mismo resultado en cada
        corrida, que es lo que hace comparables dos versiones.
      </p>
    </div>
  );
}
