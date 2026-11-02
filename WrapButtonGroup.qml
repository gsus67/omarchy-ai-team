pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons

// Same API as the shell's Ui.ButtonGroup (options, value, changed), but the options flow onto the next
// line when they do not fit in the width instead of being clipped by the panel.
Flow {
  id: root
  Layout.fillWidth: true

  readonly property var fonts: Style.font
  readonly property var metrics: Style.spacing

  property var options: []
  property string value: ""
  property real fontSize: root.fonts.body
  property string fontFamily: root.fonts.family
  property bool focusable: true

  signal changed(string value)

  spacing: root.metrics.xxs

  function optionValue(o) { return (o && typeof o === "object") ? String(o.value) : String(o) }
  function optionLabel(o) { return (o && typeof o === "object" && o.label !== undefined) ? String(o.label) : String(o) }

  Repeater {
    model: root.options

    delegate: Ui.Button {
      required property var modelData
      text: root.optionLabel(modelData)
      selected: root.optionValue(modelData) === root.value
      bordered: true
      focusable: root.focusable
      fontFamily: root.fontFamily
      fontSize: root.fontSize
      onClicked: root.changed(root.optionValue(modelData))
    }
  }
}
