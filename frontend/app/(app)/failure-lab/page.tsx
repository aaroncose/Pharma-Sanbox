import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { getProfile } from "@/lib/session";

import { LabClient, type Scenario } from "./LabClient";

export const dynamic = "force-dynamic";

export default async function FailureLabPage() {
  const profile = (await getProfile())!;
  const canRun = profile.permissions.includes("failure_lab.run");

  const data = await api<{
    items: Scenario[];
    count: number;
    executed: number;
    passed: number;
    failed: number;
  }>("/api/v1/failure-lab");

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-[22px] font-semibold tracking-tight">
          Failure Lab
        </h2>
        <p className="text-[14px] text-slate-500 mt-1 max-w-3xl">
          Entorno controlado para validar seguridad, políticas y recuperación.
          Cada escenario ataca el sistema real y observa cómo responde: la
          prueba de aislamiento consulta una interacción que existe y pertenece a
          otra organización, y la de caída del proveedor provoca un fallo en el
          mismo punto donde fallaría la API del modelo.
        </p>
        <p className="text-[13px] text-slate-500 mt-2">
          Ninguna prueba utiliza datos reales.
        </p>
      </div>

      {data.executed > 0 ? (
        <Card
          className={`p-4 ${
            data.failed === 0
              ? "border-teal-500/30 bg-teal-50/40"
              : "border-red-500/40 bg-red-50"
          }`}
        >
          <p className="text-[13px] font-medium text-slate-950">
            {data.failed === 0
              ? `${data.passed} de ${data.executed} escenarios ejecutados se comportaron como debían`
              : `${data.failed} escenario${data.failed === 1 ? "" : "s"} no se comportó como debía`}
          </p>
          <p className="text-[12px] text-slate-700 mt-1">
            {data.executed < data.count
              ? `Quedan ${data.count - data.executed} sin ejecutar.`
              : "Todos los escenarios se han ejecutado al menos una vez."}
          </p>
        </Card>
      ) : null}

      {!canRun ? (
        <Card className="p-4">
          <p className="text-[13px] text-slate-700">
            Tu rol puede consultar los escenarios y sus resultados, pero no
            ejecutarlos. Requiere el permiso{" "}
            <span className="mono">failure_lab.run</span>.
          </p>
        </Card>
      ) : null}

      <LabClient scenarios={data.items} canRun={canRun} />
    </div>
  );
}
