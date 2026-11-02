pragma ComponentBehavior: Bound
import QtQuick
import "I18n.js" as I18n
import QtQuick.Layouts
import qs.Ui as Ui
import qs.Commons
import qs.Commons as Commons

// Editable token prices (USD per million) per model, plus the button that asks the chief AI to fill the empty ones.
ColumnLayout {
  id: root
  property var translations: ({})
  function tr(source, values) { return I18n.tr(root.translations, source, values) }
  property var info: ({})
  property bool busy: false
  signal commandRequested(var arguments)
  readonly property var fonts: Style.font
  readonly property var autofill: root.info.autofill || ({})
  readonly property var kinds: [
    {key: "input", label: root.tr("Entrada")},
    {key: "output", label: root.tr("Salida")},
    {key: "cacheRead", label: root.tr("Caché leída")},
    {key: "cacheWrite", label: root.tr("Caché escrita")}]
  property var drafts: ({})  // unsaved edits survive the snapshot refresh that follows saving another field
  spacing: Style.space(6)

  component Note: Text {
    Layout.fillWidth: true
    textFormat: Text.PlainText
    color: Commons.Color.muted
    font.family: root.fonts.family
    font.pixelSize: root.fonts.caption
    wrapMode: Text.WordWrap
  }

  Note { text: root.tr("USD por millón de tokens. Un campo vacío es un precio desconocido: el coste de ese modelo no se estima.") }
  Ui.Button {
    objectName: "price-autofill"
    text: root.busy ? root.tr("Procesando…") : root.tr("Autorrellenar con IA jefe")
    focusable: true
    enabled: !root.busy
    onClicked: root.commandRequested(["price-autofill"])
  }
  Note { text: root.tr("Solo rellena los huecos vacíos y nunca cambia los precios que ya tienes. Usa búsqueda web y gasta tokens de la cuenta jefe.") }
  Note {
    visible: !!root.autofill.at
    text: root.autofill.error ? root.tr("Último intento fallido: {error}", {error: root.autofill.error})
      : root.tr("Último autorrelleno: {fecha} · {agente} · {cantidad} rellenados", {fecha: root.autofill.at || "", agente: root.autofill.agent || "", cantidad: (root.autofill.filled || []).length})
  }
  Repeater {
    model: root.autofill.filled || []
    delegate: Note {
      required property var modelData
      text: modelData.model + " · " + modelData.field + " · " + (modelData.source || "")
    }
  }
  Note {
    visible: (root.autofill.pending || []).length > 0
    text: root.tr("Siguen sin precio: {modelos}", {modelos: (root.autofill.pending || []).join(", ")})
  }
  Ui.PanelSeparator { Layout.fillWidth: true }

  Repeater {
    model: root.info.rows || []
    delegate: ColumnLayout {
      id: row
      required property var modelData
      Layout.fillWidth: true
      spacing: Style.space(2)
      Text {
        Layout.fillWidth: true
        text: row.modelData.model
        textFormat: Text.PlainText
        color: Commons.Color.foreground
        font.family: root.fonts.family
        font.pixelSize: root.fonts.body
        font.bold: true
        elide: Text.ElideRight
      }
      Flow {
        Layout.fillWidth: true
        spacing: Style.space(8)
        Repeater {
          model: root.kinds
          delegate: ColumnLayout {
            id: cell
            required property var modelData
            readonly property var stored: row.modelData[cell.modelData.key]
            readonly property string storedText: cell.stored === null || cell.stored === undefined ? "" : String(cell.stored)
            spacing: Style.space(2)
            Text {
              text: cell.modelData.label
              textFormat: Text.PlainText
              color: Commons.Color.muted
              font.family: root.fonts.family
              font.pixelSize: root.fonts.caption
            }
            RowLayout {
              spacing: Style.space(4)
              Ui.TextField {
                id: field
                objectName: "price-" + row.modelData.model + "-" + cell.modelData.key
                Layout.preferredWidth: Style.space(60)
                readonly property string draftKey: row.modelData.model + "|" + cell.modelData.key
                text: root.drafts[field.draftKey] !== undefined ? root.drafts[field.draftKey] : cell.storedText
                onTextChanged: if (text !== cell.storedText || root.drafts[field.draftKey] !== undefined) {
                  const next = Object.assign({}, root.drafts)
                  if (text === cell.storedText) delete next[field.draftKey]
                  else next[field.draftKey] = text
                  root.drafts = next
                }
                enabled: !root.busy
              }
              Ui.Button {
                objectName: "price-save-" + row.modelData.model + "-" + cell.modelData.key
                text: root.tr("Guardar")
                focusable: true
                enabled: !root.busy && field.text.trim() !== cell.storedText
                onClicked: root.commandRequested(["price-set", row.modelData.model, cell.modelData.key, field.text.trim()])
              }
            }
          }
        }
      }
      Ui.PanelSeparator {
        Layout.fillWidth: true
        Layout.topMargin: Style.space(2)
        strength: 0.08
      }
    }
  }
}
