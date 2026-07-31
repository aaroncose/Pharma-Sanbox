import { api } from "@/lib/api";

import { SummaryClient } from "./SummaryClient";

export const dynamic = "force-dynamic";

export default async function SummaryPage() {
  const [hcps, products] = await Promise.all([
    api<{ items: { id: string; full_name: string; specialty: string }[] }>(
      "/api/v1/hcps?limit=50",
    ).catch(() => ({ items: [] })),
    api<{ items: { id: string; name: string }[] }>("/api/v1/products").catch(
      () => ({ items: [] }),
    ),
  ]);

  return <SummaryClient hcps={hcps.items} products={products.items} />;
}
