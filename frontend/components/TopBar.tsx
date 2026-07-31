"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import type { Profile } from "@/lib/session";

/**
 * Barra superior: quién eres, dónde estás y cómo salir.
 *
 * El aviso de entorno de demostración va aquí y es fijo. No se puede cerrar
 * porque un aviso que se cierra deja de avisar en cuanto alguien lo cierra, y
 * lo que advierte —que CardioX, NeuroBalance y los profesionales sanitarios son
 * inventados— no deja de ser cierto por haberlo leído una vez.
 */
export function TopBar({ profile, title }: { profile: Profile; title?: string }) {
  const router = useRouter();
  const [leaving, setLeaving] = useState(false);

  async function logout() {
    setLeaving(true);
    await fetch("/api/auth/logout", { method: "POST" });
    router.replace("/login");
    router.refresh();
  }

  return (
    <header className="bg-white border-b border-slate-200">
      <div className="demo-banner">Demo environment — synthetic data only</div>

      <div className="flex items-center justify-between gap-4 px-6 py-3">
        <h1 className="text-[15px] font-semibold tracking-tight text-slate-950">
          {title ?? "Pharma Commercial AI Sandbox"}
        </h1>

        <div className="flex items-center gap-4">
          <div className="text-right hidden sm:block">
            <p className="text-[13px] font-medium text-slate-950 leading-tight">
              {profile.fullName}
            </p>
            <p className="text-[12px] text-slate-500 leading-tight">
              {profile.tenantName}
            </p>
          </div>
          <button
            onClick={logout}
            disabled={leaving}
            className="text-[13px] text-slate-500 hover:text-slate-950 transition-colors disabled:opacity-50"
          >
            {leaving ? "Saliendo…" : "Salir"}
          </button>
        </div>
      </div>
    </header>
  );
}
