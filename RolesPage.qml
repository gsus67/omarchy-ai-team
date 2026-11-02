pragma ComponentBehavior: Bound
import QtQuick
import "I18n.js" as I18n
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons
import qs.Commons as Commons

// Total spent (last 30 days, selected accounts) and how much of it each team role used.
ColumnLayout {
  id: root
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  property var info: ({})
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
    text: root.tr("{cantidad} tokens en 30 días", {cantidad: root.info.total || "—"})
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
          percent: Math.round(role.modelData.share * 100)
        }
        Text {
          Layout.preferredWidth: Style.space(44)
          horizontalAlignment: Text.AlignRight
          text: role.modelData.tokens
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
  Note { text: root.tr("El Jefe es el consumo de las sesiones. Los obreros (Planificar, Programar, Revisar y Sencillas) se cuentan desde que se registran sus encargos{desde}.", {desde: root.info.since ? " (" + root.info.since + ")" : ""}) }
}
