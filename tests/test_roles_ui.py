import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RolesUiTests(unittest.TestCase):
    def test_usage_page_shows_cache_partial_totals_muse_and_plugin_only_note(self):
        source = (ROOT / 'RolesPage.qml').read_text()
        for wanted in ('roles-total-cache', 'modelData.cache', 'partialTotal', 'Muse tiene su propia fila',
                       'los ayudantes internos de la CLI no se registran'):
            self.assertIn(wanted, source)
        self.assertIn('objectName: "plugin-version-header"', (ROOT / 'SettingsPage.qml').read_text())

    def test_main_screen_has_a_total_row_that_opens_the_roles_page(self):
        source = (ROOT / 'HarnessContent.qml').read_text()
        row = re.search(r'objectName: "usage-total".*?\n        \}', source, re.S)[0]
        self.assertIn('root.page = "roles"', row)
        self.assertRegex(source, re.compile(r'RolesPage \{.*?visible: root\.page === "roles".*?root\.snapshot\.roleUsage', re.S))

    def test_the_total_is_measured_over_the_selected_window_since_the_effective_cut(self):
        source = (ROOT / 'RolesPage.qml').read_text()
        self.assertIn('root.tr("Medido desde {fecha}"', source)
        self.assertIn('root.tr("Inicio de medición no disponible")', source)
        self.assertRegex(source, r'root\.info\.since\b')
        self.assertIn('root.tr("Última medición: {fecha}"', source)
        self.assertRegex(source, r'root\.info\.asOf\b')
        self.assertIn('root.tr("Aún no hay {dias} días completos de medición. Se mide desde {fecha}."', source)
        self.assertRegex(source, r'root\.info\.partialWindow\b')

    def test_the_old_common_start_note_is_retired_for_the_shared_window_note(self):
        source = (ROOT / 'RolesPage.qml').read_text()
        self.assertNotIn('se miden desde el mismo inicio y con la misma unidad', source)
        self.assertIn('root.tr("Todas las filas usan la misma ventana móvil y el mismo inicio común.")', source)

    def test_the_window_selector_offers_only_seven_and_thirty_days_and_asks_the_backend(self):
        page = (ROOT / 'RolesPage.qml').read_text()
        self.assertIn('signal windowRequested(int days)', page)
        self.assertRegex(page, re.compile(r'WrapButtonGroup \{.*?root\.tr\("7 días"\).*?root\.tr\("30 días"\)', re.S))
        self.assertIn('value: String(root.windowDays)', page)
        self.assertIn('onChanged: value => root.windowRequested(parseInt(value))', page)
        self.assertIn('enabled: !root.busy', page)  # the selector locks on the real busy state only
        self.assertIn('root.tr("Actualizando medición…")', page)
        content = (ROOT / 'HarnessContent.qml').read_text()
        wired = re.search(r'RolesPage \{.*?\n      \}', content, re.S)[0]
        self.assertIn('windowDays: root.usageWindowDays', wired)
        self.assertIn('busy: root.busy', wired)
        self.assertIn('measurementPending: root.usageWindowPending', wired)
        self.assertIn('onWindowRequested: days => root.requestUsageWindow(days)', wired)
        self.assertIn('root.commandRequested(["usage-window", String(days)])', content)
        self.assertEqual(content.count('commandRequested(["usage-window"'), 1)
        self.assertIn('property int requestedUsageWindowDays: 0', content)

    def test_figures_and_dates_hide_while_the_window_is_loading(self):
        page = (ROOT / 'RolesPage.qml').read_text()
        self.assertIn('property bool measurementPending: false', page)
        self.assertIn('readonly property bool loading: root.busy || root.measurementPending', page)
        for name in ('roles-total', 'roles-since', 'roles-asof', 'roles-overlap'):
            self.assertRegex(page, r'objectName: "%s"[^\n]*?visible: !root\.loading|objectName: "%s"\s+Layout.fillWidth: true\s+visible: !root\.loading' % (name, name))
        self.assertIn('model: root.loading ? [] :', page)
        self.assertIn('status: root.busy || root.usageWindowPending ? "" :', (ROOT / 'HarnessContent.qml').read_text())

    def test_figures_of_the_previous_window_are_hidden_while_the_request_is_pending(self):
        page = (ROOT / 'RolesPage.qml').read_text()
        self.assertIn('visible: !root.loading', page)
        self.assertIn('model: root.loading ? [] :', page)

    def test_a_pending_measurement_is_shown_as_pending_not_as_zero(self):
        self.assertIn('root.info.pending', (ROOT / 'RolesPage.qml').read_text())

    def test_unknown_values_are_not_shown_as_zero(self):
        source = (ROOT / 'RolesPage.qml').read_text()
        self.assertEqual(source.count('root.tr("Desconocido")'), 2)  # the total and each role's tokens
        self.assertIn('root.info.total === null', source)
        self.assertIn('role.modelData.tokens === null', source)
        self.assertIn('role.modelData.share === null ? null', source)  # the bar reads null as unknown, never a coerced 0
        self.assertNotIn('role.modelData.share * 100) ||', source)
        self.assertNotRegex(source, r'(total|tokens|share)\s*\|\|\s*0')

    def test_the_otros_row_enters_through_the_roles_model_without_its_own_styling(self):
        source = (ROOT / 'RolesPage.qml').read_text()
        self.assertNotIn('otros', source.replace('root.tr("Otros', '').replace('Otros reúne', ''))
        self.assertEqual(source.count('model: root.loading ? [] :'), 1)

    def test_the_jefe_note_shows_only_when_the_classification_is_limited(self):
        source = (ROOT / 'RolesPage.qml').read_text()
        note = re.search(r'Note \{[^\n]*root\.info\.classificationLimited[^\n]*\}', source)[0]
        self.assertIn('!root.loading', note)
        self.assertIn(f'root.tr("{self.OVERLAP}")', note)
        self.assertNotIn('Parte del consumo sin origen fiable', source)

    OVERLAP = "Jefe incluye el consumo cuyo origen no se pudo verificar; algunos encargos pueden contarse también en los roles."

    def test_the_overlap_note_has_an_exact_english_translation(self):
        import json
        english = json.loads((ROOT / 'translations' / 'en.json').read_text())
        self.assertEqual(english[self.OVERLAP], "Chief includes usage whose origin could not be verified; some tasks may also be counted in the roles.")
        self.assertNotIn("Parte del consumo sin origen fiable se cuenta como Jefe.", english)

    def test_the_otros_note_is_always_visible_and_says_the_split_is_partial(self):
        source = (ROOT / 'RolesPage.qml').read_text()
        note = re.search(r'Note \{ text: root\.tr\("Otros reúne[^\n]*\}', source)[0]
        self.assertNotIn('visible', note)
        self.assertIn('parcial', note)

    def test_share_meters_never_turn_urgent(self):
        self.assertIn('warn: false', (ROOT / 'RolesPage.qml').read_text())
        self.assertIn('root.warn &&', (ROOT / 'UsageBar.qml').read_text())


if __name__ == '__main__':
    unittest.main()
