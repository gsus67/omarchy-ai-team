import QtQuick
import "I18n.js" as I18n
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons

ColumnLayout {
  id: root
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  readonly property var metrics: Style.spacing
  property var info: ({})
  property bool busy: false
  readonly property var fonts: Style.font
  readonly property var settings: root.info.backupSettings || ({destination:"",hours:24,keep:7,enabled:false})
  signal commandRequested(var arguments)
  Layout.fillWidth: true
  spacing: Style.space(6)

  Ui.PanelSectionHeader { text: root.tr("MEMORIA") }
  CompactRow {
    Layout.fillWidth: true
    label: "ECC Memory Vault"
    status: (root.info.runtime || {}).version || root.tr("No instalado")
    switchVisible: false
    navigationEnabled: false
  }
  CompactRow {
    Layout.fillWidth: true
    label: root.tr("Almacén")
    status: root.info.path || root.tr("Pendiente")
    switchVisible: false
    navigationEnabled: false
  }
  CompactRow {
    Layout.fillWidth: true
    label: root.tr("Proyectos")
    status: String((root.info.projects || []).length)
    switchVisible: false
    navigationEnabled: false
  }
  Ui.Button {
    objectName: "memory-doctor"
    text: root.tr("Comprobar almacén")
    visible: !!(root.info.runtime || {}).installed
    enabled: !root.busy && !!(root.info.runtime || {}).installed
    onClicked: root.commandRequested(["memory-doctor"])
  }
  Ui.PanelSectionHeader { text: root.tr("COPIAS") }
  Ui.TextField {
    id: destination
    objectName: "memory-backup-destination"
    Layout.fillWidth: true
    text: root.settings.destination
    placeholderText: root.tr("Destino · carpeta local, externa o SMB")
    enabled: !root.busy
  }
  Ui.ButtonGroup {
    id: frequency
    objectName: "memory-backup-frequency"
    spacing: root.metrics.xxs
    options: [{value:"1",label:root.tr("1 h")},{value:"6",label:root.tr("6 h")},{value:"12",label:root.tr("12 h")},{value:"24",label:root.tr("Diaria")},{value:"168",label:root.tr("Semanal")}]
    value: String(root.settings.hours)
    enabled: !root.busy
    onChanged: value => frequency.value = value
  }
  Ui.NumberField {
    id: keep
    objectName: "memory-backup-keep"
    label: root.tr("Copias que conservar")
    from: 1
    to: 365
    value: root.settings.keep
    enabled: !root.busy
    onModified: value => keep.value = value
  }
  Ui.Button {
    objectName: "memory-config-save"
    text: root.tr("Guardar copias")
    enabled: !root.busy && destination.text.length > 0
    onClicked: root.commandRequested(["memory-configure",destination.text,"--hours",frequency.value,"--keep",String(keep.value)])
  }
  CompactRow {
    navigationEnabled: false
    objectName: "memory-backup-auto"
    Layout.fillWidth: true
    label: root.tr("Copias automáticas")
    status: (root.info.timer || {}).detail || (root.settings.enabled ? root.tr("Activadas") : root.tr("Desactivadas"))
    checked: root.settings.enabled
    busy: root.busy
    enabled: !root.busy && !!root.settings.destination
    onToggled: root.commandRequested(["memory-schedule",root.settings.enabled ? "false" : "true"])
  }
  Ui.Button {
    text: root.tr("Reintentar programación")
    visible: (root.info.timer || {}).prepared === false
    enabled: !root.busy && !!root.settings.destination
    onClicked: root.commandRequested(["memory-schedule",root.settings.enabled ? "true" : "false"])
  }
}
