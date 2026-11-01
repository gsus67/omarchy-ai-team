import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execFileAsync = promisify(execFile);
const capsuleText = process.env.AI_TEAM_CONTEXT;
const capsule = JSON.parse(capsuleText || "{}");
const policy = capsule.policy;
const messages = JSON.parse(process.env.AI_TEAM_PI_MESSAGES || "{}");
const stateScript = fileURLToPath(new URL("../backend/context_state.py", import.meta.url));
const summary = "Retén el plan, decisiones, archivos:líneas, errores literales y pendientes; descarta herramientas crudas y logs.";

export default function (pi) {
  const log = (ctx, message) => {
    if (ctx.hasUI) ctx.ui.notify(message, "info");
    else console.error(message);
  };
  pi.on("turn_end", (_event, ctx) => {
    const tokens = ctx.getContextUsage()?.tokens;
    if (tokens == null) {
      log(ctx, messages.unknown || "Contexto: tokens desconocidos; no se compacta.");
      return;
    }
    if (tokens <= policy.compact_at_tokens || tokens < policy.compact_floor_tokens) return;
    ctx.compact({ customInstructions: summary });
  });
  pi.on("session_before_compact", async (event, ctx) => {
    const tokens = event.preparation.tokensBefore;
    if (tokens == null || tokens < policy.compact_floor_tokens) {
      log(ctx, tokens == null ? messages.unknown || "Contexto: tokens desconocidos; no se compacta." : messages.floor || "Contexto: compactación bajo el piso cancelada.");
      return { cancel: true };
    }
    if (policy.dump_state_before) {
      try {
        const { stdout } = await execFileAsync("python3", [stateScript, "precompact", "--instance", capsuleText], { encoding: "utf8", timeout: 90000 });
        const result = JSON.parse(stdout);
        log(ctx, (messages.status || "Contexto precompact: {status}").replace("{status}", result.status));
      } catch (error) {
        let status = "failed";
        try { status = JSON.parse(error.stdout).status || status; } catch {}
        log(ctx, (messages.status || "Contexto precompact: {status}").replace("{status}", status));
      }
    }
  });
}
