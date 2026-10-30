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
  property bool busy: false
  readonly property var defaultOptions: {
    var options = [{value: "none", label: root.tr("Ninguno")}]
    for (const agent of root.agents) {
      if (agent.enabled)
        options.push({value: agent.id, label: agent.name})
    }
    return options
  }
  signal commandRequested(var arguments)
  signal pageRequested(string id)
  spacing: Style.space(6)

  Ui.PanelSectionHeader { text: root.tr("AGENTES") }
  Repeater {
    model: root.agents
    delegate: CompactRow {
      required property var modelData
      objectName: "agent-row-" + modelData.id
      Layout.fillWidth: true
      label: modelData.name
      status: modelData.installation === "installed" ? modelData.version || root.tr("Instalado") : root.tr("No instalado")
      switchVisible: false
      onOpened: root.pageRequested(modelData.id)
    }
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
    Layout.fillWidth: true
    label: root.tr("Abrir con")
    switchVisible: false
    navigationEnabled: false
  }
  Ui.ButtonGroup {
    objectName: "default-agent"
    spacing: root.metrics.xxs
    fontSize: root.fonts.caption
    options: root.defaultOptions
    value: root.launcher.defaultAgent || "none"
    enabled: !root.busy
    onChanged: value => root.commandRequested(["set-default-agent", value])
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
  Repeater {
    model: root.aiCheck.results || []
    delegate: ColumnLayout {
      id: result
      required property var modelData
      Layout.fillWidth: true
      spacing: root.metrics.xxs
      CompactRow {
        objectName: "ai-check-" + result.modelData.agent
        Layout.fillWidth: true
        label: result.modelData.name
        status: result.modelData.skipped ? root.tr("Omitido") : result.modelData.ok ? root.tr("Funciona") : root.tr("Fallos")
        switchVisible: false
        navigationEnabled: false
      }
      Text {
        Layout.fillWidth: true
        visible: text !== ""
        text: (result.modelData.failures || []).concat(result.modelData.warnings || []).join("\n")
        textFormat: Text.PlainText
        color: Color.muted
        font.family: root.fonts.family
        font.pixelSize: root.fonts.caption
        wrapMode: Text.WordWrap
      }
      Text {
        Layout.fillWidth: true
        visible: text !== ""
        text: (result.modelData.tokens !== undefined ? [root.tr("Tokens: {cantidad}", {cantidad: result.modelData.tokens})] : [])
          .concat(result.modelData.cost !== undefined ? [root.tr("Coste (USD): {coste}", {coste: result.modelData.cost})] : []).join(" · ")
        textFormat: Text.PlainText
        color: Color.muted
        font.family: root.fonts.family
        font.pixelSize: root.fonts.caption
        wrapMode: Text.WordWrap
      }
    }
  }
}
