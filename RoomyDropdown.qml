import QtQuick
import qs.Ui as Ui

Item {
  id: root
  property alias label: dropdown.label
  property string value: ""
  property alias options: dropdown.options
  readonly property bool popupOpen: dropdown.popupOpen
  signal changed(string value)

  implicitWidth: dropdown.implicitWidth
  implicitHeight: dropdown.implicitHeight

  function open() { dropdown.open() }
  function close() { dropdown.close() }

  // The native popup closes on any outside press, including its own trigger,
  // so the release would reopen it. Ignore the click that closed it.
  property real closedAt: 0
  onPopupOpenChanged: if (!popupOpen) root.closedAt = Date.now()
  onVisibleChanged: if (!visible) root.close()

  Ui.Dropdown {
    id: dropdown
    objectName: root.objectName
    anchors.fill: parent
    value: root.value
    onChanged: value => {
      root.changed(value)
      // The native control writes value before emitting changed. Restore its
      // binding so backend replies and resets still update the selected label.
      dropdown.value = Qt.binding(() => root.value)
    }
  }
  MouseArea {
    anchors.fill: dropdown
    enabled: dropdown.enabled
    cursorShape: Qt.PointingHandCursor
    onClicked: if (Date.now() - root.closedAt > 250) dropdown.toggle()
  }
}
