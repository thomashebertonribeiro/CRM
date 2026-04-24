"""
Prospector API — Settings Service

Handles reading, writing, masking, and applying API configuration settings.
Follows BACK-05 (Security): keys are masked in responses, stored in plain text
with security depending on filesystem permissions.
"""
import os
import json
import tempfile
import importlib

from app.config import settings as _settings_module
from app.config.settings import DATA_DIR
from app.models.errors import ValidationError, InternalError

# ─── Constants ───
SETTINGS_FILE = os.path.join(DATA_DIR, "api_settings.json")

# Fields that are API keys and should be masked in responses
KEY_FIELDS = {"SERPER_KEY", "OLLAMA_KEY"}

# All editable settings fields
EDITABLE_FIELDS = {"SERPER_KEY", "OLLAMA_KEY", "OLLAMA_BASE", "OLLAMA_MODEL", "OLLAMA_FALLBACKS"}

MAX_FIELD_LENGTH = 500


def mask_key(key: str) -> str:
    """
    Returns first 4 + '...' + last 4 chars for keys with len >= 8.
    Returns '' for shorter keys.
    """
    if len(key) < 8:
        return ""
    return key[:4] + "..." + key[-4:]


def load_settings_file() -> dict:
    """
    Reads DATA_DIR/api_settings.json.
    Returns {} if the file does not exist.
    """
    if not os.path.exists(SETTINGS_FILE):
        return {}
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_settings_file(data: dict) -> None:
    """
    Persists DATA_DIR/api_settings.json atomically using tempfile + os.replace().
    Raises InternalError if the file cannot be written.
    """
    dir_path = os.path.dirname(SETTINGS_FILE)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=dir_path,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, SETTINGS_FILE)
    except OSError as exc:
        raise InternalError(f"Failed to write settings file: {exc}") from exc


def apply_settings_to_module(data: dict) -> None:
    """
    Overwrites attributes of app.config.settings in memory with values from data.
    Only updates known editable fields that are present in data.
    """
    # Reload the module reference to ensure we have the live module object
    mod = importlib.import_module("app.config.settings")
    for field in EDITABLE_FIELDS:
        if field in data:
            setattr(mod, field, data[field])


def get_current_settings() -> dict:
    """
    Returns a dict with current values from the settings module.
    API key fields (SERPER_KEY, OLLAMA_KEY) are masked.
    """
    mod = importlib.import_module("app.config.settings")
    result = {}
    for field in EDITABLE_FIELDS:
        value = getattr(mod, field, "")
        # OLLAMA_FALLBACKS is stored as a list in the module; convert to string
        if isinstance(value, list):
            value = ",".join(value)
        if field in KEY_FIELDS:
            result[field] = mask_key(value) if value else ""
        else:
            result[field] = value
    return result


def _validate_payload(payload: dict) -> None:
    """
    Validates all fields in the payload.
    Raises ValidationError if any field exceeds 500 chars or contains
    control characters (ASCII < 32, except \\t and \\n).
    """
    for field, value in payload.items():
        if not isinstance(value, str):
            continue
        if len(value) > MAX_FIELD_LENGTH:
            raise ValidationError(
                f"Field '{field}' exceeds maximum length of {MAX_FIELD_LENGTH} characters.",
                details=[{"field": field, "max_length": MAX_FIELD_LENGTH}],
            )
        for char in value:
            code = ord(char)
            if code < 32 and char not in ("\t", "\n"):
                raise ValidationError(
                    f"Field '{field}' contains invalid control character (ASCII {code}).",
                    details=[{"field": field, "invalid_char_code": code}],
                )


def update_settings(payload: dict) -> dict:
    """
    Validates, persists, and applies new settings values.

    - Rejects fields with length > 500 or control characters (raises ValidationError).
    - Preserves existing key values when the submitted field is an empty string.
    - Returns a dict with the saved values (API keys masked).
    """
    # Validate all incoming fields
    _validate_payload(payload)

    # Load existing persisted settings to preserve keys when blank is submitted
    existing = load_settings_file()

    # Build the new settings dict
    new_settings = {}
    for field in EDITABLE_FIELDS:
        incoming = payload.get(field, None)

        if incoming is None:
            # Field not in payload — keep existing value (or nothing)
            if field in existing:
                new_settings[field] = existing[field]
        elif incoming == "" and field in KEY_FIELDS:
            # Blank key field — preserve existing value
            if field in existing and existing[field]:
                new_settings[field] = existing[field]
            # If no existing value, simply omit (no key stored)
        else:
            # Non-blank value — use the new value
            new_settings[field] = incoming

    # Persist atomically
    save_settings_file(new_settings)

    # Apply to module in memory
    apply_settings_to_module(new_settings)

    # Return masked representation
    result = {}
    for field in EDITABLE_FIELDS:
        value = new_settings.get(field, "")
        if field in KEY_FIELDS:
            result[field] = mask_key(value) if value else ""
        else:
            result[field] = value

    return result
