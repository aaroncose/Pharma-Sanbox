/**
 * Cierre de sesión.
 *
 * Se avisa al backend **antes** de borrar la cookie. El orden importa: el
 * backend revoca el token de refresco en Redis, y si se borrara primero la
 * cookie no quedaría nada con lo que pedir la revocación. El resultado sería
 * una sesión que parece cerrada en el navegador y sigue viva siete días en el
 * servidor.
 *
 * Si la llamada al backend falla, la cookie se borra igual: el usuario ha
 * pedido salir y la interfaz tiene que obedecer. Que la revocación remota falle
 * es un problema de infraestructura, no un motivo para dejarle la sesión
 * abierta.
 */

import { NextResponse } from "next/server";

import { API_BASE, clearSession, getAccessToken } from "@/lib/session";

export async function POST() {
  const token = await getAccessToken();

  if (token) {
    await fetch(`${API_BASE}/api/v1/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    }).catch(() => {
      // Silenciado a propósito: ver el comentario de arriba.
    });
  }

  await clearSession();
  return NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
}
