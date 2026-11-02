import QtQuick
import "I18n.js" as I18n
import Quickshell
import Quickshell.Io
import qs.Ui as Ui
import qs.Commons

Ui.Panel {
  id: root
  readonly property var translations: root.snapshot.translations || ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  moduleName: "gsus67.ai-team"
  manageIpc: false
  property var shell: null
  property var manifest: ({ id: "gsus67.ai-team" })
  property var snapshot: ({ agents: [], components: [], deferred: [] })
  property string errorText: ""
  property string message: ""
  property bool launchRequested: false
  property bool reconnectAttempted: false
  property bool reconnectPending: false
  property var operation: ({})
  property alias page: content.page
  readonly property bool busy: backend.running
  implicitWidth: icon.implicitWidth
  implicitHeight: icon.implicitHeight

  onOpenedChanged: if (opened) run(["status"])
  // `omarchy plugin update` has no hook: notice a new version as soon as the plugin loads.
  Component.onCompleted: run(["status"])

  Ui.BarIconButton {
    id: icon
    objectName: "harness-icon"
    anchors.fill: parent
    bar: root.bar
    text: "󰀎"
    tooltipText: "AI Team"
    onPressed: button => {
      if (button === Qt.LeftButton) root.toggle()
      else if (button === Qt.RightButton) root.run(["launch-default"])
    }
  }

  function run(arguments) {
    if (backend.running) return
    messageTimer.restart()
    errorText = ""
    message = ""
    if (arguments[0] === "agent-update" || arguments[0] === "runtime-update") message = root.tr("Comprobando y actualizando mediante el gestor oficial…")
    else if (arguments[0] === "agent-install") message = root.tr("Instalando la CLI con el gestor oficial…")
    else if (arguments[0] === "agent-uninstall") message = root.tr("Desinstalando la CLI; tus cuentas y sesiones se conservan…")
    else if (arguments[0] === "set-agent" && arguments[2] === "true") message = root.tr("Preparando agente e integraciones; se instalará únicamente lo que falte…")
    launchRequested = arguments[0] === "launch-default"
    var script = decodeURIComponent(String(Qt.resolvedUrl("backend/harness.py")).replace(/^file:\/\//, ""))
    backend.command = ["python3", script].concat(arguments)
    backend.running = true
  }

  Timer {
    id: messageTimer
    interval: 4000
    repeat: false
    onTriggered: root.message = ""
  }

  Process {
    id: backend
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          const result = JSON.parse(text)
          if (result.data) root.snapshot = result.data
          if (result.data && backend.command[2] === "status" && root.snapshot.needsReconnect && !root.reconnectAttempted)
            root.reconnectPending = true
          root.operation = result.operation || ({})
          if (result.ok) {
            root.message = result.message || ""
          } else {
            root.errorText = result.error || root.tr("No se pudo completar la operación.")
            root.message = result.message || ""
            if (root.launchRequested) { root.page = ""; root.open() }
          }
          if (root.message !== "") messageTimer.restart()
        } catch (error) { root.errorText = root.tr("No se pudo leer la respuesta de AI Team.") }
      }
    }
    stderr: StdioCollector { }
    onExited: (exitCode, exitStatus) => {
      if (exitCode !== 0 && root.errorText === "") root.errorText = root.tr("La operación no pudo completarse.")
      // The new version is saved before this runs, so the restarted shell does not come back here.
      if (root.operation.shellRestart) {
        Quickshell.execDetached(["omarchy", "restart", "shell"])
        return
      }
      if (root.reconnectPending) {
        root.reconnectPending = false
        root.reconnectAttempted = true
        Qt.callLater(() => root.run(["after-update"]))
      }
    }
  }

  Ui.KeyboardPanel {
    id: panel
    objectName: "harnessPanel"
    anchorItem: icon
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: content
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(560))

    HarnessContent {
      translations: root.translations
      id: content
      objectName: "harnessContent"
      anchors.fill: parent
      snapshot: root.snapshot
      busy: root.busy
      errorText: root.errorText
      message: root.message
      operation: root.operation
      onCommandRequested: arguments => root.run(arguments)
      onRestoreRequested: (commandArgs, explanation) => {
        confirmation.pendingArguments = commandArgs
        confirmation.selectedIndex = 0
        confirmation.message = explanation
        confirmation.opened = true
        confirmation.forceActiveFocus()
      }
      onAuthorizationRequested: (commandArgs, explanation) => {
        authorization.pendingArguments = commandArgs
        authorization.selectedIndex = 0
        authorization.message = explanation
        authorization.opened = true
        authorization.forceActiveFocus()
      }
      onUninstallRequested: (commandArgs, explanation) => {
        toolUninstall.pendingArguments = commandArgs
        toolUninstall.selectedIndex = 0
        toolUninstall.message = explanation
        toolUninstall.opened = true
        toolUninstall.forceActiveFocus()
      }
      Keys.onEscapePressed: root.close()
    }
    Ui.ConfirmDialog {
      id: toolUninstall
      objectName: "tool-uninstall-confirm"
      property var pendingArguments: []
      anchors.fill: parent
      cancelText: root.tr("Cancelar")
      confirmText: root.tr("Desinstalar")
      selectedIndex: 0
      onCanceled: { opened = false; content.forceActiveFocus() }
      onConfirmed: { opened = false; root.run(toolUninstall.pendingArguments); content.forceActiveFocus() }
      Keys.onPressed: event => { if (toolUninstall.handleKey(event)) event.accepted = true }
    }
    // The mouse "back" button works like ‹ Volver; other buttons pass through.
    MouseArea {
      anchors.fill: content
      z: 100
      acceptedButtons: Qt.BackButton
      onPressed: content.goBack()
    }
    Ui.ConfirmDialog {
      id: authorization
      objectName: "provider-authorization-confirm"
      property var pendingArguments: []
      anchors.fill: parent
      cancelText: root.tr("Cancelar")
      confirmText: root.tr("Activar")
      selectedIndex: 0
      onCanceled: { opened = false; content.forceActiveFocus() }
      onConfirmed: { opened = false; root.run(authorization.pendingArguments); content.forceActiveFocus() }
      Keys.onPressed: event => { if (authorization.handleKey(event)) event.accepted = true }
    }
    Ui.ConfirmDialog {
      id: confirmation
      objectName: "memory-restore-confirm"
      property var pendingArguments: []
      anchors.fill: parent
      cancelText: root.tr("Cancelar")
      confirmText: root.tr("Restaurar")
      selectedIndex: 0
      onCanceled: { opened = false; content.forceActiveFocus() }
      onConfirmed: { opened = false; root.run(confirmation.pendingArguments); content.forceActiveFocus() }
      Keys.onPressed: event => { if (confirmation.handleKey(event)) event.accepted = true }
    }
  }
}
