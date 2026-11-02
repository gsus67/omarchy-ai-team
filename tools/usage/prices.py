"""Price table for the estimated cost of tokens (USD per million tokens).

Claude prices come from Anthropic's published rates; the cache write column is
1.25x the input price (the 5-minute TTL rate). Codex models have no verified
public price, so they stay ``None``: they are shown as unpriced, never guessed.
The user edits ~/.config/omarchy/agents/prices.json to fill them in.
"""

import copy
import json
import os
import tempfile
from pathlib import Path

KINDS = ('input', 'output', 'cacheRead', 'cacheWrite')


def _row(input_, output, cache_read, cache_write):
    return {'input': input_, 'output': output, 'cacheRead': cache_read, 'cacheWrite': cache_write}


DEFAULT_PRICES = {
    'claude-opus-5-5': _row(4.00, 20.00, 0.20, 5.00),
    'claude-sonnet-5-5': _row(2.00, 10.00, 0.20, 2.50),
    'claude-haiku-5-5': _row(0.10, 0.50, 0.01, 0.125),
    'claude-fable-5-1': _row(10.00, 50.00, 0.25, 12.50),
    **{model: _row(None, None, None, None) for model in (
        'gpt-6-sol', 'gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra', 'gpt-6.1-terra', 'codex-auto-review')},
}


def _write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.prices.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write('\n')
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_prices(path):
    """Defaults merged with the user's file; the file is created if missing, never rewritten if broken."""
    path = Path(path)
    defaults = copy.deepcopy(DEFAULT_PRICES)
    if not path.exists():
        _write_private(path, defaults)
        return defaults
    try:
        user = json.loads(path.read_text())
    except (OSError, ValueError):
        return defaults
    if not isinstance(user, dict):
        return defaults
    for model, row in user.items():
        if isinstance(row, dict):
            defaults[model] = {kind: row.get(kind) for kind in KINDS}
    return defaults


def cost_usd(prices, model, kinds):
    """Cost of ``kinds`` (tokens per kind) for ``model``, or None when any needed price is unknown."""
    row = prices.get(model)
    if not row or all(row.get(kind) is None for kind in KINDS):
        return None
    total = 0.0
    for kind in KINDS:
        tokens = kinds.get(kind, 0)
        if not tokens:
            continue
        price = row.get(kind)
        if price is None:
            return None
        total += tokens * price / 1_000_000
    return total
