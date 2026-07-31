/**
 * Login. El token entra en una cookie `httpOnly` y no sale de aquí.
 *
 * La respuesta al navegador contiene el perfil —nombre, rol, permisos— y
 * **nunca** los tokens. Es lo que hace que un XSS en el frontend no pueda
 * llevarse la credencial: no está en ningún sitio que JavaScript pueda leer.
 */

import { NextResponse } from "next/server";

import { API_BASE, storeSession } from "@/lib/session";

export async function POST(request: Request) {
  const { email, password } = await request.json();

  const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
    cache: "no-store",
  });

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    // Se reenvía el mensaje del backend tal cual. Es deliberado: allí todos los
    // fallos de login —usuario inexistente, contraseña incorrecta, cuenta
    // suspendida— devuelven exactamente la misma respuesta, y reescribirla aquí
    // podría reintroducir la diferencia que permite enumerar usuarios.
    return NextResponse.json(
      { code: payload.code ?? "AUTH_FAILED", message: payload.message },
      { status: response.status },
    );
  }

  const profile = {
    userId: payload.user.id,
    email: payload.user.email,
    fullName: payload.user.full_name,
    role: payload.user.role,
    tenantName: payload.user.tenant.name,
    permissions: payload.user.permissions,
  };

  await storeSession(
    payload.access_token,
    payload.refresh_token,
    profile,
    Math.floor(payload.expires_in / 60),
  );

  return NextResponse.json(
    { profile },
    // Ninguna respuesta de autenticación se cachea, ni siquiera esta que no
    // lleva tokens: el perfil dice quién ha entrado.
    { headers: { "Cache-Control": "no-store" } },
  );
}
