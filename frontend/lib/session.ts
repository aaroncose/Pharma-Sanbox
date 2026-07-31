/**
 * Sesión del usuario, guardada donde el navegador no puede leerla.
 *
 * El backend emite JWT. Hay tres sitios donde ponerlos y en un producto cuyo
 * argumento central es el control de acceso, dos son indefendibles:
 *
 *   · `localStorage` — cualquier XSS lo lee y se lo lleva a otro dominio, donde
 *     sirve durante los 30 minutos de vida del token de acceso y los 7 días del
 *     de refresco. El robo es silencioso y sobrevive al cierre de la pestaña.
 *   · Cookie sin `httpOnly` — exactamente el mismo problema.
 *   · Cookie `httpOnly` puesta por el servidor — el token nunca entra en el
 *     espacio de JavaScript.
 *
 * Se elige la tercera, con el coste de que toda llamada al API pasa por Next.
 * Lo que se compra: un XSS en el frontend sigue pudiendo hacer peticiones en
 * nombre del usuario —eso es inevitable— pero **no puede extraer la credencial**
 * para usarla fuera del navegador ni después de cerrar sesión.
 *
 * Es la misma distinción que el backend hace entre «denegar el acceso» y
 * «demostrar que no salió ningún campo»: no basta con que el ataque sea difícil,
 * hay que acotar qué consigue si sale bien.
 */

import { cookies } from "next/headers";

const ACCESS = "pcs_access";
const REFRESH = "pcs_refresh";
const PROFILE = "pcs_profile";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8010";

export type Profile = {
  userId: string;
  email: string;
  fullName: string;
  role: string;
  tenantName: string;
  permissions: string[];
};

/**
 * `sameSite: "lax"` y no `"strict"`.
 *
 * Con `strict` la sesión se pierde al llegar desde cualquier enlace externo,
 * incluido el propio enlace de la demostración, y el usuario aterriza en el
 * login sin entender por qué. `lax` bloquea igualmente el envío en peticiones
 * de escritura de origen cruzado, que es lo que protege de CSRF.
 */
const COOKIE_BASE = {
  httpOnly: true,
  sameSite: "lax" as const,
  secure: process.env.NODE_ENV === "production",
  path: "/",
};

export async function storeSession(
  accessToken: string,
  refreshToken: string,
  profile: Profile,
  accessTtlMinutes = 30,
) {
  const jar = await cookies();

  jar.set(ACCESS, accessToken, {
    ...COOKIE_BASE,
    maxAge: accessTtlMinutes * 60,
  });
  jar.set(REFRESH, refreshToken, {
    ...COOKIE_BASE,
    maxAge: 7 * 24 * 60 * 60,
  });
  // El perfil sí es legible por el cliente: es nombre, rol y permisos, no una
  // credencial. La interfaz lo necesita para no ofrecer acciones que el backend
  // va a rechazar. Ocultar un botón NO es un control de acceso —el permiso lo
  // comprueba el backend en cada petición— pero ofrecer un botón que siempre
  // falla es una interfaz que miente.
  jar.set(PROFILE, JSON.stringify(profile), {
    ...COOKIE_BASE,
    httpOnly: false,
    maxAge: 7 * 24 * 60 * 60,
  });
}

export async function clearSession() {
  const jar = await cookies();
  for (const name of [ACCESS, REFRESH, PROFILE]) {
    jar.delete(name);
  }
}

export async function getAccessToken(): Promise<string | null> {
  return (await cookies()).get(ACCESS)?.value ?? null;
}

export async function getRefreshToken(): Promise<string | null> {
  return (await cookies()).get(REFRESH)?.value ?? null;
}

export async function getProfile(): Promise<Profile | null> {
  const raw = (await cookies()).get(PROFILE)?.value;
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Profile;
  } catch {
    return null;
  }
}
