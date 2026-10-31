import QtQuick
import "I18n.js" as I18n
import qs.Ui as Ui

Ui.MultiSelect {
  id: root
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  objectName: "agent-selector"
  property var agents: []
  property bool busy: false
  readonly property var selectedIds: root.agents.filter(agent => agent.enabled).map(agent => agent.id)
  signal selectionRequested(var ids)
  enabled: !root.busy
  showLabel: false
  placeholderText: root.tr("Buscar agente…")
  emptyText: root.tr("Sin agentes")
  noSelectionText: root.tr("Sin agentes")
  values: root.selectedIds
  options: root.agents.map(agent => ({value: agent.id, label: agent.installation === "installed" ? agent.name : root.tr("{nombre} · no instalado", {nombre: agent.name})}))
  triggerLabel: root.agents.filter(agent => agent.enabled).map(agent => agent.name).join(", ") || root.tr("Sin agentes")

  // The native label otherwise includes installation hints or a selection count.
  function selectionLabel() { return root.triggerLabel }

  onVisibleChanged: if (!visible) root.close()
  // The native popup closes on any outside press, including its own trigger,
  // so the release would reopen it. Ignore the click that closed it.
  property real closedAt: 0
  onPopupOpenChanged: if (!popupOpen) root.closedAt = Date.now()

  MouseArea {
    anchors.fill: parent
    z: 1
    enabled: root.enabled
    cursorShape: Qt.PointingHandCursor
    onClicked: if (Date.now() - root.closedAt > 250) root.toggle()
  }

  onChanged: values => {
    const ids = root.agents.filter(agent => values.indexOf(agent.id) !== -1 && (agent.installation !== "missing-profile" || agent.enabled)).map(agent => agent.id)
    // MultiSelect writes values itself; the snapshot remains the authority,
    // including rejected missing profiles and partial backend failures.
    root.values = Qt.binding(() => root.selectedIds)
    if (!root.busy && ids.join(",") !== root.selectedIds.join(",")) root.selectionRequested(ids)
  }
}
