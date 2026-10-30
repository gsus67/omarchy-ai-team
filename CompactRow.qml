pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons
import qs.Commons as Commons

RowLayout {
  id: root
  property string icon: ""
  property string label: ""
  property string status: ""
  property color statusColor: Commons.Color.muted
  property bool checked: false
  property bool switchVisible: true
  property bool busy: false
  property bool navigationEnabled: true
  readonly property var fonts: Style.font
  signal opened()
  signal toggled()
  Layout.minimumWidth: 0
  spacing: Style.space(2)

  Ui.Button {
    id: entry
    objectName: root.objectName + "-open"
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    implicitWidth: labels.implicitWidth + Style.space(12)
    implicitHeight: labels.implicitHeight + Style.space(12)
    visible: root.navigationEnabled
    focusable: root.navigationEnabled
    onClicked: root.opened()
  }
  // Ui.Button keeps its hover state while disabled, so informational rows use a
  // plain item: nothing under the pointer can highlight.
  Item {
    id: plain
    visible: !root.navigationEnabled
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    implicitWidth: entry.implicitWidth
    implicitHeight: entry.implicitHeight

    RowLayout {
      id: labels
      parent: root.navigationEnabled ? entry : plain
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        visible: root.icon !== ""
        text: root.icon
        textFormat: Text.PlainText
        color: Commons.Color.foreground
        font.family: root.fonts.family
        font.pixelSize: root.fonts.title
      }
      Text {
        text: root.label
        textFormat: Text.PlainText
        color: Commons.Color.foreground
        font.family: root.fonts.family
        font.pixelSize: root.fonts.body
        elide: Text.ElideRight
        Layout.minimumWidth: 0
      }
      Text {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        text: root.status
        textFormat: Text.PlainText
        color: root.statusColor
        font.family: root.fonts.family
        font.pixelSize: root.fonts.caption
        horizontalAlignment: Text.AlignRight
        elide: Text.ElideRight
      }
      Text {
        visible: !root.switchVisible && root.navigationEnabled
        text: "›"
        textFormat: Text.PlainText
        color: Commons.Color.muted
        font.family: root.fonts.family
        font.pixelSize: root.fonts.body
      }
    }
  }
  Ui.ToggleSwitch {
    objectName: root.objectName + "-switch"
    visible: root.switchVisible
    checked: root.checked
    busy: root.busy
    enabled: !root.busy
    activeFocusOnTab: true
    hasCursor: activeFocus
    Keys.onReturnPressed: if (enabled) root.toggled()
    Keys.onEnterPressed: if (enabled) root.toggled()
    Keys.onSpacePressed: if (enabled) root.toggled()
    onToggled: root.toggled()
  }
}
