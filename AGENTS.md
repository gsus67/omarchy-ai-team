# Omarchy AI Team — instrucciones de desarrollo

## Objetivo y arquitectura

Mantener un plugin nativo gratuito, de código abierto, sencillo y modular que
configure herramientas existentes. Priorizar el menor volumen de código propio:
reutilizar Omarchy, configurar integraciones oficiales y añadir solo el puente
mínimo indispensable. No crear otro agente, router, coordinador o memoria;
el reparto nativo autorizado se limita a [Equipo por papeles](#equipo-por-papeles--2026-10-30).

Consultar primero la versión instalada, scripts y documentación local de Omarchy.
No asumir que una función del repositorio más reciente existe en esta máquina.
Consultar fuentes externas solo para detalles ausentes o que exijan actualidad,
cuando la tarea autorice red. Mantener configuración propia separada del código
original de las dependencias y de los proyectos del usuario.

## Herramientas y funciones

- Omarchy: componentes gráficos, plugins, selección/terminal, mise y consumo.
- Claude Code, Codex y Codex-2: CLI nativas para desarrollo y revisión; modelos,
  permisos, autenticación y contexto propios de cada sesión.
- Superpowers: única metodología de planificación, TDD, depuración y revisión.
- ECC Memory Vault: único motor central de memoria persistente, OSS y local,
  compartido entre agentes con separación lógica por proyecto. Decisión del
  usuario (2026-10-31): `backend/memory_projects.mjs` es una capa fina que elige
  el almacén en cada llamada, para trabajar en varios proyectos desde una sesión.
  No guarda ni valida por su cuenta; todo lo hace el servicio oficial de ECC.
- Pi: agente adicional con Grok/xAI y Nous mediante proveedores nativos.
- Catálogo local: herramientas deterministas, sin hooks,
  inferencia ni otra memoria al auditar.

Las alternativas se evalúan aisladas con datos sintéticos, nunca conectando dos
motores de memoria a sesiones reales. Una sustitución necesita evidencia comparable,
exportación/restauración, copia recuperable y retirada de la conexión anterior.
Memoria local no significa extracción o embeddings locales si usan APIs externas:
no declarar operación local completa sin comprobarla. AgentMemory, mem0 y Graphiti
son evaluaciones históricas, no motores simultáneos activos.

## Interfaz nativa y uso habitual

Usar una pantalla única de 380 de ancho con cabecera de estado y selector
múltiple de agentes; las páginas de agente viven en **Ajustes**. Incluir cuatro
objetivos con un interruptor cada uno, **Abrir predeterminado**, **Actualizar
todo** y **Ajustes**. Las filas ocupan una línea. Las listas cortas usan botones
segmentados para que ninguna opción quede recortada. Tocar nombre/estado abre
la página; solo el interruptor activa o desactiva para los perfiles
seleccionados. Memoria compartida agrupa memoria, copias y recuperación.
Seguir siempre el tema activo de Omarchy: sin colores, fuentes ni tamaños
literales en QML (`tests/test_qml_theme.py`).

El idioma sigue al sistema, con selector Sistema/Español/English en **Ajustes**.
El español es el texto fuente; todo texto visible nuevo necesita entrada en
`translations/en.json` (lo vigila un test). Skills e instrucciones para agentes
no se traducen. Las filas informativas no reaccionan al puntero.

Reutilizar `Ui.Panel` y `Ui.KeyboardPanel`, con anclaje al botón, tema, espacio
disponible, foco, Escape, cierre exterior y coordinación entre paneles. No usar
`FloatingWindow` ni la galería de desarrollo como patrón del plugin de barra.
El desplegable requiere Wayland; Qt offscreen no carga su backend `PanelWindow`.

El usuario abre su CLI habitual en cualquier proyecto y pide tareas en lenguaje
natural; no debe repetir una instrucción especial ni elegir una ruta por turno.
La memoria determina el proyecto desde la sesión. El panel puede ofrecer
**Abrir predeterminado**, clic derecho equivalente y accesos opcionales `.desktop`
mediante el lanzador existente de Omarchy, conservando modelo/permisos de la CLI.
No interceptar conversaciones ni atribuir coordinación automática a sesiones.

Activar detecta/reutiliza o instala lo necesario mediante el método oficial,
configura y comprueba la conexión. Mostrar por separado instalado, preferencia
habilitada, conexión comprobada y disponibilidad efectiva; conservar pendiente
hasta probarla. Los errores parciales identifican el perfil y ofrecen reintento.
Desactivar retira únicamente la conexión gestionada, conservando instalación y
datos. No promete bloquear ejecutables fuera de los permisos nativos. Desinstalar
es una acción distinta con confirmación explícita.

Los cambios nativos se cargan en sesiones nuevas, sin cerrar ni inyectar en las
abiertas. No seleccionar perfiles, conectar instalaciones nuevas o activar pagos
silenciosamente al actualizar. Las CLI conservan sus controles; no hay modos
normal/supervisado/autónomo ni modelos por etapa en el panel.

## Memoria, exportación y respaldos

Compartir conocimiento del proyecto, sin capturar transcripciones ni mezclar
contextos privados. Worktrees del mismo repositorio comparten identidad; proyectos
homónimos distintos se separan. Para trabajo paralelo usar Git worktrees cuando
corresponda, sin construir mensajería, locks de archivos o coordinación propia.

Exportar memoria completa o por proyecto en formato portable y recuperable,
sin credenciales por defecto. Reutilizar parser, diagnóstico y detector de
secretos oficiales. Restaurar con explicación, vista previa y confirmación;
conservar documentos válidos y bloquear conflictos. Permitir reasociar memoria
tras una migración de carpeta o máquina.

Destino libre: carpeta local, unidad externa, SMB montado o cualquier ruta
accesible. Frecuencia diaria por defecto, configurable, retención sencilla y
último respaldo visible. Programación expresa con systemd user timer persistente;
reintento seguro si estuvo apagado o el destino falta. Comprobar integridad antes
de marcar válida la copia y aplicar retención; nunca borrar la última buena tras
un fallo. No prometer atomicidad sobre escritores externos arbitrarios.

## Catálogo, ECC y consumo

Consultar [HERRAMIENTAS_LOCALES.md](docs/HERRAMIENTAS_LOCALES.md). Ahorro de
tokens y Seguridad son grupos del catálogo con una sola skill; el selector por
proyecto vive en **Avanzado**. Automático reutiliza utilidades pertinentes instaladas y comprobadas
mediante skills nativas; no instala el catálogo ni crea otro clasificador.
Distinguir detección, probe del binario y conexión real. Conservar preferencias.

Evaluar ECC selectivo según [ECC.md](docs/ECC.md), incluidos contexto, tokens y
seguridad. Revisar licencias, dependencias transitivas, hooks y compatibilidad.
No instalar todo el catálogo ni añadir dependencias porque una skill las mencione.
Una función principal por responsable; no duplicar memoria, metodología o captura.
Las piezas diferidas siguen pendientes hasta integrar y probar su ciclo de vida.

Usar `rg`, Git, parsers, linters y tests locales antes de inferencia. Salidas
breves conservan errores y exit status; menos bytes no prueban ahorro total.
Para comparar componentes, misma tarea, modelo, permisos y criterio de calidad.
Registrar tokens reportados, reintentos y coste cuando existan; distinguir
estimaciones y desconocidos. Modelos ligeros autorizados primero si bastan;
escalar solo con motivo. No activar proveedores de pago sin consentimiento.

Pi/Nous/Grok conservan su implementación y quedan al final para ampliaciones,
tras pendientes del núcleo/ECC. Respetar esperas nativas y cuotas reales; margen
inicial 20–25 % respecto de límites conocidos, sin inventar cuotas. Solo el
mecanismo local mínimo si falta coordinación oficial entre sesiones. Sin proxy,
servicio pesado ni fallback automático a pago. Pi no tiene sandbox de sistema
ni aprobación por herramienta: no simular controles que su integración no ofrece.

## Mantenimiento y seguridad

Acciones gráficas individuales y conjuntas mediante canales oficiales,
conservando activación, preferencias y datos. Actualizar solo instalados y
comprobar versiones reales: éxito con cero instalaciones actualizadas no prueba
sustitución. Preparar copia adecuada de datos persistentes y respetar sesiones
activas. Recuperación solo cuando el mecanismo la permita; no prometer revertir
migraciones irreversibles. Ver [ACTUALIZACIONES.md](docs/ACTUALIZACIONES.md).

No modificar Omarchy, dependencias o proyectos originales para integrar. No
editar el plugin instalado para ocultar fallos. No guardar secretos en logs,
QML, repositorio o respaldos expuestos. No enviar datos privados a proveedores
sin acción autorizada. Instalar solo componentes activados, minimizar permisos,
dependencias y procesos residentes. Verificar licencias/avisos antes de publicar.

## Desarrollo y comprobaciones

`docs/` es local e ignorado por Git: planes, diseños, pruebas y continuidad no se
suben; a GitHub solo va lo necesario. Antes de hacer pull de un commit que retire
archivos locales, copiarlos y restaurarlos. El CI solo corre con el repo público
y se omite en privado; qmllint y el smoke Wayland siguen siendo locales.

Trabajar en este repositorio. Leer [README.md](README.md),
[ACEPTACION.md](docs/ACEPTACION.md) y [PRUEBAS.md](docs/PRUEBAS.md). Preparar la tabla
función → responsable existente → integración mínima antes de ampliar funciones.
Orden: panel, CLI/Superpowers, memoria, recuperación, mantenimiento, secundarios.

Ejecutar `make check` después de cambios relevantes: validador, unittest,
qmllint y smoke Wayland. Preferencias, HOME/XDG y datos gráficos temporales;
detectar no debe instalar ni iniciar inferencia. `make test` tiene salida breve
y `make test-verbose` conserva detalle. Verificar acciones reales sin crear otro
motor de certificación de compatibilidad. Documentar fallos y límites sin fingir
PASS ni aceptación completa por una implementación parcial.

Mantener un único punto en [CONTINUIDAD.md](docs/CONTINUIDAD.md), con decisiones,
cambios, pruebas, restricciones y siguiente paso. Consultar
`python3 tools/session_usage.py` al iniciar y entre bloques; si falla, cuota
desconocida, sin inventar porcentajes. Umbrales heredados: 90 % usado de ventana
5h y 98 % semanal. Guardar y avisar al alcanzarlos; esta tarea no autoriza otros
escritores, subagentes, relevos automáticos ni timers. No copiar credenciales o
transcripciones para continuidad.

## Equipo por papeles — 2026-10-30

Decisión explícita del usuario: se permite delegar Planificar, Programar,
Revisar y Tareas sencillas según el [diseño aprobado](docs/superpowers/specs/2026-10-30-equipo-por-papeles-design.md).
Esta excepción sustituye la prohibición de reparto solo mediante la skill
nativa `ai-team-roles`: la sesión habitual dirige y usa las CLI existentes,
sus permisos y sandboxes, sin otro proceso, servicio, router ni orquestador.
El plugin configura los papeles; no clasifica ni ejecuta tareas.

Modelo y esfuerzo se configuran por separado en **Ajustes → Equipo**.
Los recomendados evitan `max` y `ultra`; `ultra` manual se permite con aviso.
Revisar usa un modelo distinto de Programar, salvo disponibilidad de uno solo,
con aviso. Pi solo se recomienda con modelos gratuitos comprobados; los de
pago conservan el consentimiento explícito existente. Las tareas triviales
las resuelve la sesión; las escrituras paralelas usan worktrees Git.

Esta decisión no autoriza tocar perfiles reales. Aceptación con peticiones naturales en
[PRUEBAS](docs/PRUEBAS.md): Claude dirige y reparte; Codex como jefe no puede
lanzar otras CLI dentro de su sandbox y debe informarlo, sin presentar ayudantes
internos como revisión independiente.

Decisión del usuario (2026-10-31): un hook nativo de Claude y Codex (`backend/usage_guard.py`)
avisa a la sesión al llegar al límite de consumo, 90 % por defecto, para que pare y deje
un relevo. Solo añade contexto: no bloquea, no lee la conversación ni ejecuta modelos.

## Sin orquestador

No se usa orquestador: Superpowers cubre la metodología y ECC aporta únicamente
memoria y piezas opcionales necesarias. No tocar perfiles reales `~/.claude`,
`~/.codex*`, `~/.pi` sin autorización expresa del usuario.
