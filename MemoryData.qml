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
  property var info: ({})
  property var operation: ({})
  property bool busy: false
  readonly property var fonts: Style.font
  readonly property var preview: root.operation.restorePreview || ({})
  // Archive project key → new folder, sorted so it compares with the folders the preview answered for.
  property var folders: ({})
  readonly property var movedFolders: {
    const moved = {}
    for (const key of Object.keys(root.folders).sort()) if (root.folders[key]) moved[key] = root.folders[key]
    return moved
  }
  readonly property var mapArguments: Object.keys(root.movedFolders).reduce((list, key) => list.concat(["--project-map", key + "=" + root.movedFolders[key]]), [])
  readonly property bool previewIsCurrent: root.preview.requestedArchive === archive.text && root.preview.projectPath === project.text
    && JSON.stringify(root.preview.projectMap || {}) === JSON.stringify(root.movedFolders)
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
    enabled: !root.busy && archive.text.length > 0
    onClicked: root.commandRequested(["memory-preview",archive.text,"--project-path",project.text].concat(root.mapArguments))
  }
  // Each project can move on its own; `compartido` has no folder, so it never asks for one.
  Repeater {
    model: (root.preview.projects || []).length > 1 ? root.preview.projects.filter(row => !row.shared) : []
    delegate: Ui.TextField {
      id: folderField
      required property var modelData
      objectName: "memory-map-" + folderField.modelData.key.slice(0, 8)
      Layout.fillWidth: true
      placeholderText: root.tr("{proyecto} · nueva carpeta", {proyecto: (folderField.modelData.name || root.tr("Proyecto")) + " · " + folderField.modelData.key.slice(0, 8)})
      enabled: !root.busy
      Component.onCompleted: folderField.text = root.folders[folderField.modelData.key] || ""
      onTextChanged: {
        if ((root.folders[folderField.modelData.key] || "") === folderField.text) return
        root.folders = Object.assign({}, root.folders, {[folderField.modelData.key]: folderField.text})
      }
    }
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
    enabled: !root.busy && !!root.preview.token && root.preview.conflictCount === 0 && root.preview.newCount > 0 && root.previewIsCurrent
    onClicked: root.restoreRequested(["memory-restore",archive.text,root.preview.token,"--project-path",project.text].concat(root.mapArguments),
      root.tr("Añadir {cantidad} memorias en {proyectos} proyectos. Se conservarán los documentos existentes.", {cantidad: root.preview.newCount, proyectos: root.preview.projectCount}) +
      (project.text ? root.tr("\nDestino: {destino}", {destino: project.text}) :
       root.mapArguments.length ? Object.keys(root.movedFolders).map(key => root.tr("\nDestino: {destino}", {destino: root.movedFolders[key]})).join("") :
       root.tr("\nProyectos originales del archivo.")))
  }
  Text {
    objectName: "memory-restore-note"
    Layout.fillWidth: true
    visible: !!root.preview.token
    text: root.tr("Las memorias siguen asociadas a su carpeta original salvo que indiques otra; restaurar no conecta ningún agente.")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    wrapMode: Text.WordWrap
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
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
