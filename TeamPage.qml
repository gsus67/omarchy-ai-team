pragma ComponentBehavior: Bound
import QtQuick
import "I18n.js" as I18n
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons
import qs.Commons as Commons

ColumnLayout {
  id: root
  readonly property real labelColumn: Style.space(74)
  readonly property real effortColumn: Style.space(98)
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  property var info: ({})
  property var agents: []
  property bool busy: false
  readonly property var fonts: Style.font
  readonly property var metrics: Style.spacing
  signal commandRequested(var arguments)
  spacing: Style.space(6)

  Ui.PanelSectionHeader { text: root.tr("EQUIPO") }
  RowLayout {
    Layout.fillWidth: true
    spacing: root.metrics.xxs
    Ui.Button {
      objectName: "team-recommended"
      text: root.tr("Usar recomendados")
      focusable: true
      enabled: !root.busy
      onClicked: root.commandRequested(["team-recommend"])
    }
    Ui.Button {
      objectName: "team-models"
      text: root.tr("Actualizar")
      focusable: true
      enabled: !root.busy
      onClicked: root.commandRequested(["team-models"])
    }
  }
  Text {
    objectName: "team-warning"
    Layout.fillWidth: true
    visible: text !== ""
    text: (root.info.warnings || []).join("\n")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
  }

  RowLayout {
    Layout.fillWidth: true
    spacing: Style.space(4)
    Repeater {
      model: [{text: root.tr("Papel"), width: root.labelColumn}, {text: root.tr("Modelo"), width: -1}, {text: root.tr("Esfuerzo"), width: root.effortColumn}]
      delegate: Text {
        required property var modelData
        Layout.fillWidth: modelData.width < 0
        Layout.preferredWidth: modelData.width
        text: modelData.text
        textFormat: Text.PlainText
        color: Commons.Color.muted
        font.family: root.fonts.family
        font.pixelSize: root.fonts.caption
      }
    }
  }
  Repeater {
    model: [{id: "plan", label: root.tr("Planificar")}, {id: "code", label: root.tr("Programar")},
      {id: "review", label: root.tr("Revisar")}, {id: "simple", label: root.tr("Sencillas")}]
    delegate: RowLayout {
      id: role
      required property var modelData
      readonly property var current: (root.info.roles || {})[role.modelData.id] || ({})
      readonly property var recommended: role.current.recommended || ({})
      readonly property var choice: (root.info.options || []).find(option => option.agent === role.current.agent && option.model === role.current.model) || ({})
      readonly property var modelOptions: (root.info.options || []).map(option => ({
        value: JSON.stringify([option.agent, option.model]),
        label: option.agent === role.recommended.agent && option.model === role.recommended.model ? root.tr("★ {modelo}", {modelo: option.model}) : option.model
      }))
      readonly property var effortOptions: (role.choice.efforts || []).map(effort => ({
        value: effort,
        label: effort === (role.choice.recommendedEfforts || {})[role.modelData.id] ? root.tr("★ {esfuerzo}", {esfuerzo: effort || root.tr("Sin ajuste")}) : effort || root.tr("Sin ajuste")
      }))
      Layout.fillWidth: true
      Layout.minimumWidth: 0
      spacing: Style.space(4)

      function choose(agent, model, effort) {
        root.commandRequested(["team-set", role.modelData.id, agent, model, "--effort", effort])
      }

      Text {
        objectName: "team-label-" + role.modelData.id
        Layout.preferredWidth: root.labelColumn
        Layout.preferredHeight: root.metrics.controlHeight
        Layout.alignment: Qt.AlignTop
        text: role.modelData.label
        textFormat: Text.PlainText
        color: Commons.Color.foreground
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        font.family: root.fonts.family
        font.pixelSize: root.fonts.body
      }
      RoomyDropdown {
        objectName: "team-role-" + role.modelData.id
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        Layout.alignment: Qt.AlignTop
        options: role.modelOptions
        value: role.current.model ? JSON.stringify([role.current.agent, role.current.model]) : ""
        enabled: !root.busy && options.length > 0
        onChanged: value => {
          const selected = JSON.parse(value)
          role.choose(selected[0], selected[1], role.current.effort || "")
        }
      }
      RoomyDropdown {
        objectName: "team-effort-" + role.modelData.id
        Layout.preferredWidth: root.effortColumn
        Layout.alignment: Qt.AlignTop
        options: role.effortOptions
        value: role.current.effort || ""
        enabled: !root.busy && options.length > 1
        onChanged: value => role.choose(role.current.agent, role.current.model, value)
      }
    }
  }
  Text {
    text: root.tr("★ Recomendado")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
  }
  RowLayout {
    Layout.fillWidth: true
    spacing: root.metrics.xxs
    Ui.NumberField {
      id: guardPercent
      objectName: "usage-guard-percent"
      Layout.fillWidth: true
      label: root.tr("Guardar y parar al (%)")
      from: 50
      to: 99
      value: (root.info.usageGuard || {}).percent || 90
      enabled: !root.busy
      onModified: value => root.commandRequested(["usage-guard", String(guardSwitch.checked), String(value)])
    }
    Ui.ToggleSwitch {
      id: guardSwitch
      objectName: "usage-guard-switch"
      checked: (root.info.usageGuard || {}).enabled !== false
      enabled: !root.busy
      onToggled: root.commandRequested(["usage-guard", String(!checked), String(guardPercent.value)])
    }
  }

  Ui.PanelSeparator { Layout.fillWidth: true }
  Repeater {
    model: (root.info.targets || []).filter(target => root.agents.some(agent => agent.id === target.agent && agent.enabled))
    delegate: CompactRow {
      required property var modelData
      objectName: "team-status-" + modelData.agent
      Layout.fillWidth: true
      label: modelData.name
      status: modelData.enabled && modelData.verified ? root.tr("Conectado") : root.info.enabled || modelData.enabled ? root.tr("Pendiente") : root.tr("Desactivado")
      statusColor: modelData.enabled && modelData.verified ? Commons.Color.foreground : Commons.Color.muted
      switchVisible: false
      navigationEnabled: false
    }
  }
  Text {
    Layout.fillWidth: true
    text: root.tr("Los cambios se cargan en sesiones nuevas.")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
  }
}
