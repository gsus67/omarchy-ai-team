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

Pi con Nous solo ofrece modelos gratuitos. Grok con xAI es de pago y requiere tu consentimiento explícito. Solo los errores de la cuenta pausan Nous para todas las sesiones. Nous no publica un cliente OAuth para terceros, así que ese inicio de sesión usa el de Hermes y podría dejar de funcionar; las API keys de `/login nous` las guarda Pi en texto plano.

## Desarrollo

```sh
make check
```

Ejecuta el validador del plugin, los tests, qmllint y la prueba visual en Wayland, con perfiles temporales y sin inferencia. Las reglas para trabajar en este repo están en [AGENTS.md](AGENTS.md).

## Licencia

[MIT](LICENSE)
