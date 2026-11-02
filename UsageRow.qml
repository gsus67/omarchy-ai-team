pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Commons as Commons

// One account on the front page: its name and two compact bars (5 h and weekly). Click opens the detail.
Item {
  id: root
  property var row: ({})
  property string fiveLabel: ""
  property string weeklyLabel: ""
  readonly property var fonts: Style.font
  signal opened()

  implicitHeight: content.implicitHeight + Style.space(10)
  activeFocusOnTab: true
  Keys.onReturnPressed: root.opened()
  Keys.onEnterPressed: root.opened()
  Keys.onSpacePressed: root.opened()

  Rectangle {
    anchors.fill: parent
    radius: Style.cornerRadius
    color: Qt.rgba(Commons.Color.foreground.r, Commons.Color.foreground.g, Commons.Color.foreground.b, hover.hovered || root.activeFocus ? 0.10 : 0.05)
  }

  RowLayout {
    id: content
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    anchors.leftMargin: Style.space(8)
    anchors.rightMargin: Style.space(8)
    spacing: Style.space(8)

    Text {
      Layout.preferredWidth: Style.space(52)
      text: root.row.name || ""
      textFormat: Text.PlainText
      color: Commons.Color.foreground
      font.family: root.fonts.family
      font.pixelSize: root.fonts.caption
      font.bold: true
      elide: Text.ElideRight
    }
    UsageBar {
      Layout.fillWidth: true
      Layout.preferredWidth: 1
      label: root.fiveLabel
      percent: root.row.five === undefined ? null : root.row.five
    }
    UsageBar {
      Layout.fillWidth: true
      Layout.preferredWidth: 1
      label: root.weeklyLabel
      percent: root.row.weekly === undefined ? null : root.row.weekly
    }
  }

  HoverHandler { id: hover }
  MouseArea {
    anchors.fill: parent
    cursorShape: Qt.PointingHandCursor
    onClicked: root.opened()
  }
}
