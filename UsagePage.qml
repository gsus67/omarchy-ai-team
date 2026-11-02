pragma ComponentBehavior: Bound
import QtQuick
import "I18n.js" as I18n
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons

// Detail of one account: limit windows with their reset time, tokens by model and where the tokens go.
ColumnLayout {
  id: root
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  property var account: ({})
  readonly property var fonts: Style.font
  readonly property var spend: root.account.spend || null
  spacing: Style.space(6)

  component Heading: Text {
    Layout.fillWidth: true
    Layout.topMargin: Style.space(4)
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    font.bold: true
  }

  component Line: RowLayout {
    id: line
    property string name: ""
    property string value: ""
    Layout.fillWidth: true
    spacing: Style.space(8)
    Text {
      Layout.fillWidth: true
      Layout.minimumWidth: 0
      text: line.name
      textFormat: Text.PlainText
      color: Commons.Color.foreground
      font.family: root.fonts.family
      font.pixelSize: root.fonts.caption
      elide: Text.ElideMiddle
    }
    Text {
      text: line.value
      textFormat: Text.PlainText
      color: Commons.Color.muted
      font.family: root.fonts.family
      font.pixelSize: root.fonts.caption
      font.bold: true
    }
  }

  Text {
    Layout.fillWidth: true
    text: (root.account.name || "") + (root.account.tier ? " · " + root.account.tier : "")
    textFormat: Text.PlainText
    color: Commons.Color.foreground
    font.family: root.fonts.family
    font.pixelSize: root.fonts.body
    font.bold: true
    elide: Text.ElideRight
  }

  Heading { text: root.tr("Límites") }
  Repeater {
    model: root.account.windows || []
    delegate: ColumnLayout {
      id: windowRow
      required property var modelData
      Layout.fillWidth: true
      spacing: 0
      UsageBar {
        Layout.fillWidth: true
        label: windowRow.modelData.label
        labelWidth: Style.space(96)
        percent: windowRow.modelData.percent
      }
      Text {
        visible: windowRow.modelData.reset !== ""
        Layout.leftMargin: Style.space(96 + 4)
        text: windowRow.modelData.reset
        textFormat: Text.PlainText
        color: Commons.Color.muted
        font.family: root.fonts.family
        font.pixelSize: root.fonts.caption
      }
    }
  }

  Heading {
    visible: (root.account.models || []).length > 0
    text: root.tr("Tokens por modelo")
  }
  Repeater {
    model: root.account.models || []
    delegate: Line {
      required property var modelData
      name: modelData.name
      value: modelData.tokens
    }
  }

  Heading {
    visible: root.spend !== null
    text: root.tr("Dónde se gastan") + (root.spend ? " · " + root.tr("Coste estimado:") + " " + root.spend.cost : "")
  }
  Heading { visible: root.spend !== null; text: root.tr("Por tipo") }
  Repeater {
    model: root.spend ? root.spend.kinds : []
    delegate: Line {
      required property var modelData
      name: modelData.name
      value: modelData.tokens
    }
  }
  Heading { visible: root.spend !== null && root.spend.projects.length > 0; text: root.tr("Por proyecto") }
  Repeater {
    model: root.spend ? root.spend.projects : []
    delegate: Line {
      required property var modelData
      name: modelData.name
      value: modelData.tokens + " · " + modelData.cost
    }
  }
  Heading { visible: root.spend !== null && root.spend.tools.length > 0; text: root.tr("Por herramienta") }
  Repeater {
    model: root.spend ? root.spend.tools : []
    delegate: Line {
      required property var modelData
      name: modelData.name
      value: modelData.tokens + " · " + modelData.cost
    }
  }
  Text {
    visible: root.spend !== null
    Layout.fillWidth: true
    text: root.tr("Coste estimado a precio de API; entre herramientas el reparto es a partes iguales")
          + (root.spend && root.spend.unpriced !== "" ? "\n" + root.tr("Sin precio:") + " " + root.spend.unpriced : "")
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
  }
}
