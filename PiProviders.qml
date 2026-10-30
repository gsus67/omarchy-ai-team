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
  property var info: ({providers:{},checks:{},catalog:{}})
  property bool selected: false
  property bool busy: false
  property bool advanced: false
  readonly property var fonts: Style.font
  readonly property var providers: root.info.providers || ({})
  readonly property var catalog: root.info.catalog || ({})
  readonly property var xaiCheck: (root.info.checks || {}).xai || ({})
  readonly property var nousCheck: (root.info.checks || {}).nous || ({})
  readonly property var modelOptions: [{value:"",label:root.tr("Elegir modelo xAI")}].concat((root.catalog.models || []).filter(row => row.provider === "xai").map(row => ({value:row.id,label:row.label})))
  signal commandRequested(var arguments)
  signal authorizationRequested(var arguments, string explanation)
  spacing: Style.space(6)

  Ui.PanelSectionHeader { text: root.tr("PROVEEDORES") }
  RowLayout {
    Layout.fillWidth: true
    spacing: root.metrics.xxs
    CompactRow {
      id: xai
      objectName: "pi-provider-xai"
      Layout.fillWidth: true
      label: root.tr("xAI · pago")
      status: !root.providers.xai ? root.tr("Desactivado") : root.xaiCheck.remoteVerified ? root.tr("Conectado") : root.xaiCheck.ready ? root.tr("Conexión pendiente") : root.tr("Autenticación pendiente")
      checked: !!root.providers.xai
      busy: root.busy
      navigationEnabled: false
      enabled: !root.busy && root.selected
      onToggled: {
        if (root.providers.xai) root.commandRequested(["pi-provider","xai","false"])
        else root.authorizationRequested(["pi-provider","xai","true","--confirm-paid"], root.tr("Autorizar xAI como proveedor externo de pago para los modelos que elijas en Pi. Las consultas pueden consumir saldo de xAI. Esta acción solo configura la preferencia: no envía código, no inicia consultas ni omite permisos. La autenticación se completa en Pi."))
      }
    }
  }
  RowLayout {
    spacing: root.metrics.xxs
    Ui.Button {
      objectName: "pi-auth"
      text: root.tr("Iniciar sesión")
      visible: root.selected && (!!root.providers.xai || !!root.providers.nous)
      enabled: !root.busy
      onClicked: root.commandRequested(["pi-auth"])
    }
    Ui.Button {
      objectName: "pi-check-xai"
      text: root.tr("Comprobar xAI")
      visible: root.selected && !!root.providers.xai
      enabled: !root.busy
      onClicked: root.commandRequested(["pi-provider-check","xai"])
    }
    Ui.Button {
      objectName: "pi-probe-xai"
      text: root.tr("Probar · pago")
      visible: root.selected && !!root.providers.xai && !!root.info.model && !root.catalog.stale
      enabled: !root.busy
      onClicked: root.commandRequested(["pi-provider-probe","xai",root.info.model])
    }
  }
  CompactRow {
    Layout.fillWidth: true
    label: root.tr("Catálogo Pi")
    status: root.catalog.detail || root.tr("Pendiente")
    switchVisible: false
    navigationEnabled: false
  }
  Ui.Button {
    objectName: "pi-models"
    text: root.tr("Consultar modelos")
    visible: root.selected
    enabled: !root.busy
    onClicked: root.commandRequested(["agent-models","pi"])
  }
  RoomyDropdown {
    objectName: "pi-default-model"
    Layout.fillWidth: true
    label: root.tr("Modelo · sesiones nuevas")
    options: root.modelOptions
    value: root.info.model || ""
    enabled: !root.busy && root.selected && !!root.providers.xai && !root.catalog.stale
    onChanged: value => { if (value) root.commandRequested(["pi-model",value]) }
  }
  RowLayout {
    Layout.fillWidth: true
    spacing: root.metrics.xxs
    CompactRow {
      id: nous
      objectName: "pi-provider-nous"
      Layout.fillWidth: true
      label: root.tr("Nous · gratis")
      status: !root.providers.nous ? root.tr("Desactivado") : root.nousCheck.remoteVerified ? root.tr("Conectado · {modelo}", {modelo: root.nousCheck.model}) : root.nousCheck.ready ? root.tr("Conexión pendiente") : root.tr("Autenticación pendiente")
      checked: !!root.providers.nous
      busy: root.busy
      navigationEnabled: false
      enabled: !root.busy && root.selected
      onToggled: root.commandRequested(["pi-provider","nous",root.providers.nous ? "false" : "true"])
    }
  }
  Text {
    objectName: "pi-nous-connection"
    Layout.fillWidth: true
    visible: !!root.providers.nous && !!root.nousCheck.checkedAt
    text: (root.nousCheck.detail || root.tr("Pendiente")) + (root.nousCheck.model ? " · " + root.nousCheck.model : "")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    elide: Text.ElideRight
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
  }
  RowLayout {
    spacing: root.metrics.xxs
    Ui.Button {
      objectName: "pi-check-nous"
      text: root.tr("Comprobar Nous")
      visible: root.selected && !!root.providers.nous
      enabled: !root.busy
      onClicked: root.commandRequested(["pi-provider-check","nous"])
    }
    Ui.Button {
      objectName: "pi-probe-nous"
      text: root.tr("Probar · :free")
      visible: root.selected && !!root.providers.nous && ((root.info.nousCatalog || {}).models || []).length > 0 && (root.info.quota || {}).requestsPerMinute > 0 && !!root.nousCheck.ready
      enabled: !root.busy
      onClicked: root.commandRequested(["pi-provider-probe","nous","nous/" + root.info.nousCatalog.models[0].id])
    }
  }
  CompactRow {
    Layout.fillWidth: true
    label: root.tr("Catálogo Nous")
    status: (root.info.nousCatalog || {}).checkedAt || root.tr("Sin consultar")
    switchVisible: false
    navigationEnabled: false
  }
  Ui.Button {
    objectName: "pi-nous-refresh"
    text: root.tr("Actualizar catálogo Nous")
    visible: root.selected && !!root.providers.nous
    enabled: !root.busy
    onClicked: root.commandRequested(["pi-nous-refresh"])
  }
  Ui.Button {
    objectName: "pi-advanced"
    text: root.advanced ? root.tr("Ocultar avanzado") : root.tr("Avanzado")
    focusable: true
    onClicked: root.advanced = !root.advanced
  }
  ColumnLayout {
    visible: root.advanced
    Layout.fillWidth: true
    spacing: Style.space(6)

    Ui.NumberField {
      id: rpm
      objectName: "pi-nous-rpm"
      Layout.fillWidth: true
      label: root.tr("RPM declarado · 0 = desconocido")
      from: 0
      to: 100000
      value: (root.info.quota || {}).requestsPerMinute || 0
      enabled: !root.busy && root.selected
      onModified: value => rpm.value = value
    }
    Ui.NumberField {
      id: margin
      objectName: "pi-nous-margin"
      Layout.fillWidth: true
      label: root.tr("Margen RPM (%)")
      from: 0
      to: 90
      value: (root.info.quota || {}).marginPercent === undefined ? 25 : root.info.quota.marginPercent
      enabled: !root.busy && root.selected
      onModified: value => margin.value = value
    }
    Ui.Button {
      objectName: "pi-nous-quota-save"
      text: root.tr("Guardar cuota")
      enabled: !root.busy && root.selected
      onClicked: root.commandRequested(["pi-nous-quota","--rpm",String(rpm.value),"--margin",String(margin.value)])
    }
    CompactRow {
      Layout.fillWidth: true
      label: root.tr("Cuota compartida")
      status: (root.info.quotaConnection || {}).paused ? root.tr("En pausa · indica reset oficial") : (root.info.quotaConnection || {}).connected ? root.tr("Conectada · sesiones nuevas") : root.tr("Pendiente")
      statusColor: (root.info.quotaConnection || {}).paused ? Commons.Color.urgent : Commons.Color.muted
      switchVisible: false
      navigationEnabled: false
    }
    Ui.TextField {
      id: reset
      objectName: "pi-nous-reset"
      Layout.fillWidth: true
      placeholderText: root.tr("Reset oficial UTC · AAAA-MM-DDThh:mm:ssZ")
      enabled: !root.busy && root.selected && !!root.providers.nous
    }
    Ui.Button {
      objectName: "pi-nous-resume"
      text: root.tr("Programar reintento")
      enabled: !root.busy && root.selected && !!root.providers.nous && !!reset.text
      onClicked: root.commandRequested(["pi-nous-resume",reset.text])
    }
  }
  Repeater {
    model: (root.info.nousCatalog || {}).models || []
    delegate: CompactRow {
      required property var modelData
      Layout.fillWidth: true
      label: modelData.name
      status: !root.providers.nous ? root.tr("Desactivado") : root.nousCheck.remoteVerified && root.nousCheck.model === "nous/" + modelData.id ? root.tr("Conectado · :free") : root.tr("Pendiente · :free")
      switchVisible: false
      navigationEnabled: false
    }
  }
  Text {
    Layout.fillWidth: true
    text: root.tr("Pi: sin sandbox ni aprobación por herramienta.")
    textFormat: Text.PlainText
    color: Commons.Color.muted
    elide: Text.ElideRight
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
  }
}
