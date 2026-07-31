"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, Field, inputClass, Mono } from "@/components/ui";

/**
 * Las cuentas se muestran en pantalla.
 *
 * En un producto real esto sería un fallo grave. Aquí es lo contrario: el
 * entorno es una demostración con datos sintéticos, y lo que se quiere enseñar
 * es precisamente **que el mismo sistema se comporta de forma distinta según
 * quién entre**. Sin las cuentas a la vista, comprobarlo exigiría credenciales
 * que solo yo tengo, y la demostración pasaría a ser una afirmación.
 *
 * El orden no es alfabético: es el del recorrido de la demostración.
 */
const DEMO_ACCOUNTS = [
  {
    email: "laura.garcia@novapharma.demo",
    name: "Laura García",
    role: "Comercial",
    org: "NovaPharma",
    can: "Genera briefings, pregunta al asistente, practica en el simulador",
  },
  {
    email: "maria.ruiz@novapharma.demo",
    name: "María Ruiz",
    role: "Compliance",
    org: "NovaPharma",
    can: "Aprueba documentos y decide sobre la cola de revisión. No genera contenido",
  },
  {
    email: "ana.serra@novapharma.demo",
    name: "Ana Serra",
    role: "Auditoría",
    org: "NovaPharma",
    can: "Solo lectura. Ve el registro completo y no puede invocar al agente",
  },
  {
    email: "sofia.marin@biohealth.demo",
    name: "Sofía Marín",
    role: "Comercial",
    org: "BioHealth",
    can: "La otra organización. Sirve para comprobar que el aislamiento es real",
  },
];

const DEMO_PASSWORD = "Demo1234!";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);

    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      setError(body.message ?? "No se ha podido iniciar sesión");
      setPending(false);
      return;
    }

    router.replace("/");
    router.refresh();
  }

  function useAccount(accountEmail: string) {
    setEmail(accountEmail);
    setPassword(DEMO_PASSWORD);
    setError(null);
  }

  return (
    <div className="min-h-screen flex flex-col">
      <div className="demo-banner">
        Demo environment — synthetic data only
      </div>

      <div className="flex-1 grid lg:grid-cols-2">
        {/* Identidad del producto */}
        <div className="bg-navy-950 text-white px-8 py-12 lg:px-16 lg:py-16 flex flex-col justify-center">
          <div className="max-w-md">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-[10px] bg-blue-600 grid place-items-center text-[17px]">
                ◈
              </div>
              <div>
                <p className="font-semibold tracking-tight">PHARMA SANDBOX</p>
                <p className="text-[12px] text-slate-300/70">Commercial AI</p>
              </div>
            </div>

            <h1 className="mt-10 text-[28px] leading-tight font-semibold tracking-tight">
              Un sistema de IA comercial con límites, permisos y responsabilidad
            </h1>
            <p className="mt-4 text-[14px] leading-relaxed text-slate-300/80">
              Todo lo que genera el asistente cita documentación aprobada, pasa
              por un verificador independiente y deja una traza que se puede
              reconstruir paso a paso. Cuando no hay material que respalde una
              respuesta, el sistema lo dice en vez de improvisar.
            </p>

            <dl className="mt-10 space-y-4 text-[13px]">
              {[
                ["Aislamiento", "Row-Level Security en PostgreSQL, no filtros en el código"],
                ["Verificación", "Un segundo modelo intenta refutar antes de entregar"],
                ["Trazabilidad", "De la respuesta al documento, la versión y el prompt"],
              ].map(([term, def]) => (
                <div key={term} className="flex gap-3">
                  <dt className="w-24 shrink-0 text-slate-300/60">{term}</dt>
                  <dd className="text-slate-300/90">{def}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>

        {/* Acceso */}
        <div className="px-6 py-10 lg:px-16 lg:py-16 flex flex-col justify-center">
          <div className="w-full max-w-md mx-auto">
            <h2 className="text-[20px] font-semibold tracking-tight">
              Acceder
            </h2>
            <p className="text-[13px] text-slate-500 mt-1">
              Elige una cuenta para ver cómo cambia el sistema según el rol.
            </p>

            <form onSubmit={submit} className="mt-6 space-y-4">
              <Field label="Correo electrónico">
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={inputClass}
                  placeholder="nombre@organizacion.demo"
                  autoComplete="username"
                />
              </Field>

              <Field label="Contraseña">
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={inputClass}
                  autoComplete="current-password"
                />
              </Field>

              {error ? (
                <p
                  role="alert"
                  className="text-[13px] text-red-500 bg-red-50 rounded-[10px] px-3 py-2"
                >
                  {error}
                </p>
              ) : null}

              <Button type="submit" disabled={pending} className="w-full">
                {pending ? "Comprobando…" : "Entrar"}
              </Button>
            </form>

            <div className="mt-8">
              <p className="text-[12px] font-semibold uppercase tracking-wider text-slate-500">
                Cuentas de demostración
              </p>
              <p className="text-[12px] text-slate-500 mt-1">
                Contraseña común: <Mono>{DEMO_PASSWORD}</Mono>
              </p>

              <ul className="mt-3 space-y-2">
                {DEMO_ACCOUNTS.map((account) => (
                  <li key={account.email}>
                    <button
                      type="button"
                      onClick={() => useAccount(account.email)}
                      className="w-full text-left bg-white border border-slate-200 rounded-[10px] px-3 py-2.5 hover:border-blue-600 hover:bg-blue-50/40 transition-colors"
                    >
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="text-[13px] font-medium text-slate-950">
                          {account.name}
                        </span>
                        <span className="text-[12px] text-slate-500">
                          {account.role} · {account.org}
                        </span>
                      </div>
                      <p className="text-[12px] text-slate-500 mt-0.5">
                        {account.can}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
