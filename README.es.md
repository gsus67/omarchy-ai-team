# Omarchy AI Team

[English](README.md)

Panel de Omarchy que hace trabajar como un equipo a los agentes de IA que elijas. Configura las herramientas que ya usas y no añade ningún agente, orquestador ni motor de memoria propio.

- **Agentes:** Claude Code, Codex, Codex-2 y Pi, con sus propias CLI, sesiones y permisos.
- **Equipo:** asigna modelo y esfuerzo a Planificar, Programar, Revisar y Tareas sencillas, con alternativas permitidas si un modelo no responde.
- **Calidad:** Superpowers como método de trabajo.
- **Memoria compartida:** ECC Memory Vault, local y de código abierto, con una memoria por proyecto.
- **Tokens y seguridad:** un catálogo de herramientas locales que los agentes usan antes de la inferencia.
- **Mantenimiento:** actualizaciones, copias de la memoria, exportación y restauración desde el panel.

## Requisitos

Omarchy shell, Python 3 y mise. Al activar la memoria se reutiliza Node o se prepara con mise. El inicio de sesión OAuth de Nous necesita `secret-tool` y el llavero desbloqueado.

## Instalación

```sh
omarchy plugin add https://github.com/gsus67/omarchy-ai-team.git --enable
```

Para actualizar, usa `omarchy plugin update gsus67.ai-team`. Después, el plugin reinicia el shell de Omarchy una vez para no quedarse con código viejo; tus ventanas siguen abiertas.

Para quitarlo, usa `omarchy plugin remove gsus67.ai-team`. Antes, desactiva cada objetivo en el panel para retirar lo que configuró.

## Uso

1. Pulsa el icono de la barra y elige tus agentes. Si hace falta, completa cada sesión en **Ajustes**.
2. Activa los objetivos que quieras. En **Ajustes → Equipo** puedes ajustar los modelos.
3. Abre tu agente desde el panel y habla con naturalidad: "vamos a trabajar en el proyecto X". Carga la memoria de ese proyecto, y varios proyectos en una misma sesión se mantienen separados.

Las normas comunes van en un proyecto llamado `compartido`. Desde otra carpeta solo se pueden nombrar los repos que estén directamente en `~/Work`.

**Guardar y parar:** al llegar al 90 % del consumo de Claude o Codex, de 5 horas o semanal, se ordena a las sesiones parar y dejar un relevo detallado en la memoria del proyecto. Cambia el límite o desactívalo en **Ajustes → Equipo**. Codex pide aprobarlo una vez con `/hooks`.

Pi con Nous solo ofrece modelos gratuitos. Grok con xAI es de pago y requiere tu consentimiento explícito. Solo los errores de la cuenta pausan Nous para todas las sesiones. Nous no publica un cliente OAuth para terceros, así que ese inicio de sesión usa el de Hermes y podría dejar de funcionar; las API keys de `/login nous` las guarda Pi en texto plano.

## Contexto por lanzamiento

Las aperturas del panel y las recetas de Equipo usan la sección `context` del JSON del plugin (`$XDG_CONFIG_HOME/omarchy-ai-team/settings.json`, por defecto `~/.config/omarchy-ai-team/settings.json`). Se aplica solo a procesos nuevos; nunca escribe ajustes globales de las CLI ni cambia modelo, permisos o sandbox. Las sesiones abiertas conservan su configuración.

```json
{
  "context": {
    "enabled": true,
    "apply": "launch",
    "compact_at_tokens": 150000,
    "compact_floor_tokens": 70000,
    "roles": {"jefe": {"compact_at_tokens": 100000}},
    "dump_state_before": true,
    "chief_brief": "memory_index",
    "worker_report_max_tokens": 800,
    "worker_report_on": ["milestone", "blocked"]
  }
}
```

Todos los números son recomendaciones ajustables. Cada papel (`jefe`, `plan`, `code`, `review`, `simple`) puede ajustar umbral, piso y presupuesto del informe. Una ejecución puede pasar `--context-override '{"compact_at_tokens":180000,"reason":"terminar una frontera de depuración"}'`; la excepción motivada prevalece sobre papel, ajustes y defaults, sin guardarse. `apply=launch` y la separación de configuración global son fijos. Claude admite umbrales nativos de 100000 a 1000000. `enabled=false` conserva argumentos, entorno y salida nativos, incluida la ausencia de filtro de informes; también se admite como excepción motivada. Autenticación queda fuera de la política.

Cada instancia recibe UUID y cwd fijo: los worktrees conservan la identidad de proyecto que ya resuelve ECC. El agente entrega por stdin un JSON explícito con listas `plan`, `decisions`, `files`, `errors`, `pending` y booleanos opcionales `safe_boundary`, `debug` al comando de checkpoint mostrado en sus instrucciones (`python3 backend/context_state.py checkpoint`, o `python3 backend/harness.py context-checkpoint`). ECC guarda el relevo y lo relee para verificarlo; no se extrae estado de conversaciones ni herramientas. El evento PreCompact guarda un puntero al checkpoint de esa instancia. Ausente, inválido, ya usado o fallo de ECC quedan como incompleto/fallo explícito; no se fabrica un resumen ni se garantiza un veto de compactación. `dump_state_before=false` omite ese hook y su garantía de retención. El Jefe recibe un índice ECC de títulos acotados e identificadores del proyecto y `compartido`, sin cuerpos ni extractos, con metadatos tratados como datos no confiables y estado pendiente si falla. Sin proyecto no se consulta otro por defecto.

Claude recibe hooks y prompt por proceso. Los interruptores heredados `DISABLE_AUTO_COMPACT`/`DISABLE_COMPACT` activos detienen la apertura con un error; hay que resolverlos o desactivar la política explícitamente. Codex recibe overrides y hooks SessionStart/PreCompact por proceso, conservando instrucciones de desarrollador existentes y revisión nativa de confianza: hasta aprobar los hooks, instrucciones e índice están pendientes. Una UUID nueva puede requerir nueva aprobación. La configuración global nativa y sus controles pueden impedir la compactación: un parser válido no acredita el valor efectivo en sesión.

Las recetas `python3 backend/context_run.py --agent codex-2 --role review --cwd '<dir>' -- codex exec -s read-only -m '<modelo>' '<encargo>'` usan el mismo resolver y aceptan `--settings` y `--context-override`. No eligen tareas ni modelos. Con política activa consumen salida estructurada nativa y descartan eventos de herramientas y stderr; solo devuelven JSON con `status`, `references`, `tests`, `error`, `pending`. El presupuesto de transporte usa aproximadamente cuatro bytes UTF-8 por token, con un sobre mínimo de 256 bytes: no es un contador de tokens. Los informes extensos se guardan y verifican en ECC y se devuelven por referencia; un rechazo no reenvía su contenido. Los errores fatales estructurados se conservan acotados o por referencia; si la CLI solo escribió stderr, se informa del código de salida y de que stderr fue descartado. No hay copia local de transcripciones.

El piso, las fases seguras, debug, operaciones irreversibles y la regla de que el Jefe solo delegue son cooperativos donde no hay guarda nativa. Los obreros reportan por hito o bloqueo, sin logs crudos. Se permiten excepciones justificadas al umbral; no se promete máximo duro ni detección determinista de fases/tokens. **Pi sigue pendiente con política activa** y falla explícitamente; puede abrirse con una excepción motivada `enabled=false`. No se ha probado una extensión Pi en sesión. Los tests sintéticos y probes de parser no demuestran confianza de hooks, compactación real ni ventanas efectivas.

### Ajustes de ahorro

Objetivo: menos tokens de entrada acumulados por tarea aceptada, incluidos los leídos de caché. Un prefijo estable puede mejorar el aprovechamiento de caché; por sí solo no reduce lo que se relee. Todas las claves viven en `context`, admiten los mismos overrides por papel y excepciones motivadas, y se aplican en sesiones nuevas.

| Clave | Valor / rango | Tipo |
|---|---|---|
| `task_plan_with_pointers` | `true` | Cooperativo: el encargo lleva objetivo, referencias `archivo:línea`, aceptación y lista «no releer»; se relee si cambian los archivos, aparece un fallo o la evidencia es insuficiente |
| `fresh_worker_per_subtask` | `true` | Receta nativa (`codex exec --ephemeral`, `claude -p --no-session-persistence`; se rechazan `resume`/`fork` y, en Claude, `--resume`/`--continue`/`-c`, mientras `-c clave=valor` de Codex sigue siendo válido); abrir un obrero por subtarea es cooperativo |
| `bounded_reads` / `read_window_lines` | `true` / `120` (1–2000) | Cooperativo: leer por ventanas, no archivos enteros |
| `stable_prompt_prefix` | `true` | El texto del plugin deja la UUID y el comando de checkpoint al final; Claude añade `--system-prompt-snapshot on`; Codex no tiene un control equivalente |
| `measure_task_tokens` | `true` | Contadores nativos de los eventos que el wrapper ya procesa; instantáneas numéricas privadas en `$XDG_STATE_HOME/omarchy-ai-team/context-usage/<uuid>/` |
| `clear_between_tasks_below_floor` | `true` | Cooperativo: sugiere `/clear` tras guardar el relevo; nunca se envía solo |

Se editan en **Ajustes → Contexto** o con `python3 backend/harness.py context-set <clave> <json> [--role <papel>]` y `context-reset [--role <papel>]` (los enteros de 64 bits viajan como texto decimal). Solo cambia el `settings.json` del plugin; no se toca ningún perfil. Tokens por tarea: `python3 tools/session_usage.py --task-tokens --before before.json --after after.json` (en Codex `input_tokens` ya incluye los cacheados; en Claude se suman entrada nueva, lectura y creación de caché). El coste aparece solo si la CLI lo entrega. El benchmark sintético `python3 tools/context_benchmark.py` es aritmética sobre fixtures inventados y **no** acredita ahorro real; el protocolo A/B real está en `docs/RENDIMIENTO.md` (local).

La CLI que abre el botón principal **Abrir predeterminado** se elige en la fila **Jefe** de **Ajustes → Equipo**; la preferencia guardada no cambia.

## Desarrollo

```sh
make check
```

Ejecuta el validador del plugin, los tests, qmllint y la prueba visual en Wayland, con perfiles temporales y sin inferencia. Las reglas para trabajar en este repo están en [AGENTS.md](AGENTS.md).

## Licencia

[MIT](LICENSE)
