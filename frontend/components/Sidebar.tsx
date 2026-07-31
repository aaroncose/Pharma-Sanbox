"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import type { Profile } from "@/lib/session";

/**
 * Navegación lateral.
 *
 * **Los módulos sin permiso se muestran desactivados, no se ocultan.**
 *
 * Ocultarlos es lo habitual y aquí sería un error. Este producto existe para
 * enseñar que los permisos son reales; una interfaz que se limita a esconder lo
 * que no se puede usar deja al usuario sin forma de distinguir «este sistema no
 * tiene auditoría» de «tú no puedes verla», y a quien evalúa el producto sin
 * forma de ver que la matriz de roles existe.
 *
 * Que no es un control de acceso está claro: el permiso lo comprueba el backend
 * en cada petición, y estas etiquetas podrían falsificarse desde la consola sin
 * conseguir nada. Son una explicación, no una puerta.
 */

const ROLE_LABELS: Record<string, string> = {
  sales_rep: "Comercial",
  compliance_officer: "Compliance",
  org_admin: "Administración",
  auditor: "Auditoría",
  platform_superadmin: "Plataforma",
};

type Item = {
  href: string;
  label: string;
  icon: string;
  /** Permiso que exige el backend para el endpoint principal del módulo. */
  permission?: string;
};

const SECTIONS: { title: string; items: Item[] }[] = [
  {
    title: "Principal",
    items: [
      { href: "/", label: "Inicio", icon: "▣" },
      {
        href: "/briefing",
        label: "Preparar reunión",
        icon: "◫",
        permission: "briefing.create",
      },
      {
        href: "/chat",
        label: "Asistente documental",
        icon: "◌",
        permission: "chat.use",
      },
      {
        href: "/simulator",
        label: "Simulador",
        icon: "◉",
        permission: "simulation.use",
      },
      {
        href: "/summary",
        label: "Resumen posterior",
        icon: "✓",
        permission: "summary.create",
      },
    ],
  },
  {
    title: "Gestión",
    items: [
      {
        href: "/library",
        label: "Biblioteca documental",
        icon: "▤",
        permission: "document.read",
      },
      {
        href: "/compliance",
        label: "Compliance",
        icon: "⚑",
        permission: "review.read",
      },
      { href: "/audit", label: "Auditoría", icon: "◷", permission: "audit.read" },
      {
        href: "/evals",
        label: "Evaluaciones",
        icon: "◈",
        permission: "eval.read",
      },
      {
        href: "/failure-lab",
        label: "Failure Lab",
        icon: "⚠",
        permission: "failure_lab.read",
      },
    ],
  },
];

export function Sidebar({
  profile,
  pendingReviews = 0,
}: {
  profile: Profile;
  pendingReviews?: number;
}) {
  const pathname = usePathname();
  const has = (permission?: string) =>
    !permission || profile.permissions.includes(permission);

  return (
    <nav
      className="w-64 shrink-0 bg-navy-950 text-white flex flex-col"
      aria-label="Navegación principal"
    >
      <div className="px-5 py-5 border-b border-white/8">
        <Link href="/" className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-[9px] bg-blue-600 grid place-items-center text-[15px]">
            ◈
          </div>
          <div className="leading-tight">
            <p className="text-[13px] font-semibold tracking-tight">
              PHARMA SANDBOX
            </p>
            <p className="text-[11px] text-slate-300/50">Commercial AI</p>
          </div>
        </Link>
      </div>

      <div className="px-5 py-4 border-b border-white/8">
        <p className="text-[11px] uppercase tracking-wider text-slate-300/50">
          {profile.tenantName}
        </p>
        <p className="text-[13px] font-medium mt-1">{profile.fullName}</p>
        <p className="text-[12px] text-slate-300/60">
          {ROLE_LABELS[profile.role] ?? profile.role}
        </p>
      </div>

      <div className="flex-1 overflow-y-auto py-3">
        {SECTIONS.map((section) => (
          <div key={section.title} className="mb-4">
            <p className="px-5 mb-1.5 text-[11px] uppercase tracking-wider text-slate-300/40">
              {section.title}
            </p>
            <ul>
              {section.items.map((item) => {
                const allowed = has(item.permission);
                const active =
                  pathname === item.href ||
                  (item.href !== "/" && pathname.startsWith(item.href));

                if (!allowed) {
                  return (
                    <li key={item.href}>
                      <div
                        className="mx-2 px-3 py-2 rounded-[9px] flex items-center gap-3 cursor-not-allowed"
                        title={`Tu rol no tiene el permiso ${item.permission}`}
                      >
                        <span className="text-[14px] text-slate-300/25" aria-hidden>
                          {item.icon}
                        </span>
                        <span className="text-[13px] text-slate-300/30">
                          {item.label}
                        </span>
                        <span
                          className="ml-auto text-[10px] text-slate-300/25"
                          aria-label="Sin permiso"
                        >
                          ⌀
                        </span>
                      </div>
                    </li>
                  );
                }

                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={`mx-2 px-3 py-2 rounded-[9px] flex items-center gap-3 transition-colors ${
                        active
                          ? "bg-blue-600 text-white"
                          : "text-slate-300/85 hover:bg-white/6"
                      }`}
                    >
                      <span className="text-[14px]" aria-hidden>
                        {item.icon}
                      </span>
                      <span className="text-[13px]">{item.label}</span>
                      {item.href === "/compliance" && pendingReviews > 0 ? (
                        <span className="ml-auto min-w-[20px] text-center bg-amber-500 text-navy-950 text-[11px] font-semibold rounded-full px-1.5 py-0.5">
                          {pendingReviews}
                        </span>
                      ) : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <div className="px-5 py-3 border-t border-white/8">
        <p className="text-[11px] text-slate-300/40 leading-relaxed">
          Los módulos en gris requieren un permiso que tu rol no tiene. Se
          muestran para que la matriz de permisos sea visible.
        </p>
      </div>
    </nav>
  );
}
