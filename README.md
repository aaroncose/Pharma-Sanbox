# Pharma Commercial AI Sandbox

> **DEMO ENVIRONMENT — SYNTHETIC DATA ONLY**
> Ningún dato de este repositorio corresponde a personas, profesionales
> sanitarios, productos o empresas reales.

Plataforma multi-tenant que asiste a equipos comerciales farmacéuticos:
preparación de reuniones, consulta de documentación autorizada, simulación
conversacional, resumen posterior y revisión humana obligatoria del contenido
sensible.

El objetivo del proyecto no es la cantidad de funcionalidad. Es demostrar que
un sistema con agentes puede ser **fiable, controlable, trazable y seguro** en
un contexto regulado: el agente está rodeado de un harness con restricciones,
verificación posterior, evaluaciones y ciclos de feedback, no es un prompt
conectado a un modelo.

---

## Estado

| Fase | Contenido | Estado |
|------|-----------|--------|
| 1 | Scaffolding e infraestructura | ok |
| 2 | Modelo de datos, RLS y datos sintéticos | ok |
| 3 | Auth, roles y aislamiento multi-tenant | ok |
| 4 | Harness del agente | ok |
| 5 | Módulos funcionales | ok |
| 6 | Frontend | ok |
| 7 | Failure Lab y evaluaciones | ok |
| 8 | Documentación y entregables | ok |

| 9 | Revision y correciones y añadir funciones (citadas aqui abajo) | trabajando en ello Vie 31 Jul 12:24 | 

⚠️ Frontend con bloqueo de build/lint.
⚠️ La validación completa del sistema requiere PostgreSQL y Redis.
⚠️ Requiere levantar Docker

IMPORTANTE!!

He subido este proyecto como base técnica de una demo de referencia en seguridad y trazabilidad para agentes en un contexto farmacéutico. 
La parte de backend y la arquitectura de aislamiento multi-tenant están ya construidas y parcialmente validadas con pruebas, pero el proyecto aún no está cerrado como demo end-to-end en este entorno: el frontend presenta bloqueos de build/lint y la validación completa del sistema requiere PostgreSQL y Redis disponibles. 
En otras palabras, el repositorio ya ofrece una base sólida y verificable, pero no una entrega totalmente pulida y ejecutable sin esos servicios y ajustes adicionales.

Sigo trabajando en ello, lo ire actualizando asap.

---

## Arranque rápido

Requisitos: macOS o Linux, Python 3.12, Node 20+, y PostgreSQL 17 + Redis
(nativos o vía Docker).

```bash
# Opción A — nativo (Homebrew)
make setup-native     # instala y arranca PostgreSQL 17 + pgvector + Redis
make setup            # entorno virtual, dependencias, .env, esquema y datos

# Opción B — contenedores
docker compose up -d db redis
make setup

make dev              # API en :8010, frontend en :3000
```

`make seed` deja documentos, profesionales y políticas, pero ninguna ejecución
del agente: el panel, la cola de revisión y la auditoría salen vacíos. `make
demo` los llena atravesando los mismos endpoints que usa la interfaz —no
inserta filas a mano— así que las trazas, las citas y el coste son reales.

Comprobación:

```bash
curl -s localhost:8010/readyz
# {"status":"ready","database":"ok","rls_enforced":true,"db_role":"pharma_app",...}
```

Acceso: cualquier cuenta de la pantalla de login, contraseña `Demo1234!`.
`maria.ruiz@novapharma.demo` (compliance) es la que más pantallas ve.

`make help` lista el resto de comandos.

---

## Decisiones de arranque que ya están tomadas

Estas tres condicionan todo lo demás y están documentadas con alternativas
descartadas en `docs/adr/`.

**El aislamiento entre clientes se impone en la base de datos, no en el código.**
La API se conecta con un rol (`pharma_app`) que no es superusuario y no tiene
`BYPASSRLS`. Cada transacción fija `app.tenant_id` con `set_config(..., true)`
y las políticas de Row-Level Security filtran cada fila contra ese valor. Si un
endpoint olvida filtrar, o alguien manipula un ID en la URL, la consulta
devuelve cero filas y la aplicación traduce ese vacío a
`403 ACCESS_DENIED_CROSS_TENANT`.

La API **se niega a arrancar** si detecta que su rol de base de datos puede
saltarse RLS (`app/db/session.py::assert_rls_enforced`). Es la salvaguarda
contra el fallo más silencioso de este diseño: con un rol privilegiado todo
funciona, todas las pruebas de humo pasan, y el aislamiento simplemente no
existe.

**El proveedor de IA es sustituible y opcional.** Si no hay `ANTHROPIC_API_KEY`,
el sistema degrada a un proveedor mock determinista y la demostración completa
sigue siendo ejecutable, sin red y sin coste. Esto también hace que la prueba
de "caída del proveedor" del Failure Lab sea real y no una simulación pintada.

**Los secretos no tienen valor por defecto utilizable.** En `staging` o
`production` la aplicación falla al arrancar si falta `JWT_SECRET` o
`FIELD_ENCRYPTION_KEY`. En local se genera un secreto efímero, que invalida los
tokens en cada reinicio: es el comportamiento correcto en desarrollo.

---

## Estructura

```text
backend/
  app/
    config.py         Configuración; sin defaults inseguros
    main.py           Composición: routers, errores, middleware de traza
    core/             Errores de dominio con código estable, logging redactado
    db/               Sesiones, anclaje de tenant, migraciones, seeds
    models/           Modelo de datos
    schemas/          Contratos de entrada y salida
    api/v1/           Endpoints
    agent/            Harness: context builder, tools, verificador
    policies/         Motor de políticas (políticas como datos)
    services/         Lógica de dominio
    evals/            Suite de evaluaciones
  tests/              Incluye pruebas de aislamiento entre tenants
frontend/             Next.js 16, React 19, TypeScript, Tailwind 4
docs/
  adr/                Architecture Decision Records
prompts/              Prompts versionados
scripts/              Utilidades e inicialización de base de datos
```

---

## Licencia y alcance

Proyecto de demostración técnica. No apto para uso clínico ni comercial.
Las limitaciones conocidas están documentadas en `docs/limitations.md`.
