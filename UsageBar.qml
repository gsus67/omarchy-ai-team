pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons

// Thin meter: short label, bar and percent. The bar turns urgent from 90 %, where the usage guard warns.
RowLayout {
  id: root
  property string label: ""
  property var percent: null
  readonly property var fonts: Style.font
  readonly property bool high: root.percent !== null && root.percent >= 90
  readonly property color fill: root.high ? Commons.Color.urgent : Commons.Color.accent
  Layout.minimumWidth: 0
  spacing: Style.space(4)

  Text {
    Layout.preferredWidth: Style.space(24)
    text: root.label
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    elide: Text.ElideRight
  }

  Rectangle {
    Layout.fillWidth: true
    Layout.minimumWidth: Style.space(24)
    Layout.preferredHeight: Style.space(6)
    radius: height / 2
    color: Qt.rgba(Commons.Color.foreground.r, Commons.Color.foreground.g, Commons.Color.foreground.b, 0.14)

    Rectangle {
      width: root.percent === null ? 0 : parent.width * Math.max(0, Math.min(100, root.percent)) / 100
      height: parent.height
      radius: parent.radius
      color: root.fill
    }
  }

  Text {
    Layout.preferredWidth: Style.space(30)
    horizontalAlignment: Text.AlignRight
    text: root.percent === null ? "—" : root.percent + "%"
    textFormat: Text.PlainText
    color: root.high ? Commons.Color.urgent : Commons.Color.foreground
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    font.bold: root.high
  }
}
