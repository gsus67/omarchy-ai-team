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
  readonly property var metrics: Style.spacing
  property string goal: "tokens"
  property var toolIds: []
  property var info: ({rows: [], settings: {}, projectOptions: []})
  property bool busy: false
  property bool advanced: false
  property string scope: "default"
  property string expandedTool: ""
  readonly property var fonts: Style.font
  readonly property var rows: (root.info.rows || []).filter(row => root.toolIds.includes(row.id))
  signal commandRequested(var arguments)
  signal uninstallRequested(string toolId, var items)
  spacing: Style.space(6)
  onGoalChanged: {
    root.advanced = false
    root.scope = "default"
    root.expandedTool = ""
  }

  function selected(row) {
    const settings = root.info.settings || ({})
    const choices = root.scope === "default" ? settings.defaults || ({}) : (settings.projects || {})[root.scope] || ({})
    return choices[row.id] || (root.scope === "default" ? "auto" : "inherit")
  }

  Repeater {
    model: root.rows
    delegate: ColumnLayout {
      id: tool
      required property var modelData
      Layout.fillWidth: true
      spacing: 0

      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(8)

        CompactRow {
          objectName: "tool-" + tool.modelData.id
          Layout.fillWidth: true
          label: tool.modelData.name
          status: tool.modelData.installation !== "installed" ? (tool.modelData.optional ? root.tr("Opcional") : root.tr("Falta")) : tool.modelData.status || (tool.modelData.probeVerified ? root.tr("Listo") : root.tr("Pendiente"))
          switchVisible: false
          navigationEnabled: !!tool.modelData.brief
          onOpened: root.expandedTool = root.expandedTool === tool.modelData.id ? "" : tool.modelData.id
        }
        Ui.Button {
          readonly property bool installed: tool.modelData.installation === "installed"
          readonly property bool uninstallable: installed && (tool.modelData.uninstallItems || []).length > 0
          objectName: (installed ? "uninstall-tool-" : "install-tool-") + tool.modelData.id
          visible: installed || !!tool.modelData.installSupported
          text: installed ? root.tr("Desinstalar") : root.tr("Instalar")
          // Tools installed elsewhere keep the button, dimmed, so every row reads the same.
          opacity: installed && !uninstallable ? 0.4 : 1
          focusable: !installed || uninstallable
          enabled: !root.busy && (!installed || uninstallable)
          onClicked: {
            if (uninstallable) root.uninstallRequested(tool.modelData.id, tool.modelData.uninstallItems)
            else root.commandRequested(["catalog-preference", tool.modelData.id, "on", "--scope", "default"])
          }
        }
      }
      Text {
        objectName: "tool-brief-" + tool.modelData.id
        Layout.fillWidth: true
        Layout.leftMargin: Style.space(10)
        visible: root.expandedTool === tool.modelData.id
        text: [tool.modelData.brief || "", tool.modelData.ownershipDetail || ""].filter(value => value).join("\n")
        textFormat: Text.PlainText
        color: Commons.Color.muted
        font.family: root.fonts.family
        font.pixelSize: root.fonts.caption
        wrapMode: Text.WordWrap
      }
    }
  }

  Ui.Button {
    objectName: "advanced-" + root.goal
    text: root.advanced ? root.tr("Ocultar avanzado") : root.tr("Avanzado")
    focusable: true
    onClicked: root.advanced = !root.advanced
  }
  ColumnLayout {
    visible: root.advanced
    Layout.fillWidth: true
    spacing: Style.space(6)

    RoomyDropdown {
      objectName: "local-tools-scope"
      Layout.fillWidth: true
      label: root.tr("Ámbito")
      options: root.info.projectOptions || [{value:"default",label:root.tr("Preferencias generales")}]
      value: root.scope
      enabled: !root.busy
      onChanged: value => root.scope = value
    }
    Repeater {
      model: root.rows
      delegate: ColumnLayout {
        id: preference
        required property var modelData
        Layout.fillWidth: true
        spacing: Style.space(6)

        CompactRow {
          Layout.fillWidth: true
          label: preference.modelData.name
          switchVisible: false
          navigationEnabled: false
        }
        Ui.ButtonGroup {
          objectName: "local-tool-" + preference.modelData.id
          spacing: root.metrics.xxs
          fontSize: root.fonts.caption
          options: (root.scope === "default" ? [] : [{value:"inherit",label:root.tr("Heredar")}]).concat([{value:"auto",label:root.tr("Automático")},{value:"on",label:root.tr("Activada")},{value:"off",label:root.tr("Desactivada")}])
          value: root.selected(preference.modelData)
          enabled: !root.busy
          onChanged: value => root.commandRequested(["catalog-preference", preference.modelData.id, value, "--scope", root.scope])
        }
        Ui.Button {
          objectName: "probe-tool-" + preference.modelData.id
          visible: preference.modelData.installation === "installed"
          text: preference.modelData.probeVerified ? root.tr("Volver a comprobar") : root.tr("Comprobar / reintentar")
          focusable: true
          enabled: !root.busy && preference.modelData.installation === "installed"
          onClicked: root.commandRequested(["catalog-probe", preference.modelData.id])
        }
      }
    }
  }
  Text {
    Layout.fillWidth: true
    text: root.tr("Indicación para los agentes; no bloquea otras herramientas.")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    elide: Text.ElideRight
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
  }
}
