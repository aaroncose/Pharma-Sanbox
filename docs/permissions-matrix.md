# Matriz de roles y permisos

> Generado automáticamente desde `backend/app/core/permissions.py`.
> No editar a mano: ejecutar `make docs`.

El backend comprueba estos permisos en cada endpoint mediante la dependencia
`require(...)`. La interfaz recibe la lista de permisos del usuario al iniciar
sesión y la usa para ocultar lo que no procede, pero eso es una comodidad de
presentación: **ocultar un botón no es un control de acceso**. La comprobación
que cuenta es la del servidor.

Criterio general: **denegar por defecto**. Un permiso que no aparezca marcado
para un rol se rechaza, y un endpoint que olvide declarar el suyo queda
inaccesible en lugar de quedar abierto.

## Decisiones de diseño

**El superadministrador de plataforma no es un comodín.** Crea organizaciones y
administra modelos; no puede leer documentos, interacciones, briefings ni la
cola de revisión de ningún cliente. No es solo una restricción de la capa de
aplicación: las políticas RLS tampoco le conceden acceso al contenido comercial.

**El auditor no genera nada.** Tiene la lectura más amplia del sistema y ningún
permiso de escritura ni de invocación del agente. Un rol de solo lectura capaz
de lanzar generaciones consumiría presupuesto, escribiría trazas y podría
producir contenido que alguien tendría que revisar.

**Compliance revisa, no produce.** Decide sobre contenido generado y aprueba
documentos, pero no crea briefings ni resúmenes: quien revisa no debería ser
también quien genera.

**El administrador de organización sube documentos pero no los aprueba.** Es la
separación que impide que una sola persona introduzca material no validado y lo
deje disponible para el agente.


## Matriz

| Permiso | Superadmin plataforma | Admin organización | Compliance | Comercial | Auditor |
|---|:---:|:---:|:---:|:---:|:---:|
| **Plataforma** |  |  |  |  |  |
| `platform.metrics.read` | ✅ | — | — | — | — |
| `platform.models.manage` | ✅ | — | — | — | — |
| `platform.org.manage` | ✅ | — | — | — | — |
| **Usuarios** |  |  |  |  |  |
| `user.manage` | — | ✅ | — | — | — |
| `user.read` | — | ✅ | ✅ | — | ✅ |
| **Productos** |  |  |  |  |  |
| `product.assign` | — | ✅ | — | — | — |
| `product.read` | — | ✅ | — | ✅ | — |
| **Biblioteca documental** |  |  |  |  |  |
| `document.approve` | — | — | ✅ | — | — |
| `document.create` | — | ✅ | ✅ | — | — |
| `document.read` | — | ✅ | ✅ | ✅ | ✅ |
| `document.withdraw` | — | — | ✅ | — | — |
| **Profesionales sanitarios** |  |  |  |  |  |
| `hcp.read` | — | ✅ | ✅ | ✅ | ✅ |
| **Interacciones** |  |  |  |  |  |
| `interaction.read` | — | ✅ | ✅ | ✅ | ✅ |
| **Tareas** |  |  |  |  |  |
| `task.manage` | — | — | — | ✅ | — |
| `task.read` | — | ✅ | — | ✅ | ✅ |
| **Briefings** |  |  |  |  |  |
| `briefing.create` | — | — | — | ✅ | — |
| `briefing.read` | — | ✅ | ✅ | ✅ | ✅ |
| **Asistente documental** |  |  |  |  |  |
| `chat.use` | — | — | — | ✅ | — |
| **Simulador** |  |  |  |  |  |
| `simulation.use` | — | — | — | ✅ | — |
| **Resumen posterior** |  |  |  |  |  |
| `summary.create` | — | — | — | ✅ | — |
| **Revisión humana** |  |  |  |  |  |
| `review.decide` | — | — | ✅ | — | — |
| `review.read` | — | — | ✅ | — | ✅ |
| `review.request` | — | — | — | ✅ | — |
| **Políticas** |  |  |  |  |  |
| `policy.manage` | — | — | ✅ | — | — |
| `policy.read` | — | ✅ | ✅ | ✅ | ✅ |
| **Auditoría** |  |  |  |  |  |
| `audit.export` | — | — | ✅ | — | ✅ |
| `audit.read` | — | ✅ | ✅ | — | ✅ |
| **Trazas** |  |  |  |  |  |
| `trace.read` | — | ✅ | ✅ | — | ✅ |
| **Evaluaciones** |  |  |  |  |  |
| `eval.read` | ✅ | ✅ | ✅ | — | ✅ |
| `eval.run` | ✅ | — | ✅ | — | — |
| **Failure Lab** |  |  |  |  |  |
| `failure_lab.read` | ✅ | ✅ | ✅ | — | ✅ |
| `failure_lab.run` | — | — | ✅ | — | — |

## Recuento

| Rol | Permisos |
|---|---:|
| Superadmin plataforma | 6 |
| Admin organización | 15 |
| Compliance | 19 |
| Comercial | 13 |
| Auditor | 13 |
| **Total definidos** | **32** |
