"""The editable token price table (USD per million tokens) behind the estimated cost.

Shared with tools/usage (the breakdown writer). Reading never creates the file; a price is only
written when the user, or the AI auto-fill for an empty field, sets it.
"""

import importlib.util
import json
import math
from pathlib import Path

from i18n import tr

FIELDS = ('input', 'output', 'cacheRead', 'cacheWrite')
MAX_PRICE = 1000  # USD per million tokens: far above any real rate, far below anything that could overflow


def _prices():
    spec = importlib.util.spec_from_file_location('ai_team_prices', Path(__file__).resolve().parents[1] / 'tools/usage/prices.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def path():
    return Path.home() / '.config/omarchy/agents/prices.json'


def _load():
    return _prices().load_prices(path(), create=False)


def rows():
    table = _load()
    return [{'model': model, **{field: table[model].get(field) for field in FIELDS}} for model in sorted(table)]


def missing():
    return sum(1 for row in rows() if any(row[field] is None for field in FIELDS))


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= value <= MAX_PRICE)  # False for nan, inf and negatives


def _save(table):
    _prices()._write_private(path(), table)


def ensure_writable():
    """Refuse to write over a prices file that exists but cannot be read: saving would replace the user's data."""
    if not path().exists():
        return
    try:
        valid = isinstance(json.loads(path().read_text()), dict)
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise ValueError(tr('El archivo de precios está dañado: arréglalo o bórralo antes de guardar.'))


def set_value(model, field, text):
    """Save one price; an empty text clears it. Raises ValueError (translated) for anything invalid."""
    ensure_writable()
    table = _load()
    if model not in table or field not in FIELDS:
        raise ValueError(tr('Ese precio no existe.'))
    text = str(text).strip()
    if text == '':
        table[model][field] = None
    else:
        try:
            value = float(text)
        except ValueError:
            raise ValueError(tr('El precio debe ser un número entre 0 y 1000 (USD por millón de tokens).')) from None
        if not _number(value):
            raise ValueError(tr('El precio debe ser un número entre 0 y 1000 (USD por millón de tokens).'))
        table[model][field] = value
    _save(table)


def apply_proposals(proposals):
    """Fill empty prices from ``{model: {field: number|None, source: str}}``; never overwrites a price that is set."""
    ensure_writable()
    table = _load()
    filled = []
    for model, proposal in (proposals.items() if isinstance(proposals, dict) else []):
        if model not in table or not isinstance(proposal, dict):
            continue
        source = proposal.get('source')
        for field in FIELDS:
            value = proposal.get(field)
            if table[model].get(field) is None and _number(value):
                table[model][field] = float(value)
                filled.append({'model': model, 'field': field, 'value': float(value),
                               'source': source[:200] if isinstance(source, str) else ''})
    if filled:
        _save(table)
    return filled
