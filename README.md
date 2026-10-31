# Omarchy AI Team

El identificador interno es `gsus67.ai-team`; la configuración, los datos y las skills usan `omarchy-ai-team` y `ai-team-*`. Para instalaciones anteriores, consulta la migración al final de este documento.

Plugin nativo de Omarchy con un panel compacto de 380 de ancho, selector múltiple
de agentes y filas de una línea para configurar metodología, memoria compartida
y herramientas locales desde una pantalla organizada por objetivos:

- Selector múltiple de **agentes**: Claude Code, Codex, Codex-2 y Pi; Grok/xAI y
  opciones gratuitas de Nous mediante Pi. Las páginas de agente están en
  **Ajustes**.
- Cuatro objetivos: **Calidad** (Superpowers), **Memoria compartida** (ECC),
  **Ahorro de tokens** y **Seguridad** (grupos del mismo catálogo y una skill).
  ECC reúne memoria, copias, exportación y restauración.
- Acciones **Abrir predeterminado**, **Actualizar todo** y **Ajustes**.
- En **Ajustes**: idioma (Sistema, Español o English), **Equipo** y **Comprobar
  con IA**, que prueba cada agente seleccionado en un proyecto temporal solo
  cuando lo pulsas.

Seleccionas los perfiles una vez y activas cada función con un solo interruptor.
Abres tu CLI habitual en cualquier proyecto y pides tareas en lenguaje natural;
el proyecto de memoria se determina desde la sesión. No hay un orquestador:
Superpowers aporta planificación, TDD y revisión dentro de las sesiones nativas.
ECC Memory Vault es el único motor central, local y de código abierto. Cada
sesión conserva su contexto privado; los worktrees comparten la memoria del
repositorio y los proyectos distintos permanecen separados.

En **Ajustes → Equipo** puedes activar el reparto de Planificar, Programar,
Revisar y Tareas sencillas y elegir modelo y esfuerzo por separado, o **Usar
recomendados**. La skill nativa `ai-team-roles` indica a tu sesión habitual
cómo delegar mediante las CLI existentes con sus permisos y sandboxes. Los
recomendados evitan `max` y `ultra`; elegir `ultra` manualmente muestra un aviso.
Claude reparte el trabajo con peticiones naturales. Codex como jefe no puede
lanzar otras CLI dentro de su sandbox, así que conviene usar Claude para dirigir.

## Estado

Implementado:

- Panel de barra con `Ui.Panel` y `Ui.KeyboardPanel`, anclaje al icono, foco,
  Escape, cierre exterior y ajuste al espacio disponible. Pulsar el nombre de
  una pieza abre sus ajustes; solo el interruptor cambia su activación.
- Selección única de agentes/perfiles, conexiones parciales visibles y reintento.
  Claude/Codex usan los gestores oficiales y mise cuando corresponde; no se
  copian credenciales ni se crean perfiles nuevos al detectar o actualizar.
- Superpowers se instala o reutiliza mediante su integración oficial. Activar
  y actualizar actúan sobre los perfiles seleccionados; desactivar conserva la
  instalación. Codex recibe una instrucción breve que remite a sus skills,
  preservando instrucciones previas mediante la API atómica de la CLI.
- MCP oficial ECC compartido por proyecto, con diagnóstico nativo. No instala el
  catálogo completo ECC, otra metodología, hooks de captura ni otro motor.
- Exportación ZIP portable de documentos Markdown y hashes, todos los proyectos
  o uno, sin cuentas, configuraciones ni transcripciones. Restauración con vista
  previa, confirmación y reasociación opcional a otra carpeta Git; conserva
  documentos idénticos y bloquea conflictos.
- Respaldos con destino libre accesible al sistema, frecuencia diaria inicial y
  retención. Programación expresa con systemd user timer persistente, reintento
  horario y comprobación de identidad del destino. Solo aplica retención después
  de verificar una copia nueva; conserva la última buena y archivos ajenos.
- Catálogo local con búsqueda, filtros y Automático/Activada/Desactivada, general
  o por proyecto. Automático ofrece utilidades instaladas y comprobadas sin
  instalar todo el catálogo. La skill nativa consulta las capacidades desde la
  sesión, respetando los permisos de la CLI.
- Actualización individual y conjunta desde los canales oficiales, conservando
  preferencias, datos y sesiones. ECC se prepara en staging, valida la copia y
  conserva el runtime anterior; mise conserva versiones previas. Un no-op se
  distingue de una sustitución real. Instalaciones ajenas mantienen su gestor.
- **Abrir predeterminado** y el clic derecho en el icono usan el mismo lanzador
  de terminal de Omarchy con el perfil elegido, su modelo y permisos nativos.
  Los accesos opcionales **(AI Team)** del menú abren perfiles seleccionados.
- Pi conserva su CLI y permisos propios, sin sandbox de sistema ni aprobación
  por herramienta. El catálogo incluido se consulta sin inferencia; el modelo
  predeterminado se aplica a sesiones nuevas. xAI requiere consentimiento de
  pago explícito. Nous filtra modelos `:free` de precio cero con herramientas;
  el RPM declarado y margen inicial del 25 % se comparten entre sesiones
  gestionadas, sin proxy ni fallback de pago. En Pi, `/login` → cuenta →
  **Nous Portal** inicia sesión con OAuth y guarda los tokens en el llavero cifrado
  con `secret-tool`; sin llavero no se inicia sesión ni se usa texto plano. Desactivar Nous en el
  panel borra esa sesión; `/logout` en Pi no la borra del llavero. Nous no
  publica un cliente para terceros: el OAuth usa el de Hermes y puede dejar de
  funcionar si Nous lo cambia. `/login nous` guarda una API key en texto plano en
  `auth.json` de Pi: evítalo y usa en su lugar una clave `!secret-tool lookup …`.

Los cambios nativos se cargan en sesiones nuevas. Detectar, actualizar el panel
o activar una preferencia no inicia inferencia. Desactivar retira únicamente
la integración gestionada, conservando instalación y datos; no bloquea usos
externos a los controles nativos.

No se declara aceptación completa de toda tarea/modelo. Las conexiones
autenticadas y la calidad de xAI/Nous siguen pendientes. Las guías ECC diferidas no quedan disponibles por activar
Memory Vault. La recuperación se comprobó con datos sintéticos, sin reinstalar
físicamente esta máquina.

## Instalación de desarrollo

Requiere Omarchy shell, Python 3 y mise para herramientas gestionadas. No hay
paquetes Python externos. Al activar memoria se reutilizan Node/npm o se preparan
con mise y se instala el runtime oficial ECC sin scripts del instalador general.
El identificador es `gsus67.ai-team`.

El instalador y actualizador nativos son las referencias técnicas:

```sh
omarchy plugin add https://github.com/gsus67/omarchy-ai-team.git --enable
omarchy plugin update gsus67.ai-team
```

Esta versión se desarrolla en la rama `feat/team-roles`; no se ha
publicado ni aplicado al plugin instalado. Para revisar la interfaz local sin
instalarla: `python3 tools/preview.py`.

## Uso habitual

1. Abre AI Team con clic izquierdo y selecciona perfiles en el selector
   múltiple de agentes. En **Ajustes** puedes abrir sus páginas y completar la
   autenticación nativa cuando haga falta.
2. Activa los objetivos necesarios. En **Memoria compartida** elige destino,
   frecuencia y retención; activa la programación expresamente.
3. Abre tu CLI habitual en el proyecto y pide la tarea. En **Ajustes** puedes
   elegir un predeterminado y accesos opcionales de menú; el clic derecho abre
   ese mismo perfil sin cambiar sus permisos.
4. Usa **Memoria compartida → Exportar y restaurar** para revisar la restauración antes de
   confirmarla. Las actualizaciones de herramientas se ofrecen desde el panel.

## Comprobaciones

```sh
make check
```

Ejecuta validador nativo, unittest, qmllint y smoke Wayland del desplegable.
El smoke usa HOME/XDG y perfiles temporales, ejecutables sintéticos y capturas
ignoradas en `artifacts/phase1/`, sin inferencia ni instalaciones reales.
`make test` usa salida breve; `make test-verbose` conserva el detalle.


## Desarrollo

Consulta primero documentación, scripts y versiones locales. Reutiliza las
integraciones oficiales; no copies implementaciones de dependencias ni crees
otro agente, router, memoria o coordinador. Usa herramientas deterministas antes
de inferencia. El núcleo/ECC precede a ampliaciones de Pi/Nous/Grok. Una mejora
de tokens exige tareas equivalentes y calidad comprobada, no solo menos bytes.
