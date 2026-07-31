import { redirect } from "next/navigation";

import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import { api } from "@/lib/api";
import { getProfile } from "@/lib/session";

/**
 * Envoltura de todo lo autenticado.
 *
 * La comprobación de sesión se hace en el servidor y **antes** de renderizar
 * nada. Un guardián en el cliente dejaría ver la estructura de la aplicación
 * durante un instante a quien no ha entrado, y aunque los datos no llegarían
 * —el backend los deniega— enseñar el esqueleto ya dice qué módulos existen.
 */
export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const profile = await getProfile();
  if (!profile) redirect("/login");

  // El contador de la cola. Falla en silencio a propósito: que compliance no
  // pueda ver un número no es motivo para dejar sin navegación a nadie.
  let pending = 0;
  if (profile.permissions.includes("review.read")) {
    try {
      const queue = await api<{ totals: Record<string, number> }>(
        "/api/v1/review?status=pending&limit=1",
      );
      pending = queue.totals?.pending ?? 0;
    } catch {
      pending = 0;
    }
  }

  return (
    <div className="min-h-screen flex">
      <Sidebar profile={profile} pendingReviews={pending} />
      <div className="flex-1 min-w-0 flex flex-col">
        <TopBar profile={profile} />
        <main className="flex-1 p-6 max-w-[1400px] w-full">{children}</main>
      </div>
    </div>
  );
}
