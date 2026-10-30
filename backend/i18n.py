"""Spanish source messages and the shared, dependency-free panel lookup."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import json
import os
from pathlib import Path
import re

_language = ContextVar('panel_language', default='es')


def resolve(preference='system', environment=None):
    if preference in ('es', 'en'):
        return preference
    environment = os.environ if environment is None else environment
    locale = next((environment.get(key) for key in ('LC_ALL', 'LC_MESSAGES', 'LANG') if environment.get(key)), '')
    return 'es' if locale.lower().startswith('es') else 'en'


@lru_cache(maxsize=1)
def english():
    return json.loads((Path(__file__).resolve().parents[1] / 'translations/en.json').read_text())


def table(language):
    return english() if language == 'en' else {}


def lookup(source, language, **values):
    template = table(language).get(source, source)
    # Substitute once: user-provided braces are data, never another template.
    return re.sub(r'\{([A-Za-z_][A-Za-z_0-9]*)\}', lambda match: str(values.get(match[1], match[0])), template)


class Message(str):
    """Retain source identity for constants created before settings are loaded."""
    def __new__(cls, source, values):
        value = super().__new__(cls, lookup(source, _language.get(), **{key: localize(item, _language.get()) for key, item in values.items()}))
        value.source = source
        value.values = values
        return value

    def __reduce__(self):
        return (Message, (self.source, self.values))


def tr(source, **values):
    return Message(source, values)


def set_language(language):
    return _language.set(language)


def localize(value, language):
    if isinstance(value, Message):
        return lookup(value.source, language, **{key: localize(item, language) for key, item in value.values.items()})
    if isinstance(value, dict):
        return {key: localize(item, language) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [localize(item, language) for item in value]
    return value


@contextmanager
def using(language):
    token = set_language(language)
    try:
        yield
    finally:
        _language.reset(token)


def current_language():
    return _language.get()


@lru_cache(maxsize=1)
def saved_patterns():
    patterns = []
    for source, translated in english().items():
        for template in (source, translated):
            names = re.findall(r'\{([A-Za-z_][A-Za-z_0-9]*)\}', template)
            if not names:
                continue
            chunks = re.split(r'\{[A-Za-z_][A-Za-z_0-9]*\}', template)
            pattern = '(.*?)'.join(re.escape(chunk) for chunk in chunks)
            patterns.append((re.compile(pattern, re.DOTALL), source, names))
    return sorted(patterns, key=lambda row: len(re.sub(r'\{[^}]+\}', '', row[1])), reverse=True)


def saved_message(value, language):
    """Render only backend-owned persisted message fields, retaining dynamic data."""
    if not isinstance(value, str):
        return value
    translations = english()
    if value in translations:
        return lookup(value, language)
    source = next((key for key, translated in translations.items() if value == translated), None)
    if source is not None:
        return lookup(source, language)
    for pattern, source, names in saved_patterns():
        match = pattern.fullmatch(value)
        if match:
            values = dict(zip(names, match.groups()))
            # This field is an internal role label. Model names, paths and names remain raw.
            if 'papel' in values:
                role = next((key for key in ('Planificar', 'Programar', 'Revisar', 'Tareas sencillas')
                             if values['papel'] in (key, english().get(key))), values['papel'])
                values['papel'] = lookup(role, language)
            for field in ('error', 'aviso'):
                if field in values:
                    values[field] = saved_message(values[field], language)
            return lookup(source, language, **values)
    return value


def localize_saved_fields(value, language):
    # Labels, model names, paths, identifiers and user content are intentionally absent.
    fields = {'detail', 'message', 'error', 'warning', 'lastError', 'retentionError',
              'managementError', 'timerError', 'pausedReason'}
    lists = {'warnings', 'failures', 'updateErrors', 'notices'}
    if isinstance(value, dict):
        return {key: (saved_message(item, language) if key in fields and isinstance(item, str)
                      else [saved_message(row, language) for row in item] if key in lists and isinstance(item, list)
                      else localize_saved_fields(item, language)) for key, item in value.items()}
    if isinstance(value, list):
        return [localize_saved_fields(item, language) for item in value]
    return value


def configured_language():
    """Small standalone bridges may render errors before the panel is involved."""
    base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    try:
        path = base / 'omarchy-ai-team/settings.json'
        if not base.is_absolute() or path.is_symlink() or path.stat().st_size > 1024 * 1024:
            return resolve()
        value = json.loads(path.read_text())
        return resolve(value.get('language', 'system')) if isinstance(value, dict) else resolve()
    except (OSError, ValueError):
        return resolve()
