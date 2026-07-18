"""
Data access for the Settings tab — read/write the settings.json overlay
that config.py applies on top of its hardcoded defaults. Pure data access,
no Textual import, same separation as results_data.py / memory_data.py.

Restart-required by design: config.py only reads settings.json once, at
import time, and every other module binds config values at its own import
time too (`from config import X`) — so a save here takes effect on the next
process start, not the current one. See config.py's own comment for why.
"""

import json
from dataclasses import dataclass

import config
from config import SETTINGS_KEYS, SETTINGS_PATH, HARDCODED_DEFAULTS


@dataclass
class SettingField:
    key: str        # must be a name in config.SETTINGS_KEYS
    label: str
    type: str        # "str" | "int"
    section: str


FIELDS: list[SettingField] = [
    SettingField("GHIDRA_PATH", "Ghidra analyzeHeadless path", "str", "Ghidra"),

    SettingField("LLM_PROVIDER", "Default provider", "str", "Pipeline defaults"),
    SettingField("NUM_PASSES", "Default number of passes", "int", "Pipeline defaults"),

    SettingField("ANTHROPIC_MODEL_HEAVY", "Heavy model (passes 3-4)", "str", "Anthropic"),
    SettingField("ANTHROPIC_MODEL_FAST", "Fast model (passes 1,2,5,6)", "str", "Anthropic"),
    SettingField("MAX_TOKENS", "Max tokens (Anthropic / Xiaomi)", "int", "Anthropic"),

    SettingField("XIAOMI_BASE_URL", "Base URL", "str", "Xiaomi"),
    SettingField("XIAOMI_MODEL", "Model", "str", "Xiaomi"),

    SettingField("OLLAMA_BASE_URL", "Base URL", "str", "Ollama"),
    SettingField("OLLAMA_MODEL", "Model", "str", "Ollama"),
    SettingField("OLLAMA_MAX_TOKENS", "Max tokens", "int", "Ollama"),

    SettingField("BONSAI_BASE_URL", "Base URL", "str", "Bonsai"),
    SettingField("BONSAI_MODEL", "Model", "str", "Bonsai"),
    SettingField("BONSAI_MAX_TOKENS", "Max tokens", "int", "Bonsai"),

    SettingField("AI_TIMEOUT_SECONDS", "AI call timeout (s)", "int", "Timeouts"),
    SettingField("DECOMPILER_TIMEOUT_SECONDS", "Decompiler timeout (s)", "int", "Timeouts"),
]

# Cheap drift guard: every SETTINGS_KEYS entry must have a field here (and
# vice versa) or the Settings screen and config.py's whitelist have silently
# gone out of sync.
assert {f.key for f in FIELDS} == set(SETTINGS_KEYS), (
    "tui/settings_data.py FIELDS and config.SETTINGS_KEYS have drifted apart"
)

SECTIONS = list(dict.fromkeys(f.section for f in FIELDS))  # first-seen order


def load_current_values() -> dict:
    """Current effective values (defaults, possibly overridden by settings.json)."""
    return {key: getattr(config, key) for key in SETTINGS_KEYS}


def load_hardcoded_defaults() -> dict:
    return dict(HARDCODED_DEFAULTS)


def save_overrides(values: dict) -> None:
    """
    Persist a full snapshot of every whitelisted setting. Always writes all
    of SETTINGS_KEYS (not just the ones that differ from the hardcoded
    default) — settings.json is meant to be the complete last-saved state,
    not a diff, so it stays simple to reason about.
    """
    payload = {key: values[key] for key in SETTINGS_KEYS}
    SETTINGS_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def reset_to_defaults() -> None:
    """Delete the override file — takes effect on next restart."""
    if SETTINGS_PATH.exists():
        SETTINGS_PATH.unlink()
