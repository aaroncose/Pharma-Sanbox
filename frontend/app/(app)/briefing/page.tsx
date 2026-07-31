import { api } from "@/lib/api";

import { BriefingClient } from "./BriefingClient";
import { guard } from "@/components/Guard";

export const dynamic = "force-dynamic";

export default async function BriefingPage() {
  const denied = await guard("briefing.create");
  if (denied) return denied;

  const [hcps, products] = await Promise.all([
    api<{ items: { id: string; full_name: string; specialty: string; institution: string; consent_data_analysis: boolean }[] }>(
      "/api/v1/hcps?limit=50",
    ).catch(() => ({ items: [] })),
    api<{ items: { id: string; name: string; therapeutic_area: string }[] }>(
      "/api/v1/products",
    ).catch(() => ({ items: [] })),
  ]);

  return <BriefingClient hcps={hcps.items} products={products.items} />;
}
