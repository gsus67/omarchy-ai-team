pragma ComponentBehavior: Bound
import QtQuick
import "I18n.js" as I18n
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons

ColumnLayout {
  id: root
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  readonly property var fonts: Style.font
  readonly property var metrics: Style.spacing
  property string languagePreference: "system"
  property var agents: []
  property var launcher: ({})
  property var team: ({})
  property var aiCheck: ({})
  property var prices: ({})
  property var components: []
  property var lastReconcile: ({})
  property bool busy: false
  function setupStatus(agent) {
    if (agent.setupState === "ready") return root.tr("Listo")
    if (agent.setupState === "error") return root.tr("Con errores")
    if (agent.setupState === "pending") return root.tr("Pendiente")
    return agent.installation === "installed" ? agent.version || root.tr("Instalado") : root.tr("No instalado")
  }
  function repairedText() {
    const rows = (root.lastReconcile.repaired || []).map(row => {
      const agent = root.agents.find(item => item.id === row.agent) || {}
      const component = root.components.find(item => item.id === row.component) || {}
      return (agent.name || row.agent) + " · " + (component.name || row.component)
    })
    return rows.length ? root.tr("Última reparación: {lista}", {lista: rows.join(", ")}) : ""
  }
  signal commandRequested(var arguments)
  signal pageRequested(string id)
  spacing: Style.space(6)

  // All the agents in one wrapping block, ordered by family so accounts of the same tool sit together.
  readonly property var orderedAgents: {
    const order = ["claude", "codex", "pi"]
    const rank = agent => { const i = order.indexOf(String(agent.id).replace(/-\d+$/, "")); return i === -1 ? order.length : i }
    return root.agents.map((agent, index) => ({agent: agent, index: index}))
      .sort((a, b) => rank(a.agent) - rank(b.agent) || a.index - b.index).map(item => item.agent)
  }

  Ui.PanelSectionHeader { text: root.tr("AGENTES") }
  Flow {
    Layout.fillWidth: true
    spacing: root.metrics.xxs
    Repeater {
      model: root.orderedAgents
      delegate: Ui.Button {
        required property var modelData
        objectName: "agent-row-" + modelData.id
        readonly property bool installed: modelData.installation === "installed"
        text: (installed ? "" : "○ ") + modelData.name
        tooltipText: root.setupStatus(modelData)
        foreground: installed ? Color.foreground : Color.muted
        bordered: true
        focusable: true
        fontSize: root.fonts.caption
        onClicked: root.pageRequested(modelData.id)
      }
    }
  }
  Text {
    objectName: "last-reconcile"
    Layout.fillWidth: true
    visible: text !== ""
    text: root.repairedText()
    textFormat: Text.PlainText
    color: Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
  }
  Ui.PanelSeparator { Layout.fillWidth: true }
  Ui.PanelSectionHeader { text: root.tr("GENERAL") }
  Text {
    objectName: "language-label"
    text: root.tr("Idioma")
    textFormat: Text.PlainText
    color: Color.foreground
    font.family: root.fonts.family
    font.pixelSize: root.fonts.body
  }
  Ui.ButtonGroup {
    objectName: "language-selector"
    spacing: root.metrics.xxs
    fontSize: root.fonts.caption
    options: [{value: "system", label: root.tr("Sistema")},
              {value: "es", label: root.tr("Español")}, {value: "en", label: root.tr("English")}]
    value: root.languagePreference
    enabled: !root.busy
    onChanged: value => root.commandRequested(["set-language", value])
  }
  CompactRow {
    objectName: "team-row"
    Layout.fillWidth: true
    label: root.tr("Equipo")
    status: root.team.enabled ? root.tr("{conectados}/{seleccionados} conectados", {conectados: root.team.connectedCount || 0, seleccionados: root.team.selectedCount || 0}) : root.tr("Desactivado")
    checked: !!root.team.enabled
    busy: root.busy
    onOpened: root.pageRequested("team")
    onToggled: root.commandRequested(["team-all", root.team.enabled ? "disable" : "enable"])
  }
  CompactRow {
    objectName: "context-row"
    Layout.fillWidth: true
    label: root.tr("Contexto")
    status: root.tr("Sesiones nuevas")
    switchVisible: false
    onOpened: root.pageRequested("context")
  }
  CompactRow {
    objectName: "prices-row"
    Layout.fillWidth: true
    label: root.tr("Precios")
    status: (root.prices.missing || 0) > 0 ? root.tr("{cantidad} sin precio", {cantidad: root.prices.missing}) : root.tr("Completos")
    switchVisible: false
    onOpened: root.pageRequested("prices")
  }
  CompactRow {
    objectName: "launcher-menu"
    Layout.fillWidth: true
    navigationEnabled: false
    label: root.tr("Accesos en el menú")
    checked: !!(root.launcher.menu || {}).enabled
    busy: root.busy
    onToggled: root.commandRequested(["launcher-menu", (root.launcher.menu || {}).enabled ? "false" : "true"])
  }
  Ui.Button {
    text: root.tr("Aplicar menú")
    visible: (root.launcher.menu || {}).verified === false
    focusable: true
    enabled: !root.busy
    onClicked: root.commandRequested(["launcher-menu", (root.launcher.menu || {}).enabled ? "true" : "false"])
  }
  Ui.Button {
    text: root.busy ? root.tr("Procesando…") : root.tr("Actualizar detección")
    focusable: true
    enabled: !root.busy
    onClicked: root.commandRequested(["status"])
  }
  Ui.Button {
    objectName: "ai-check"
    text: root.tr("Comprobar con IA")
    focusable: true
    enabled: !root.busy && root.agents.some(agent => agent.enabled && agent.installation === "installed")
    onClicked: root.commandRequested(["ai-check"])
  }
  Text {
    Layout.fillWidth: true
    visible: !!root.aiCheck.checkedAt
    text: root.tr("Última comprobación: {fecha}", {fecha: root.aiCheck.checkedAt || ""})
    textFormat: Text.PlainText
    color: Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
  }
}
