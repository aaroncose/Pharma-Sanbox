import { api } from "@/lib/api";

import { SimulatorClient } from "./SimulatorClient";
import { guard } from "@/components/Guard";

export const dynamic = "force-dynamic";

export default async function SimulatorPage() {
  const denied = await guard("simulation.use");
  if (denied) return denied;

  const [hcps, products] = await Promise.all([
    api<{ items: { id: string; full_name: string; specialty: string }[] }>(
      "/api/v1/hcps?limit=50",
    ).catch(() => ({ items: [] })),
    api<{ items: { id: string; name: string }[] }>("/api/v1/products").catch(
      () => ({ items: [] }),
    ),
  ]);

  return <SimulatorClient hcps={hcps.items} products={products.items} />;
}
