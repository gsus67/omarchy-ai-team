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
  readonly property var providers: ({claude: "Claude", codex: "Codex", pi: "Pi"})
  function providerRank(agent) { const i = Object.keys(root.providers).indexOf(agent); return i === -1 ? 99 : i }
  function providerName(agent) { return root.providers[agent] || agent }
  readonly property real effortColumn: Style.space(98)
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  property var info: ({})
  property var agents: []
  property string defaultAgent: ""
  property bool busy: false
  // The Chief row picks the CLI that "Abrir predeterminado" and the right click open.
  readonly property var chiefOptions: [{value: "none", label: root.tr("Ninguno")}].concat(
    root.agents.filter(agent => agent.enabled).map(agent => ({value: agent.id, label: agent.name})))
  readonly property var fonts: Style.font
  readonly property var metrics: Style.spacing
  signal commandRequested(var arguments)
  spacing: Style.space(6)

  Ui.PanelSectionHeader { text: root.tr("EQUIPO") }
  Flow {
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
      model: [{text: root.tr("Papel"), width: root.labelColumn}, {text: root.tr("Proveedor"), width: -1}, {text: root.tr("Esfuerzo"), width: root.effortColumn}]
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
    delegate: ColumnLayout {
      id: role
      required property var modelData
      readonly property var current: (root.info.roles || {})[role.modelData.id] || ({})
      readonly property var recommended: role.current.recommended || ({})
      readonly property var choice: (root.info.options || []).find(option => option.agent === role.current.agent && option.model === role.current.model) || ({})
      // Two short lists instead of one long one: the provider first, then only its models in alphabetical order.
      readonly property var providerOptions: Array.from(new Set((root.info.options || []).map(option => option.agent)))
        .sort((a, b) => root.providerRank(a) - root.providerRank(b)).map(agent => ({value: agent, label: root.providerName(agent)}))
      readonly property var providerModels: (root.info.options || []).filter(option => option.agent === role.current.agent).slice()
        .sort((a, b) => a.model.localeCompare(b.model, undefined, {numeric: true, sensitivity: "base"}))
      readonly property var modelOptions: role.providerModels.map(option => ({
        value: JSON.stringify([option.agent, option.model]),
        label: (option.agent === role.recommended.agent && option.model === role.recommended.model ? "★ " : "") + option.model
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

      RowLayout {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: Style.space(4)
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
          objectName: "team-provider-" + role.modelData.id
          Layout.fillWidth: true
          Layout.minimumWidth: 0
          Layout.alignment: Qt.AlignTop
          options: role.providerOptions
          value: role.current.agent || ""
          enabled: !root.busy && options.length > 1
          onChanged: value => {
            // The provider's recommended model when it has one, otherwise its first one alphabetically.
            const models = (root.info.options || []).filter(option => option.agent === value)
              .sort((a, b) => a.model.localeCompare(b.model, undefined, {numeric: true, sensitivity: "base"}))
            const target = models.find(option => option.agent === role.recommended.agent && option.model === role.recommended.model) || models[0]
            if (target) role.choose(target.agent, target.model, (target.recommendedEfforts || {})[role.modelData.id] || "")
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
      RowLayout {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: Style.space(4)
        Item { Layout.preferredWidth: root.labelColumn }
        RoomyDropdown {
          objectName: "team-role-" + role.modelData.id
          Layout.fillWidth: true
          Layout.minimumWidth: 0
          options: role.modelOptions
          value: role.current.model ? JSON.stringify([role.current.agent, role.current.model]) : ""
          enabled: !root.busy && options.length > 0
          onChanged: value => {
            const selected = JSON.parse(value)
            role.choose(selected[0], selected[1], role.current.effort || "")
          }
        }
      }
      Ui.PanelSeparator {
        Layout.fillWidth: true
        strength: 0.08
      }
    }
  }
  // Below the roles on purpose: above them it pushes the effort popups past the window edge.
  Text {
    objectName: "team-label-jefe"
    text: root.tr("Jefe")
    textFormat: Text.PlainText
    color: Commons.Color.foreground
    font.family: root.fonts.family
    font.pixelSize: root.fonts.body
  }
  WrapButtonGroup {
    objectName: "default-agent"
    spacing: root.metrics.xxs
    fontSize: root.fonts.caption
    options: root.chiefOptions
    value: root.defaultAgent || "none"
    enabled: !root.busy
    onChanged: value => root.commandRequested(["set-default-agent", value])
  }
  Text {
    Layout.fillWidth: true
    text: root.tr("El Jefe es la CLI que abre el botón Abrir predeterminado.")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
  }
  Text {
    text: root.tr("★ Recomendado")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
  }
  Text {
    objectName: "usage-guard-pending"
    Layout.fillWidth: true
    visible: (root.info.usageGuardPending || []).length > 0
    text: root.tr("Falta aprobar el aviso de consumo en {perfiles}: abre Codex con ese perfil, escribe /hooks y pulsa t.", {perfiles: (root.info.usageGuardPending || []).join(", ")})
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
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
