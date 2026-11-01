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
  property var agent: ({})
  property var components: []
  property var pi: ({})
  property bool busy: false
  readonly property var fonts: Style.font
  readonly property bool installed: root.agent.installation === "installed"
  readonly property bool cliReady: root.installed || (root.agent.installation === "missing-profile" && !!root.agent.cliInstalled)
  readonly property bool canUninstall: root.installed && root.agent.manager === "mise" && ["claude", "codex", "pi"].indexOf(root.agent.id) !== -1
  signal commandRequested(var arguments)
  signal uninstallRequested(var arguments, string explanation)
  signal authorizationRequested(var arguments, string explanation)
  spacing: Style.space(6)

  Ui.PanelSectionHeader { text: root.agent.name || root.tr("AGENTE") }
  CompactRow {
    Layout.fillWidth: true
    label: root.tr("CLI")
    status: root.agent.installation === "missing-profile" ? root.tr("Perfil sin crear · inicia sesión para crearlo") : root.installed ? root.agent.version || root.tr("Instalado") : root.tr("No instalado")
    switchVisible: false
    navigationEnabled: false
  }
  Repeater {
    model: root.components.filter(component => component.integration === "native")
    delegate: CompactRow {
      required property var modelData
      readonly property var target: (modelData.targets || []).find(item => item.agent === root.agent.id) || ({})
      Layout.fillWidth: true
      label: modelData.name
      status: target.enabled && target.verified ? root.tr("Conectado") : root.agent.enabled && modelData.enabled ? root.tr("Pendiente") : root.tr("Desactivado")
      statusColor: target.enabled && target.verified ? Commons.Color.foreground : Commons.Color.muted
      switchVisible: false
      navigationEnabled: false
    }
  }
  RowLayout {
    spacing: root.metrics.xxs
    Ui.Button {
      objectName: "authenticate-" + root.agent.id
      text: root.tr("Iniciar sesión")
      visible: root.cliReady && (root.agent.id !== "pi" || root.agent.enabled)
      focusable: true
      enabled: !root.busy
      onClicked: root.commandRequested(root.agent.id === "pi" ? ["pi-auth"] : ["authenticate-profile", root.agent.id])
    }
    Ui.Button {
      objectName: "install-agent-" + root.agent.id
      text: root.tr("Instalar CLI")
      visible: !root.cliReady
      focusable: true
      enabled: !root.busy
      onClicked: root.commandRequested(["agent-install", root.agent.id])
    }
    Ui.Button {
      objectName: "uninstall-agent-" + root.agent.id
      text: root.tr("Desinstalar CLI")
      visible: root.canUninstall
      focusable: true
      enabled: !root.busy
      onClicked: root.uninstallRequested(["agent-uninstall", root.agent.id], root.tr("Se quitará solo la CLI de {nombre}. Tus cuentas, sesiones y preferencias se conservan.", {nombre: root.agent.name}))
    }
    Ui.Button {
      objectName: "launch-agent-" + root.agent.id
      text: root.tr("Abrir")
      visible: root.agent.enabled && root.installed
      focusable: true
      enabled: !root.busy
      onClicked: root.commandRequested(["launch", root.agent.id])
    }
    Ui.Button {
      objectName: "update-agent-" + root.agent.id
      visible: root.installed
      text: root.agent.manager === "mise" ? root.tr("Actualizar CLI") : root.tr("Gestor externo")
      focusable: true
      enabled: !root.busy && root.agent.manager === "mise"
      onClicked: root.commandRequested(["agent-update", root.agent.id])
    }
  }
  Repeater {
    model: root.agent.id === "pi" ? [root.pi] : []
    delegate: PiProviders {
      translations: root.translations
      required property var modelData
      Layout.fillWidth: true
      info: modelData
      busy: root.busy
      selected: !!root.agent.enabled
      onCommandRequested: arguments => root.commandRequested(arguments)
      onAuthorizationRequested: (arguments, explanation) => root.authorizationRequested(arguments, explanation)
    }
  }
}
