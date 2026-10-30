import QtQuick
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons

Item {
  id: root
  readonly property var metrics: Style.spacing
  property alias label: dropdown.label
  property string value: ""
  property alias options: dropdown.options
  readonly property bool popupOpen: dropdown.popupOpen
  signal changed(string value)

  implicitWidth: dropdown.implicitWidth
  implicitHeight: dropdown.implicitHeight
  // Keep height equal to the trigger; the layout owns the reserved space.
  Layout.bottomMargin: room.height

  function open() { dropdown.open() }
  function close() { dropdown.close() }

  function revealPopup() {
    if (!root.popupOpen) return
    for (let ancestor = root.parent; ancestor; ancestor = ancestor.parent) {
      const flickable = ancestor as Flickable
      if (!flickable) continue
      const bottom = root.mapToItem(flickable.contentItem, 0, root.height).y + room.height
      flickable.contentY = Math.max(0, Math.min(flickable.contentHeight - flickable.height,
        Math.max(flickable.contentY, bottom - flickable.height)))
      break
    }
  }
  // The native popup closes on any outside press, including its own trigger,
  // so the release would reopen it. Ignore the click that closed it.
  property real closedAt: 0
  onPopupOpenChanged: if (popupOpen) Qt.callLater(root.revealPopup); else root.closedAt = Date.now()
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
  Item {
    id: room
    objectName: root.objectName + "-room"
    anchors.top: parent.bottom
    width: parent.width
    height: root.popupOpen ? Math.min(dropdown.options.length * dropdown.popupRowHeight
      + Math.max(0, dropdown.options.length - 1) * root.metrics.labelGap + root.metrics.xxs,
      8 * dropdown.popupRowHeight + 7 * root.metrics.labelGap + root.metrics.xxs) : 0
  }
}
