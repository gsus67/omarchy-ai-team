import QtQuick
import "I18n.js" as I18n
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons

ColumnLayout {
  id: root
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  property var info: ({})
  property var operation: ({})
  property bool busy: false
  signal commandRequested(var arguments)
  signal restoreRequested(var arguments, string explanation)
  spacing: Style.space(6)

  Ui.Button {
    objectName: "update-memory"
    text: root.tr("Actualizar ECC")
    visible: !!(root.info.runtime || {}).installed
    focusable: true
    enabled: !root.busy && !!(root.info.runtime || {}).installed
    onClicked: root.commandRequested(["runtime-update", "memory"])
  }
  MemorySettings {
    translations: root.translations
    info: root.info
    busy: root.busy
    onCommandRequested: arguments => root.commandRequested(arguments)
  }
  MemoryData {
    translations: root.translations
    info: root.info
    operation: root.operation
    busy: root.busy
    onCommandRequested: arguments => root.commandRequested(arguments)
    onRestoreRequested: (arguments, explanation) => root.restoreRequested(arguments, explanation)
  }
}
