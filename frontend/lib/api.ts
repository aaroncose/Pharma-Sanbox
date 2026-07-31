/**
 * Cliente del API, siempre desde el servidor.
 *
 * Ningún componente llama a FastAPI directamente: el token vive en una cookie
 * `httpOnly` y el navegador no puede leerlo (ver `session.ts`). Todo pasa por
 * aquí, que se ejecuta en el servidor de Next.
 *
 * La pieza que justifica el módulo es el refresco transparente. El token de
 * acceso dura 30 minutos; sin esto, una sesión de compliance revisando una cola
 * larga caducaría a mitad y devolvería al login perdiendo el trabajo. Con esto,
 * el 401 se resuelve solo y la petición se reintenta una vez.
 *
 * **Una sola vez, no en bucle.** Si el reintento con el token nuevo también
 * devuelve 401, el problema no es la caducidad —es que la sesión ya no vale— y
 * repetir solo retrasa el momento de decírselo al usuario.
 */

import { API_BASE, getAccessToken, getRefreshToken, storeSession } from "./session";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** El 403 que el backend devuelve igual exista o no el recurso ajeno. */
  get isCrossTenant() {
    return this.code === "ACCESS_DENIED_CROSS_TENANT";
  }

  get isAuthExpired() {
    return this.status === 401;
  }
}

type Options = {
  method?: string;
  body?: unknown;
  /** Uso interno del reintento tras refrescar. */
  retried?: boolean;
  /** Segundos de caché. Por defecto ninguna: casi todo aquí es estado vivo. */
  revalidate?: number;
};

async function refreshAccessToken(): Promise<boolean> {
  const refresh = await getRefreshToken();
  if (!refresh) return false;

  const response = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
    cache: "no-store",
  });

  if (!response.ok) return false;

  const data = await response.json();
  await storeSession(data.access_token, data.refresh_token, {
    userId: data.user.id,
    email: data.user.email,
    fullName: data.user.full_name,
    role: data.user.role,
    tenantName: data.user.tenant.name,
    permissions: data.user.permissions,
  });
  return true;
}

export async function api<T = unknown>(
  path: string,
  options: Options = {},
): Promise<T> {
  const token = await getAccessToken();
  const { method = "GET", body, retried = false, revalidate } = options;

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    ...(revalidate === undefined
      ? { cache: "no-store" as const }
      : { next: { revalidate } }),
  });

  if (response.status === 401 && !retried) {
    // Caducó el token de acceso. Se refresca y se reintenta **una** vez.
    if (await refreshAccessToken()) {
      return api<T>(path, { ...options, retried: true });
    }
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new ApiError(
      response.status,
      payload.code ?? "UNKNOWN",
      payload.message ?? `Error ${response.status}`,
      payload.details,
    );
  }

  return payload as T;
}
