pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons

// Thin meter: short label, bar and percent. The bar turns urgent from 90 %, where the usage guard warns.
RowLayout {
  id: root
  property string label: ""
  property real labelWidth: Style.space(24)
  property var percent: null
  property bool warn: true  // share meters are not limits: they never turn urgent
  readonly property var fonts: Style.font
  readonly property var metrics: Style.spacing
  readonly property bool high: root.warn && root.percent !== null && root.percent >= 90
  readonly property color fill: root.high ? Commons.Color.urgent : Commons.Color.foreground
  Layout.minimumWidth: 0
  spacing: Style.space(4)

  Text {
    Layout.preferredWidth: root.labelWidth
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
    Layout.preferredHeight: Math.max(Style.space(4), Math.round(root.metrics.controlHeight * 0.14))
    radius: height / 2
    color: Style.selectedFillFor(Commons.Color.foreground, Commons.Color.accent)

    Rectangle {
      width: root.percent === null ? 0 : parent.width * Math.max(0, Math.min(100, root.percent)) / 100
      height: parent.height
      radius: parent.radius
      color: root.fill

      Behavior on width {
        NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
      }
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
