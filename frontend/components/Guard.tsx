import { Card, Mono } from "@/components/ui";
import { getProfile } from "@/lib/session";

/**
 * Una pantalla a la que el rol no llega tiene que decirlo, no romperse.
 *
 * La barra lateral ya atenúa los módulos sin permiso, pero eso solo cubre la
 * navegación con ratón: escribir la URL a mano llegaba al `fetch`, el API
 * respondía 403 y la página reventaba con un 500. Para un producto cuyo
 * argumento es el control de acceso, que denegar sea indistinguible de estar
 * roto es el peor de los mensajes.
 *
 * Se comprueba con el perfil que ya viaja en la sesión, antes de llamar al API.
 * No sustituye a la comprobación del backend —que es la que manda— sino que
 * evita pedir lo que ya se sabe que se va a denegar.
 */
export async function guard(permission: string) {
  const profile = await getProfile();
  if (profile?.permissions.includes(permission)) return null;

  return (
    <Card className="p-8 max-w-xl">
      <p className="text-[15px] font-semibold text-slate-950">
        Tu rol no tiene acceso a este módulo
      </p>
      <p className="text-[13px] text-slate-500 mt-2">
        Hace falta el permiso <Mono>{permission}</Mono>, y el rol{" "}
        <Mono>{profile?.role ?? "desconocido"}</Mono> no lo incluye. La matriz de
        permisos es la misma que aplica el backend: aquí solo se anticipa para no
        pedir algo que se va a denegar.
      </p>
    </Card>
  );
}
