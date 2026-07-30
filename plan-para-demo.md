Sí. Te lo diseño como una app B2B seria, limpia y con aspecto de producto regulado, no como un dashboard genérico de “IA”. La idea visual: **control, trazabilidad y seguridad**, con interfaz clara para comerciales y zonas más densas para compliance/auditoría.

## Dirección visual

**Nombre visible:** `Pharma Commercial AI Sandbox`  
**Subtítulo fijo:** `DEMO ENVIRONMENT — SYNTHETIC DATA ONLY`

Paleta:

```css
--navy-950: #091827;   /* fondo sidebar */
--navy-900: #0E2238;
--blue-600: #2563EB;   /* acción principal */
--blue-500: #3B82F6;
--teal-500: #14B8A6;   /* correcto / validado */
--amber-500: #F59E0B;  /* requiere revisión */
--red-500: #EF4444;    /* bloqueo / riesgo */
--slate-950: #0F172A;
--slate-700: #334155;
--slate-500: #64748B;
--slate-200: #E2E8F0;
--slate-100: #F1F5F9;
--canvas: #F7F9FC;
--white: #FFFFFF;
```

Tipografía:

- **Inter** para toda la interfaz.
- **IBM Plex Mono** para IDs, logs, versiones de prompts, eventos y datos técnicos.
- Títulos sobrios, sin tipografías “futuristas”.
- Bordes suaves de 10–12 px; sombras muy ligeras; mucho contraste y jerarquía visual.

## Estructura general

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Sidebar fija                    │ Barra superior                            │
│                                 │ Organización · búsqueda · alertas · perfil│
│ - Inicio                        ├───────────────────────────────────────────┤
│ - Reuniones                     │                                           │
│ - Asistente documental          │         Contenido de cada módulo           │
│ - Simulador                     │                                           │
│ - Resumen posterior             │                                           │
│ - Biblioteca                    │                                           │
│ - Compliance                    │                                           │
│ - Auditoría                     │                                           │
│ - Evaluaciones                  │                                           │
│ - Failure Lab                   │                                           │
│ - Administración                │                                           │
└─────────────────────────────────┴───────────────────────────────────────────┘
```

En desktop: sidebar de 256 px.  
En tablet: sidebar colapsable.  
En móvil: navegación inferior con las cuatro acciones principales: Inicio, Reuniones, Asistente y Más.

## Sidebar

```text
┌──────────────────────────────────┐
│ ◈  PHARMA SANDBOX                 │
│    Commercial AI                  │
│                                  │
│ NOVAPHARMA                        │
│ Laura García · Comercial          │
│                                  │
│ PRINCIPAL                         │
│ ▣  Inicio                         │
│ ◫  Preparar reunión               │
│ ◌  Asistente documental           │
│ ◉  Simulador                      │
│ ✓  Resumen posterior              │
│                                  │
│ GESTIÓN                           │
│ ▤  Biblioteca documental          │
│ ⚑  Compliance          3          │
│ ◷  Auditoría                      │
│ ◈  Evaluaciones                   │
│ ⚠  Failure Lab                    │
│                                  │
│ SISTEMA                           │
│ ⚙  Administración                 │
│                                  │
│ ● Sistema operativo               │
│                                  │
│ DEMO — SYNTHETIC DATA ONLY        │
└──────────────────────────────────┘
```

La navegación cambia según rol. Laura no ve administración avanzada de tenants; compliance sí ve revisión y políticas; auditor tiene casi todo en modo lectura.

## Barra superior

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Inicio / Preparar reunión                      [⌘K Buscar]  [🔔 3] [Laura] │
│ NovaPharma · Tenant: nph_01 · EU Sandbox                      ▼             │
└────────────────────────────────────────────────────────────────────────────┘
```

Debajo, cuando se use IA, aparece una banda discreta:

```text
✦ Asistente de IA activo. Las respuestas se generan únicamente desde
  documentación autorizada y pueden requerir revisión humana.
```

Esto comunica transparencia: la persona sabe que interactúa con una IA y ve claramente cuándo una respuesta ha sido revisada por compliance. [digital-strategy.ec.europa](https://digital-strategy.ec.europa.eu/en/policies/guidelines-transparency-ai-generated-content)

## Dashboard comercial

No pongas gráficas decorativas. El dashboard debe decir qué hacer hoy.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Buenos días, Laura                                                         │
│ Reuniones, tareas y contenido pendiente para NovaPharma                    │
│                                                                            │
│ [ + Preparar reunión ]  [ Abrir asistente documental ]                     │
├───────────────────┬───────────────────┬───────────────────┬────────────────┤
│ REUNIONES HOY     │ TAREAS ABIERTAS   │ EN REVISIÓN       │ FUENTES VÁLIDAS│
│ 2                 │ 5                 │ 1                 │ 100%           │
│ Dr. Javier Martín │ 2 vencen hoy      │ Briefing #BR-1042 │ Últimos 30 días│
├───────────────────────────────────────────────┬────────────────────────────┤
│ PRÓXIMA REUNIÓN                                │ ACTIVIDAD RECIENTE         │
│                                                │                            │
│ 15:30 · Dr. Javier Martín                      │ ✓ Briefing creado          │
│ Cardiología · Hospital Central                 │   CardioX · hace 12 min    │
│ Producto: CardioX                              │                            │
│                                                │ ⚠ Respuesta enviada a      │
│ [ Preparar briefing ] [ Ver historial ]        │   compliance · hace 1 h   │
├───────────────────────────────────────────────┴────────────────────────────┤
│ TAREAS PRIORITARIAS                                                        │
│ ○ Enviar material aprobado de CardioX       Hoy · Alta                     │
│ ○ Confirmar fecha con Dr. Martín            Hoy · Media                    │
│ ○ Revisar respuesta de compliance           Mañana · Alta                  │
└────────────────────────────────────────────────────────────────────────────┘
```

## Preparación de reunión

Esta es la pantalla estrella. Divide claramente la entrada, el proceso y el resultado.

### Estado inicial

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Preparar reunión                                                           │
│ Crea un briefing basado solo en documentación aprobada                     │
├──────────────────────────────────┬─────────────────────────────────────────┤
│ DATOS DE LA REUNIÓN              │ REGLAS APLICADAS                        │
│                                  │                                         │
│ Profesional sanitario            │ ✓ Tenant: NovaPharma                   │
│ [ Dr. Javier Martín         ▼ ]  │ ✓ Producto asignado                     │
│                                  │ ✓ Solo documentos approved              │
│ Producto                         │ ✓ Documentos vigentes                    │
│ [ CardioX                   ▼ ]  │ ✓ Fuentes obligatorias                  │
│                                  │                                         │
│ Objetivo                         │ No se envían datos personales            │
│ [ Presentar actualización... ]   │ innecesarios al proveedor de IA.         │
│                                  │                                         │
│ Duración estimada                │                                         │
│ [ 20 minutos                ▼ ]  │                                         │
│                                  │                                         │
│                  [ Generar briefing → ]                                   │
└──────────────────────────────────┴─────────────────────────────────────────┘
```

### Resultado de briefing

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Briefing #BR-1042                                  ● Validado · Riesgo bajo│
│ Dr. Javier Martín · CardioX · 30 Jul 2026 · 14:25                         │
│ [ Exportar PDF ] [ Guardar borrador ] [ Enviar a revisión ]                │
├─────────────────────────────────────────────┬──────────────────────────────┤
│ RESUMEN DEL PROFESIONAL                     │ FUENTES UTILIZADAS            │
│ Cardiólogo con interés previo en...         │                              │
│                                             │  [digital-strategy.ec.europa](https://digital-strategy.ec.europa.eu/en/policies/guidelines-transparency-ai-generated-content) Ficha CardioX v1.2        │
│ HISTORIAL AUTORIZADO                         │     Approved · 12 Jun 2026   │
│ - 14 Jun: preguntó por...                   │     § Seguridad               │
│ - 29 May: se compartió...                   │                              │
│                                             │  [gdpr-info](https://gdpr-info.eu/art-25-gdpr/) Estudio CARDIO-101        │
│ TEMAS PARA LA REUNIÓN                        │     Approved · 04 May 2026   │
│ 1. Actualización de información aprobada    │     § Resultados               │
│ 2. Resolver duda pendiente de seguridad     │                              │
│                                             │ [ Ver 4 fuentes ]             │
│ PREGUNTAS POSIBLES                           │                              │
│ - “¿Qué evidencia respalda...?”             │ CONFIANZA                     │
│ - “¿Cuál es el perfil de...?”               │ ████████░░ 84 / 100           │
│                                             │                              │
│ RIESGOS / CUIDADO                            │ TRAZABILIDAD                  │
│ ⚠ No realizar recomendaciones individual... │ Prompt: briefing.v1.3         │
│ ⚠ Si pregunta por dosificación, derivar...  │ Modelo: provider/model-x      │
│                                             │ Latencia: 2.4 s · €0.012      │
└─────────────────────────────────────────────┴──────────────────────────────┘
```

Cada bloque responde a una pregunta: qué sé, qué decir, qué evitar y de dónde sale. La IA no debe parecer una fuente de verdad; debe enseñar sus fuentes, versión documental y nivel de confianza. [gdpr-info](https://gdpr-info.eu/art-25-gdpr/)

## Asistente documental

Diseño de chat en dos columnas. Izquierda conversación; derecha evidencia.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Asistente documental · CardioX                                             │
│ Solo consulta documentación aprobada y vigente                             │
├───────────────────────────────────────────────────┬────────────────────────┤
│                                                   │ EVIDENCIA               │
│ Laura                                             │                        │
│ ¿Qué datos aprobados hay sobre la seguridad?      │ 01                     │
│                                                   │ Ficha CardioX v1.2      │
│ AI Assistant                                      │ Approved · Vigente      │
│ Según la ficha aprobada, los aspectos de          │ Sección 4.8             │
│ seguridad incluidos en el material son...  [digital-strategy.ec.europa](https://digital-strategy.ec.europa.eu/en/policies/guidelines-transparency-ai-generated-content)     │                        │
│                                                   │ “Los eventos descritos…│
│ Confianza: 89/100                                 │                        │
│ Riesgo: Bajo                                      │ [Abrir documento]       │
│                                                   │                        │
│ [ Escribe una pregunta…                     ] [→]│ 02                     │
│                                                   │ FAQ Comercial v2.1      │
│                                                   │ Approved · Vigente      │
└───────────────────────────────────────────────────┴────────────────────────┘
```

Cuando no haya fuente:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Información insuficiente                                                   │
│ No he encontrado documentación aprobada y vigente que respalde esta        │
│ afirmación. No puedo completar la respuesta con información no verificada. │
│                                                                            │
│ [ Solicitar revisión de compliance ]                                      │
└────────────────────────────────────────────────────────────────────────────┘
```

No uses rojo aquí. Es una limitación controlada, no un error del sistema.

## Simulador conversacional

Pantalla de práctica enfocada en conversación, no en “avatar de IA”.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Simulador conversacional                                                   │
│ Dr. Javier Martín · Cardiología · Escenario: duda de evidencia             │
│                                                    [ Finalizar simulación ] │
├──────────────────────────────────────────────────────┬─────────────────────┤
│                                                      │ PANEL DE PRÁCTICA    │
│ Dr. Javier Martín                                    │                      │
│ “He leído información distinta sobre CardioX.        │ Objetivo             │
│ ¿Qué evidencia concreta puede compartir?”            │ Explicar evidencia   │
│                                                      │ aprobada sin hacer   │
│ Laura                                                │ recomendaciones      │
│ [ Escribe tu respuesta...                      ] [→] │ clínicas             │
│                                                      │                      │
│ Sugerencia disponible                                │ Tiempo               │
│ “Consulta fuentes aprobadas antes de responder.”     │ 08:41                │
│                                                      │                      │
│                                                      │ Riesgo actual         │
│                                                      │ ● Bajo                │
│                                                      │                      │
│                                                      │ [ Ver fuentes útiles ]│
└──────────────────────────────────────────────────────┴─────────────────────┘
```

Al terminar:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Resultado de simulación                                                    │
├──────────────────────┬──────────────────────┬──────────────────────────────┤
│ PUNTUACIÓN           │ COMUNICACIÓN          │ COMPLIANCE                   │
│ 78 / 100             │ Clara y estructurada  │ 1 respuesta a mejorar        │
│ ████████░░           │                      │                               │
├──────────────────────┴──────────────────────┴──────────────────────────────┤
│ ✓ Aspectos positivos                                                        │
│ - Pediste contexto antes de responder                                      │
│ - Usaste lenguaje prudente y no hiciste afirmaciones absolutas             │
│                                                                            │
│ ⚠ Riesgos identificados                                                    │
│ - La respuesta 04 contiene una afirmación que no cita una fuente           │
│                                                                            │
│ ↗ Respuesta mejorable                                                      │
│ “Puedes reformularla como: según el estudio CARDIO-101…”                   │
│                                                                            │
│ [ Ver transcripción ] [ Repetir escenario ] [ Crear tarea de estudio ]     │
└────────────────────────────────────────────────────────────────────────────┘
```

## Biblioteca documental

No debe parecer Google Drive. Debe transmitir gobierno documental.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Biblioteca documental                                [ + Subir documento ] │
│ NovaPharma · 18 documentos                                                  │
├────────────────────────────────────────────────────────────────────────────┤
│ [ Buscar documentos... ] [Producto ▼] [Estado ▼] [Tipo ▼] [Confidencial ▼] │
├───────────────────────┬───────────┬───────────┬──────────┬─────────────────┤
│ DOCUMENTO             │ PRODUCTO  │ ESTADO    │ VERSIÓN  │ ÚLTIMA ACCIÓN   │
├───────────────────────┼───────────┼───────────┼──────────┼─────────────────┤
│ Ficha CardioX         │ CardioX   │ ● Approved│ v1.2     │ 12 Jun · M. Ruiz│
│ Estudio CARDIO-101    │ CardioX   │ ● Approved│ v1.0     │ 04 May · M. Ruiz│
│ FAQ Comercial         │ CardioX   │ ● Approved│ v2.1     │ 28 Jun · M. Ruiz│
│ Campaña Q1            │ CardioX   │ ○ Draft   │ v0.8     │ 29 Jul · Laura  │
│ Material histórico    │ CardioX   │ ● Withdrawn│ v0.7    │ 14 Mar · M. Ruiz│
└───────────────────────┴───────────┴───────────┴──────────┴─────────────────┘
```

Estados:

- **Approved**: verde/teal.
- **Draft**: gris azulado.
- **Pending review**: ámbar.
- **Expired**: gris oscuro.
- **Withdrawn**: rojo tenue, pero no alarmista.
- Un documento retirado aparece en la biblioteca y auditoría, pero nunca en resultados de búsqueda del agente.

## Cola de compliance

Esta pantalla debe ser de las mejores visualmente. Aquí transmites que el producto se toma en serio la supervisión humana.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Cola de revisión                                               [ 3 abiertas ]│
│ Revisa contenido generado antes de permitir su uso comercial               │
├───────────────────────┬────────────────────────────────────────────────────┤
│ FILTROS               │ RESPUESTA PENDIENTE #RV-204                         │
│                       │                                                     │
│ ● Pendientes (3)      │ Tipo: Chat documental                               │
│ ○ Aprobadas           │ Solicitado por: Laura García                        │
│ ○ Rechazadas          │ Producto: CardioX                                  │
│ ○ Alta prioridad      │ Motivo: afirmación no respaldada                    │
│                       │                                                     │
│                       │ RESPUESTA ORIGINAL                                  │
│                       │ “CardioX ha demostrado…”                            │
│                       │                                                     │
│                       │ ALERTA                                               │
│                       │ No se ha encontrado fuente aprobada para la         │
│                       │ afirmación sobre resultados clínicos.               │
│                       │                                                     │
│                       │ [ Aprobar ] [ Editar ] [ Rechazar ] [ Regenerar ]   │
│                       │                                                     │
│                       │ Motivo de decisión                                  │
│                       │ [ Escribe una explicación obligatoria... ]          │
└───────────────────────┴────────────────────────────────────────────────────┘
```

Al guardar, el sistema muestra:

```text
✓ Decisión registrada
Revisado por María Ruiz · 30 Jul 2026 · 15:42
Política: PRODUCT_CLAIM_REQUIRES_SOURCE v1.0
```

## Auditoría

Aquí sí puedes hacer una interfaz más densa y técnica.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Auditoría                                                                   │
│ 1.248 eventos · NovaPharma · Retención: 365 días                            │
├────────────────────────────────────────────────────────────────────────────┤
│ [Buscar por usuario, recurso o ID...] [Acción ▼] [Resultado ▼] [Fecha ▼]   │
├─────────────────┬─────────────────────────────┬──────────┬─────────────────┤
│ HORA            │ EVENTO                      │ RESULTADO│ TRAZA           │
├─────────────────┼─────────────────────────────┼──────────┼─────────────────┤
│ 15:42:09        │ compliance.review.rejected  │ Blocked  │ tr_8fa2...      │
│ 15:40:21        │ agent.briefing.generated    │ Success  │ tr_c98b...      │
│ 15:37:14        │ access.cross_tenant.attempt │ Denied   │ tr_12ab...      │
│ 15:36:50        │ document.search             │ Success  │ tr_76ef...      │
└─────────────────┴─────────────────────────────┴──────────┴─────────────────┘
```

Detalle de evento:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ access.cross_tenant.attempt                                  DENIED         │
│ Trace: tr_12ab9e · 30 Jul 2026, 15:37:14 UTC                                │
├────────────────────────────────────────────────────────────────────────────┤
│ Actor                 Laura García · user_nph_laura                         │
│ Tenant                NovaPharma · nph_01                                  │
│ Requested resource    Interaction · int_bh_9832                             │
│ Resource tenant       BioHealth · bh_01                                    │
│ Decision              ACCESS_DENIED_CROSS_TENANT                            │
│ Exposed data          None                                                  │
│ Policy                TENANT_ISOLATION v1.0                                 │
│ IP / Session          Redacted                                              │
└────────────────────────────────────────────────────────────────────────────┘
```

La auditoría debe registrar intento y resultado, sin guardar más datos personales de los necesarios; eso es coherente con minimización y privacidad desde el diseño. [gdpr-info](https://gdpr-info.eu/art-25-gdpr/)

## Failure Lab

Esta debe parecer una sala de pruebas, no una pantalla de errores.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ ⚠ Failure Lab                                                               │
│ Entorno controlado para validar seguridad, políticas y recuperación         │
│ Ninguna prueba utiliza datos reales                                         │
├────────────────────┬────────────────────────────┬─────────┬────────────────┤
│ ESCENARIO          │ DESCRIPCIÓN                │ ESTADO  │ ACCIÓN         │
├────────────────────┼────────────────────────────┼─────────┼────────────────┤
│ Cross-tenant leak  │ NovaPharma accede a dato   │ Passed  │ [ Ejecutar ]   │
│                    │ de BioHealth               │         │                │
│ Prompt injection   │ Documento con instrucciones│ Passed  │ [ Ejecutar ]   │
│ Unsupported claim  │ Pregunta sin fuentes       │ Passed  │ [ Ejecutar ]   │
│ Withdrawn document │ Fuente retirada            │ Passed  │ [ Ejecutar ]   │
│ Provider outage    │ Timeout del modelo         │ Pending │ [ Ejecutar ]   │
│ Tool escalation    │ Agente intenta cambiar rol │ Passed  │ [ Ejecutar ]   │
│ Clinical advice    │ Recomendación personalizada│ Passed  │ [ Ejecutar ]   │
└────────────────────┴────────────────────────────┴─────────┴────────────────┘
```

Al ejecutar:

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Test: Cross-tenant leak                                      PASSED         │
│                                                                            │
│ Request                                                                    │
│ GET /api/interactions/int_bh_9832                                          │
│ Actor: laura.garcia@novapharma.demo                                        │
│                                                                            │
│ Result                                                                     │
│ HTTP 403 Forbidden                                                        │
│ Code: ACCESS_DENIED_CROSS_TENANT                                           │
│ Data exposure: 0 fields                                                    │
│ Audit event: aud_01J...                                                    │
│                                                                            │
│ [ Ver auditoría ] [ Repetir prueba ]                                       │
└────────────────────────────────────────────────────────────────────────────┘
```

## Evaluaciones

No lo diseñes como una gráfica de marketing. Muéstralo como control de calidad.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Evaluaciones                                              [ Ejecutar suite ]│
│ Dataset: pharma-safety-v1 · 25 casos · Última ejecución: hace 4 min        │
├───────────────────────┬───────────────────┬─────────────┬──────────────────┤
│ MÉTRICA               │ prompt.v1.2       │ prompt.v1.3 │ OBJETIVO         │
├───────────────────────┼───────────────────┼─────────────┼──────────────────┤
│ Fuentes válidas       │ 88%               │ 100%        │ ≥ 98%            │
│ Claims respaldados    │ 84%               │ 96%         │ ≥ 95%            │
│ Bloqueos correctos    │ 92%               │ 100%        │ 100%             │
│ Fugas cross-tenant    │ 0                 │ 0           │ 0                 │
│ Prompt injection      │ 84%               │ 100%        │ 100%             │
│ Latencia media        │ 1.9 s             │ 2.3 s       │ < 4 s            │
│ Coste medio           │ €0.009            │ €0.011      │ < €0.03          │
└───────────────────────┴───────────────────┴─────────────┴──────────────────┘
```

## Estado del sistema

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Estado del sistema                                                          │
├──────────────────────┬─────────────┬───────────────────────────────────────┤
│ API FastAPI          │ ● Healthy   │ p95 latency: 184 ms                    │
│ PostgreSQL + RLS     │ ● Healthy   │ 0 policy violations                    │
│ Redis / Workers      │ ● Healthy   │ Queue: 3 jobs                          │
│ Vector search        │ ● Healthy   │ Index: up to date                      │
│ LLM Provider         │ ● Degraded  │ Retry enabled · 1 timeout last hour    │
│ Object storage       │ ● Healthy   │ 30 demo documents                      │
└──────────────────────┴─────────────┴───────────────────────────────────────┘
```

No expongas claves, tokens, prompts sin redacción ni datos internos excesivos. El objetivo es observabilidad operativa sin convertir el panel en una fuga de información.

## Pantalla de login

Minimalista, elegante y sin aspecto de demo barata.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│                                                                            │
│              ◈  PHARMA SANDBOX                                             │
│                  Commercial AI                                             │
│                                                                            │
│        Un entorno controlado para soporte comercial farmacéutico            │
│                                                                            │
│        ┌──────────────────────────────────────────────┐                    │
│        │ Email                                        │                    │
│        │ laura.garcia@novapharma.demo                 │                    │
│        ├──────────────────────────────────────────────┤                    │
│        │ Contraseña                                   │                    │
│        │ •••••••••••••                                │                    │
│        ├──────────────────────────────────────────────┤                    │
│        │              [ Acceder ]                      │                    │
│        └──────────────────────────────────────────────┘                    │
│                                                                            │
│        Accesos demo: Laura / Compliance / Auditor / BioHealth              │
│                                                                            │
│        DEMO ENVIRONMENT — SYNTHETIC DATA ONLY                              │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## Componentes clave

Crea estos componentes reutilizables desde el principio:

```text
AppShell
SidebarNavigation
TopBar
TenantBadge
RoleBadge
StatusBadge
RiskBadge
SourceCitation
DocumentStatusBadge
ComplianceDecisionCard
AuditEventRow
TraceDrawer
AiDisclosureBanner
ConfidenceMeter
PolicyFlag
EmptyState
FailureTestCard
MetricCard
DataTable
ConfirmDialog
RedactedText
```

Estados y etiquetas:

```text
● Approved
● Needs review
● Reviewed by compliance
● Blocked by policy
● Source verified
● Synthetic data only
● Cross-tenant access denied
● Model provider degraded
```

## Prototipo navegable

Para tu demo, el recorrido de interfaz tiene que ser exactamente este:

```text
Login Laura (NovaPharma)
  → Dashboard
  → Preparar reunión de CardioX con Dr. Javier Martín
  → Briefing con citas
  → Pregunta sin fuente en chat documental
  → Respuesta: información insuficiente
  → Simulador de conversación
  → Resumen posterior y tareas
  → Respuesta sensible a compliance
  → Login María (Compliance)
  → Aprobar / rechazar y dejar motivo
  → Auditoría del flujo
  → Failure Lab: acceso BioHealth bloqueado
  → Evaluaciones: prompt v1.2 frente a v1.3
```

Este diseño mantiene una interfaz comercial sencilla para Laura y una capa más técnica para compliance, auditoría y evaluación. Así no pareces haber hecho “otro chat con PDFs”: pareces haber construido un sistema donde la IA tiene límites, permisos, pruebas y responsabilidad.