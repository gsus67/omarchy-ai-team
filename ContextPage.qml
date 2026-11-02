pragma ComponentBehavior: Bound
import QtQuick
import "I18n.js" as I18n
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons
import qs.Commons as Commons

ColumnLayout {
  id: root
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  property var info: ({})
  property bool busy: false
  property string scope: ""
  readonly property var fonts: Style.font
  readonly property var metrics: Style.spacing
  readonly property var values: root.scope === "" ? (root.info.settings || ({})) : ((root.info.resolved || ({}))[root.scope] || ({}))
  // native = the CLI enforces it; otherwise it is instructions the session follows.
  readonly property var flags: [
    {key: "task_plan_with_pointers", label: root.tr("Encargo con punteros"), native: false},
    {key: "fresh_worker_per_subtask", label: root.tr("Obrero nuevo por subtarea"), native: false},
    {key: "bounded_reads", label: root.tr("Lectura acotada"), native: false},
    {key: "stable_prompt_prefix", label: root.tr("Prefijo estable"), native: true},
    {key: "measure_task_tokens", label: root.tr("Medir tokens por tarea"), native: true},
    {key: "clear_between_tasks_below_floor", label: root.tr("Limpiar bajo el piso"), native: false},
    {key: "group_related_subtasks", label: root.tr("Agrupar subtareas relacionadas"), native: false},
    {key: "explore_with_simple_role", label: root.tr("Explorar con Tareas sencillas"), native: false},
    {key: "handoff_on_pause", label: root.tr("Relevo al pausar"), native: false},
    {key: "suggest_resume_after_idle", label: root.tr("Avisar tras inactividad"), native: true}]
  readonly property var numbers: [
    {key: "compact_at_tokens", label: root.tr("Compactar a los (tokens)"), native: true},
    {key: "compact_floor_tokens", label: root.tr("Piso de contexto (tokens)"), native: false},
    {key: "worker_report_max_tokens", label: root.tr("Informe del obrero (tokens)"), native: false}]
  readonly property var reportEvents: [
    {key: "milestone", label: root.tr("hitos")},
    {key: "blocked", label: root.tr("bloqueos")}]
  signal commandRequested(var arguments)
  spacing: Style.space(6)

  function command(name, key, value) {
    var args = [name, key, value]
    if (root.scope !== "") args.push("--role", root.scope)
    root.commandRequested(args)
  }
  function modeText(native) { return native ? root.tr("Nativo") : root.tr("Cooperativo") }

  Ui.PanelSectionHeader { text: root.tr("CONTEXTO") }
  Ui.ButtonGroup {
    objectName: "context-scope"
    spacing: root.metrics.xxs
    fontSize: root.fonts.caption
    options: [{value: "", label: root.tr("Todos")}, {value: "jefe", label: root.tr("Jefe")},
              {value: "plan", label: root.tr("Planificar")}, {value: "code", label: root.tr("Programar")},
              {value: "review", label: root.tr("Revisar")}, {value: "simple", label: root.tr("Sencillas")}]
    value: root.scope
    onChanged: value => root.scope = value
  }
  Text {
    Layout.fillWidth: true
    text: root.tr("Se aplica en sesiones nuevas.")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
  }
  // Global-only settings: a role row cannot override them, so they only show in "Todos".
  CompactRow {
    objectName: "context-enabled"
    visible: root.scope === ""
    Layout.fillWidth: true
    label: root.tr("Política de contexto activa")
    status: root.modeText(false)
    navigationEnabled: false
    checked: root.values.enabled !== false
    busy: root.busy
    onToggled: root.command("context-set", "enabled", String(!checked))
  }
  CompactRow {
    objectName: "context-dump_state_before"
    visible: root.scope === ""
    Layout.fillWidth: true
    label: root.tr("Guardar estado antes de compactar")
    status: root.modeText(true)
    navigationEnabled: false
    checked: root.values.dump_state_before !== false
    busy: root.busy
    onToggled: root.command("context-set", "dump_state_before", String(!checked))
  }
  Repeater {
    model: root.scope === "" ? root.reportEvents : []
    delegate: CompactRow {
      id: report
      required property var modelData
      readonly property var current: root.values.worker_report_on || ["milestone", "blocked"]
      objectName: "context-worker_report_on-" + report.modelData.key
      Layout.fillWidth: true
      label: root.tr("Informe del obrero en") + ": " + report.modelData.label
      status: root.modeText(false)
      navigationEnabled: false
      checked: report.current.indexOf(report.modelData.key) >= 0
      // At least one event must stay on: the backend rejects an empty list.
      busy: root.busy || (report.checked && report.current.length === 1)
      onToggled: {
        var next = report.checked
          ? report.current.filter(key => key !== report.modelData.key)
          : root.reportEvents.map(event => event.key).filter(key => key === report.modelData.key || report.current.indexOf(key) >= 0)
        root.command("context-set", "worker_report_on", JSON.stringify(next))
      }
    }
  }
  // Fixed by design: shown so the whole policy is visible, not editable.
  Text {
    visible: root.scope === ""
    Layout.fillWidth: true
    text: root.tr("Se aplica al lanzar la sesión · el Jefe recibe un índice de memoria (fijo).")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
  }
  Repeater {
    model: root.flags
    delegate: CompactRow {
      id: flag
      required property var modelData
      objectName: "context-" + flag.modelData.key
      Layout.fillWidth: true
      label: flag.modelData.label
      status: root.modeText(flag.modelData.native)
      navigationEnabled: false
      checked: root.values[flag.modelData.key] !== false
      busy: root.busy
      onToggled: root.command("context-set", flag.modelData.key, String(!flag.checked))
    }
  }
  Ui.NumberField {
    objectName: "context-read_window_lines"
    Layout.fillWidth: true
    label: root.tr("Ventana de lectura (líneas)")
    from: 1
    to: 2000
    value: root.values.read_window_lines || 120
    enabled: !root.busy
    onModified: value => root.command("context-set", "read_window_lines", String(value))
  }
  Ui.NumberField {
    objectName: "context-idle_cache_ttl_minutes"
    Layout.fillWidth: true
    label: root.tr("Caché inactiva (minutos)")
    from: 1
    to: 1440
    value: root.values.idle_cache_ttl_minutes || 55
    enabled: !root.busy
    onModified: value => root.command("context-set", "idle_cache_ttl_minutes", String(value))
  }
  // Token limits can reach 64 bits: the backend snapshot sends them as decimal text and they stay
  // text here, never JavaScript numbers (which round above 2^53).
  Repeater {
    model: root.numbers
    delegate: RowLayout {
      id: limit
      required property var modelData
      Layout.fillWidth: true
      spacing: root.metrics.xxs
      Text {
        Layout.fillWidth: true
        text: limit.modelData.label + " · " + root.modeText(limit.modelData.native)
        textFormat: Text.PlainText
        color: Commons.Color.foreground
        font.family: root.fonts.family
        font.pixelSize: root.fonts.caption
        elide: Text.ElideRight
      }
      Ui.TextField {
        id: field
        objectName: "context-" + limit.modelData.key
        Layout.preferredWidth: Style.space(70)
        text: typeof root.values[limit.modelData.key] === "string" ? root.values[limit.modelData.key] : ""
        enabled: !root.busy
      }
      Ui.Button {
        objectName: "context-save-" + limit.modelData.key
        text: root.tr("Guardar")
        focusable: true
        enabled: !root.busy && /^[0-9]+$/.test(field.text)
        onClicked: root.command("context-set", limit.modelData.key, field.text)
      }
    }
  }
  Text {
    Layout.fillWidth: true
    text: root.tr("Pi sigue pendiente con esta política: ábrelo desactivándola explícitamente.")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
  }
  Ui.Button {
    objectName: "context-reset"
    text: root.tr("Restaurar recomendados")
    focusable: true
    enabled: !root.busy
    onClicked: root.commandRequested(root.scope === "" ? ["context-reset"] : ["context-reset", "--role", root.scope])
  }
}
