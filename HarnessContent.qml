pragma ComponentBehavior: Bound
import QtQuick
import "I18n.js" as I18n
import QtQuick.Controls as QQC
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons
import qs.Commons as Commons

ColumnLayout {
  id: root
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  property var snapshot: ({ agents: [], components: [], deferred: [] })
  property bool busy: false
  property var operation: ({})
  property string page: ""
  property string errorText: ""
  property string message: ""
  property bool teamModelsPending: false
  readonly property var fonts: Style.font
  readonly property var summary: root.snapshot.summary || ({text: root.tr("Consultando…"), page: "", state: "pending"})
  readonly property var launcher: root.snapshot.launcher || ({})
  readonly property var defaultAgent: (root.snapshot.agents || []).find(agent => agent.id === root.launcher.defaultAgent) || ({})
  readonly property string componentId: root.page === "quality" ? "superpowers" : root.page === "tokens" || root.page === "security" ? "local-tools" : root.page === "memory" ? "memory" : root.page === "team" ? "team" : ""
  readonly property var component: (root.snapshot.components || []).find(item => item.id === root.componentId) || ({})
  signal commandRequested(var arguments)
  signal restoreRequested(var arguments, string explanation)
  signal authorizationRequested(var arguments, string explanation)
  signal uninstallRequested(var arguments, string explanation)
  spacing: Style.space(6)
  onPageChanged: {
    root.scrollTop()
    root.teamModelsPending = root.page === "team"
    root.refreshTeamModels()
  }
  onBusyChanged: root.refreshTeamModels()

  function refreshTeamModels() {
    if (!root.teamModelsPending || root.busy) return
    root.teamModelsPending = false
    root.commandRequested(["team-models"])
  }

  function goBack() {
    if (root.page === "") return
    root.page = root.page === "team" || root.page === "context" || (root.snapshot.agents || []).some(agent => agent.id === root.page) ? "settings" : ""
  }

  function scrollTop() {
    Qt.callLater(() => {
      const flickable = scroll.contentItem as Flickable
      if (flickable) flickable.contentY = 0
    })
  }

  RowLayout {
    Layout.fillWidth: true
    spacing: Style.space(12)
    Text {
      text: "AI Team"
      textFormat: Text.PlainText
      color: Commons.Color.foreground
      font.family: Style.font.family
      font.pixelSize: Style.font.title
      font.bold: true
    }
    AgentSelector {
      translations: root.translations
      visible: root.page === ""
      Layout.fillWidth: true
      Layout.minimumWidth: 0
      agents: root.snapshot.agents || []
      busy: root.busy
      onSelectionRequested: ids => root.commandRequested(["set-agents", ids.length ? ids.join(",") : "none"])
    }
    Item {
      visible: root.page !== ""
      Layout.fillWidth: true
    }
    Ui.Button {
      objectName: "navigation-back"
      visible: root.page !== ""
      text: root.tr("‹ Volver")
      focusable: true
      onClicked: root.goBack()
    }
  }

  Text {
    id: statusLine
    objectName: "summary"
    Layout.fillWidth: true
    text: "● " + (root.errorText || root.message || root.snapshot.warning || root.summary.text)
    textFormat: Text.PlainText
    color: root.errorText || root.snapshot.warning || root.summary.state === "warning" ? Commons.Color.urgent : root.summary.state === "pending" ? Commons.Color.muted : Commons.Color.accent
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
    maximumLineCount: 2
    elide: Text.ElideRight
    wrapMode: Text.WordWrap
    activeFocusOnTab: true
    Keys.onReturnPressed: root.page = root.summary.page || ""
    Keys.onEnterPressed: root.page = root.summary.page || ""
    Keys.onSpacePressed: root.page = root.summary.page || ""
    MouseArea {
      anchors.fill: parent
      cursorShape: Qt.PointingHandCursor
      onClicked: root.page = root.summary.page || ""
    }
  }

  Flow {
    objectName: "usage-summary"
    Layout.fillWidth: true
    visible: root.page === "" && (root.snapshot.usage || []).length > 0
    spacing: Style.space(10)

    Text {
      text: root.tr("Uso 5 h · semana")
      textFormat: Text.PlainText
      color: Commons.Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.caption
    }

    Repeater {
      model: root.snapshot.usage || []
      delegate: Text {
        id: usageItem
        required property var modelData
        // Highlighted at the same 90 % the usage guard warns at.
        readonly property bool high: Math.max(modelData.five === null ? 0 : modelData.five, modelData.weekly === null ? 0 : modelData.weekly) >= 90
        text: modelData.name + " " + (modelData.five === null ? "—" : modelData.five + "%") + " · " + (modelData.weekly === null ? "—" : modelData.weekly + "%")
        textFormat: Text.PlainText
        color: usageItem.high ? Commons.Color.urgent : Commons.Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
    }
  }

  QQC.ScrollView {
    id: scroll
    objectName: "harness-scroll"
    Layout.fillWidth: true
    Layout.fillHeight: true
    Layout.minimumHeight: 0
    implicitHeight: pageColumn.implicitHeight
    clip: true
    QQC.ScrollBar.horizontal.policy: QQC.ScrollBar.AlwaysOff
    QQC.ScrollBar.vertical.policy: pageColumn.implicitHeight > height ? QQC.ScrollBar.AsNeeded : QQC.ScrollBar.AlwaysOff
    contentWidth: availableWidth

    ColumnLayout {
      id: pageColumn
      width: scroll.availableWidth
      spacing: Style.space(6)

      Text {
        objectName: "error-detail"
        Layout.fillWidth: true
        visible: statusLine.truncated && (root.errorText || root.snapshot.warning)
        text: root.errorText || root.snapshot.warning || ""
        textFormat: Text.PlainText
        color: Commons.Color.urgent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }

      ColumnLayout {
        visible: root.page === ""
        Layout.fillWidth: true
        spacing: Style.space(6)

        Repeater {
          model: root.snapshot.goals || []
          delegate: CompactRow {
            required property var modelData
            objectName: "goal-" + modelData.id
            Layout.fillWidth: true
            icon: ({quality: "󰄬", memory: "󰆼", tokens: "󰓅", security: "󰒃"})[modelData.id] || ""
            label: ({quality: root.tr("Calidad"), memory: root.tr("Memoria"), tokens: "Tokens", security: root.tr("Seguridad")})[modelData.id] || modelData.name
            status: modelData.line
            checked: modelData.enabled
            busy: root.busy
            onOpened: root.page = modelData.id
            onToggled: {
              const action = modelData.enabled ? "disable" : "enable"
              root.commandRequested(modelData.id === "quality" ? ["superpowers-all", action] : modelData.id === "memory" ? ["memory-all", action] : ["tool-group", modelData.id, action])
            }
          }
        }

        Ui.PanelSeparator { Layout.fillWidth: true }

        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(2)
          Ui.Button {
            objectName: "launch-default"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: root.defaultAgent.name ? root.tr("▶ Abrir {nombre}", {nombre: root.defaultAgent.name}) : root.tr("▶ Abrir predeterminado")
            focusable: true
            enabled: !root.busy && !!root.launcher.canLaunch
            onClicked: root.commandRequested(["launch-default"])
          }
          Ui.Button {
            objectName: "update-all"
            text: "⟳"
            tooltipText: root.tr("Actualizar todo")
            focusable: true
            enabled: !root.busy
            onClicked: root.commandRequested(["runtime-update", "core"])
          }
          Ui.Button {
            objectName: "open-settings"
            text: "⚙"
            tooltipText: root.tr("Ajustes")
            focusable: true
            onClicked: root.page = "settings"
          }
        }
      }

      Repeater {
        model: root.snapshot.agents || []
        delegate: AgentPage {
          required property var modelData
          visible: root.page === modelData.id
          Layout.fillWidth: true
          agent: modelData
          components: root.snapshot.components || []
          pi: root.snapshot.pi || ({})
          busy: root.busy
          onCommandRequested: arguments => root.commandRequested(arguments)
          onAuthorizationRequested: (arguments, explanation) => root.authorizationRequested(arguments, explanation)
          onUninstallRequested: (arguments, explanation) => root.uninstallRequested(arguments, explanation)
        }
      }

      Text {
        visible: root.page === "quality"
        Layout.fillWidth: true
        text: root.tr("Planificación, TDD, depuración y revisión.")
        textFormat: Text.PlainText
        color: Commons.Color.foreground
        elide: Text.ElideRight
        font.family: root.fonts.family
        font.pixelSize: root.fonts.body
      }

      Repeater {
        model: root.page === "quality" ? (root.component.targets || []).filter(target => (root.snapshot.agents || []).some(agent => agent.id === target.agent && agent.enabled)) : []
        delegate: CompactRow {
          required property var modelData
          Layout.fillWidth: true
          label: modelData.name
          status: (modelData.enabled && modelData.verified ? root.tr("Conectado") : root.component.enabled || modelData.enabled ? root.tr("Pendiente") : root.tr("Desactivado")) + (modelData.version ? " · " + modelData.version : "")
          statusColor: modelData.enabled && modelData.verified ? Commons.Color.foreground : Commons.Color.muted
          switchVisible: false
          navigationEnabled: false
        }
      }

      Ui.Button {
        objectName: "reconnect-" + root.componentId
        visible: root.componentId !== "" && (root.component.enabled ? root.component.connectedCount < root.component.selectedCount : root.component.activeCount > 0)
        text: root.component.enabled ? root.tr("Conectar pendientes") : root.tr("Reintentar desactivación")
        focusable: true
        enabled: !root.busy
        onClicked: root.commandRequested([root.componentId + "-all", root.component.enabled ? "enable" : "disable"])
      }

      Ui.Button {
        objectName: "quality-update"
        visible: root.page === "quality" && (root.component.targets || []).some(target => target.installed)
        text: root.tr("Actualizar")
        focusable: true
        enabled: !root.busy && (root.component.targets || []).some(target => target.installed)
        onClicked: root.commandRequested(["superpowers-all", "update"])
      }

      MemoryPage {
        translations: root.translations
        visible: root.page === "memory"
        Layout.fillWidth: true
        info: root.snapshot.memory || ({})
        operation: root.operation
        busy: root.busy
        onCommandRequested: arguments => root.commandRequested(arguments)
        onRestoreRequested: (arguments, explanation) => root.restoreRequested(arguments, explanation)
      }

      ToolGroupPage {
        translations: root.translations
        objectName: "local-catalog"
        visible: root.page === "tokens" || root.page === "security"
        Layout.fillWidth: true
        goal: root.page === "security" ? "security" : "tokens"
        toolIds: ((root.snapshot.localTools || {}).groups || {})[root.page === "security" ? "security" : "tokens"] || []
        info: root.snapshot.localTools || ({})
        busy: root.busy
        onCommandRequested: arguments => root.commandRequested(arguments)
        onUninstallRequested: (toolId, items) => root.uninstallRequested(["catalog-uninstall", toolId], root.tr("Se quitará: {herramientas}. Tus proyectos no cambian.", {herramientas: items.join(", ")}))
      }

      TeamPage {
        translations: root.translations
        visible: root.page === "team"
        Layout.fillWidth: true
        info: root.snapshot.team || ({})
        agents: root.snapshot.agents || []
        defaultAgent: root.launcher.defaultAgent || ""
        busy: root.busy
        onCommandRequested: arguments => root.commandRequested(arguments)
      }

      ContextPage {
        translations: root.translations
        visible: root.page === "context"
        Layout.fillWidth: true
        info: root.snapshot.context || ({})
        busy: root.busy
        onCommandRequested: arguments => root.commandRequested(arguments)
      }

      SettingsPage {
        languagePreference: root.snapshot.languagePreference || "system"
        translations: root.translations
        visible: root.page === "settings"
        Layout.fillWidth: true
        agents: root.snapshot.agents || []
        launcher: root.launcher
        team: root.snapshot.team || ({})
        aiCheck: root.snapshot.aiCheck || ({})
        busy: root.busy
        onCommandRequested: arguments => root.commandRequested(arguments)
        onPageRequested: id => root.page = id
      }
    }
  }
}
