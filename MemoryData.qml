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
  property var info: ({})
  property var operation: ({})
  property bool busy: false
  readonly property var fonts: Style.font
  readonly property var preview: root.operation.restorePreview || ({})
  signal commandRequested(var arguments)
  signal restoreRequested(var arguments, string explanation)
  Layout.fillWidth: true
  spacing: Style.space(6)

  CompactRow {
    Layout.fillWidth: true
    label: root.tr("Última copia")
    status: (root.info.backupState || {}).lastSuccess ? new Date(root.info.backupState.lastSuccess * 1000).toLocaleString() : root.tr("Sin copia verificada")
    switchVisible: false
    navigationEnabled: false
  }
  Repeater {
    model: [{label:root.tr("Último intento"),error:(root.info.backupState || {}).lastError}, {label:root.tr("Retención"),error:(root.info.backupState || {}).retentionError}, {label:root.tr("Gestión"),error:root.info.managementError}].filter(row => !!row.error)
    delegate: CompactRow {
      required property var modelData
      Layout.fillWidth: true
      label: modelData.label
      status: modelData.error
      statusColor: Commons.Color.urgent
      switchVisible: false
      navigationEnabled: false
    }
  }
  Ui.PanelSectionHeader { text: root.tr("EXPORTAR") }
  RoomyDropdown {
    id: scope
    objectName: "memory-export-project"
    Layout.fillWidth: true
    label: root.tr("Proyectos")
    value: "all"
    options: [{value:"all",label:root.tr("Todos los proyectos")}].concat((root.info.projects || []).map(project => ({value:project.key,label:project.memoryCount === 1 ? root.tr("{proyecto} · {cantidad} memoria", {proyecto: project.label, cantidad: project.memoryCount}) : root.tr("{proyecto} · {cantidad} memorias", {proyecto: project.label, cantidad: project.memoryCount})})))
    enabled: !root.busy
  }
  RowLayout {
    Ui.Button {
      objectName: "memory-export"
      text: root.tr("Exportar")
      enabled: !root.busy && !!(root.info.runtime || {}).installed && !!(root.info.backupSettings || {}).destination && (root.info.projects || []).length > 0
      onClicked: root.commandRequested(["memory-export","--project",scope.value])
    }
    Ui.Button {
      objectName: "memory-backup-now"
      text: root.tr("Respaldar ahora")
      enabled: !root.busy && !!(root.info.runtime || {}).installed && !!(root.info.backupSettings || {}).destination && (root.info.projects || []).length > 0
      onClicked: root.commandRequested(["memory-backup"])
    }
  }
  Ui.PanelSectionHeader { text: root.tr("RESTAURAR") }
  Ui.TextField {
    id: archive
    objectName: "memory-archive"
    Layout.fillWidth: true
    placeholderText: root.tr("Archivo ZIP de memoria")
    enabled: !root.busy
  }
  Ui.TextField {
    id: project
    objectName: "memory-target-project"
    Layout.fillWidth: true
    placeholderText: root.tr("Opcional · nueva carpeta del proyecto")
    enabled: !root.busy
  }
  Ui.Button {
    objectName: "memory-preview"
    text: root.tr("Revisar archivo")
    enabled: !root.busy && archive.text.length > 0 && !!(root.info.runtime || {}).installed
    onClicked: root.commandRequested(["memory-preview",archive.text,"--project-path",project.text])
  }
  CompactRow {
    Layout.fillWidth: true
    visible: !!root.preview.token
    label: root.tr("Vista previa")
    status: root.tr("{proyectos} proyectos · {nuevas} nuevas", {proyectos: root.preview.projectCount || 0, nuevas: root.preview.newCount || 0})
    switchVisible: false
    navigationEnabled: false
  }
  CompactRow {
    Layout.fillWidth: true
    visible: !!root.preview.token
    label: root.tr("Existentes")
    status: root.tr("{conflictos} conflictos · {identicas} idénticas", {conflictos: root.preview.conflictCount || 0, identicas: root.preview.identicalCount || 0})
    statusColor: root.preview.conflictCount > 0 ? Commons.Color.urgent : Commons.Color.muted
    switchVisible: false
    navigationEnabled: false
  }
  Ui.Button {
    objectName: "memory-restore"
    text: root.tr("Restaurar nuevas…")
    visible: !!root.preview.token
    enabled: !root.busy && !!root.preview.token && root.preview.conflictCount === 0 && root.preview.newCount > 0 && root.preview.requestedArchive === archive.text && root.preview.projectPath === project.text
    onClicked: root.restoreRequested(["memory-restore",archive.text,root.preview.token,"--project-path",project.text],
      root.tr("Añadir {cantidad} memorias en {proyectos} proyectos. Se conservarán los documentos existentes.", {cantidad: root.preview.newCount, proyectos: root.preview.projectCount}) +
      (project.text ? root.tr("\nDestino: {destino}", {destino: project.text}) : root.tr("\nProyectos originales del archivo.")))
  }
  Text {
    Layout.fillWidth: true
    text: root.tr("Sin cuentas; los conflictos bloquean la restauración.")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    elide: Text.ElideRight
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
  }
}
