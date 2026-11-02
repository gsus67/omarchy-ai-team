pragma ComponentBehavior: Bound
import QtQuick
import "I18n.js" as I18n
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons
import qs.Commons as Commons

// Total since the common start (Chief and workers, selected accounts) and how much of it each team role used.
// An unknown value (null) is shown as unknown, never as zero.
ColumnLayout {
  id: root
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  property var info: ({})
  property int windowDays: 7
  property bool busy: false
  property bool measurementPending: false  // a window was requested and its measurement has not arrived yet
  readonly property bool loading: root.busy || root.measurementPending
  signal windowRequested(int days)
  function dateText(iso) {
    const moment = iso ? new Date(iso) : null
    return moment && !isNaN(moment.getTime()) ? moment.toLocaleString(Qt.locale(), Locale.ShortFormat) : ""
  }
  readonly property string sinceText: root.dateText(root.info.since)
  readonly property string asOfText: root.dateText(root.info.asOf)
  readonly property var fonts: Style.font
  spacing: Style.space(6)

  component Note: Text {
    Layout.fillWidth: true
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
  }

  Ui.PanelSectionHeader { text: root.tr("TOTAL") }
  WrapButtonGroup {
    objectName: "roles-window"
    options: [{value: "7", label: root.tr("7 días")}, {value: "30", label: root.tr("30 días")}]
    value: String(root.windowDays)
    enabled: !root.busy
    onChanged: value => root.windowRequested(parseInt(value))
  }
  Text {
    objectName: "roles-total"
    Layout.fillWidth: true
    visible: !root.loading  // the figures of the previous window are hidden until the requested one arrives
    text: root.info.total === null || root.info.total === undefined ? root.tr("Desconocido") : root.tr("{cantidad} tokens", {cantidad: root.info.total})
    textFormat: Text.PlainText
    color: Commons.Color.foreground
    font.family: root.fonts.family
    font.pixelSize: root.fonts.body
    font.bold: true
  }
  Repeater {
    model: root.loading ? [] : (root.info.roles || [])
    delegate: ColumnLayout {
      id: role
      objectName: "roles-row"
      required property var modelData
      Layout.fillWidth: true
      spacing: Style.space(2)
      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(8)
        UsageBar {
          Layout.fillWidth: true
          label: role.modelData.name
          labelWidth: Style.space(84)
          warn: false
          percent: role.modelData.share === null ? null : Math.round(role.modelData.share * 100)
        }
        Text {
          Layout.preferredWidth: Style.space(64)
          horizontalAlignment: Text.AlignRight
          text: role.modelData.tokens === null ? root.tr("Desconocido") : role.modelData.tokens
          textFormat: Text.PlainText
          color: Commons.Color.muted
          font.family: root.fonts.family
          font.pixelSize: root.fonts.caption
          font.bold: true
        }
      }
      Ui.PanelSeparator {
        Layout.fillWidth: true
        strength: 0.08
      }
    }
  }
  Note { objectName: "roles-updating"; visible: root.loading; text: root.tr("Actualizando medición…") }
  Note { visible: !root.loading && !!root.info.pending; text: root.tr("Medición pendiente de actualizar.") }
  Note { objectName: "roles-since"; visible: !root.loading; text: root.sinceText ? root.tr("Medido desde {fecha}", {fecha: root.sinceText}) : root.tr("Inicio de medición no disponible") }
  Note { visible: !root.loading && !!root.info.partialWindow && !!root.sinceText; text: root.tr("Aún no hay {dias} días completos de medición. Se mide desde {fecha}.", {dias: root.windowDays, fecha: root.sinceText}) }
  Note { objectName: "roles-asof"; visible: !root.loading && !!root.asOfText; text: root.tr("Última medición: {fecha}", {fecha: root.asOfText}) }
  Note { objectName: "roles-overlap"; visible: !root.loading && !!root.info.classificationLimited; text: root.tr("Jefe incluye el consumo cuyo origen no se pudo verificar; algunos encargos pueden contarse también en los roles.") }
  Note { text: root.tr("Otros reúne el consumo de Claude y Codex fuera de los papeles del equipo; el reparto es parcial.") }
  Note { text: root.tr("Todas las filas usan la misma ventana móvil y el mismo inicio común.") }
}
