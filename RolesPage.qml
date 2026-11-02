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
  readonly property var moment: root.info.since ? new Date(root.info.since) : null
  readonly property string sinceText: root.moment && !isNaN(root.moment.getTime()) ? root.moment.toLocaleString(Qt.locale(), Locale.ShortFormat) : ""
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
  Text {
    Layout.fillWidth: true
    text: root.info.total === null || root.info.total === undefined ? root.tr("Desconocido") : root.tr("{cantidad} tokens", {cantidad: root.info.total})
    textFormat: Text.PlainText
    color: Commons.Color.foreground
    font.family: root.fonts.family
    font.pixelSize: root.fonts.body
    font.bold: true
  }
  Repeater {
    model: root.info.roles || []
    delegate: ColumnLayout {
      id: role
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
  Note { text: root.sinceText ? root.tr("Medido desde {fecha}", {fecha: root.sinceText}) : root.tr("Inicio de medición no disponible") }
  Note { text: root.tr("El Jefe y los obreros (Planificar, Programar, Revisar y Sencillas) se miden desde el mismo inicio y con la misma unidad: entrada más salida. Un valor desconocido no cuenta como cero.") }
}
