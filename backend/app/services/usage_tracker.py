"""
Prospector API — Usage Tracker Service

Records and aggregates daily API call counts for external services.
Follows Requirements 3.x and 4.x: atomic persistence, UTC-3 timezone,
silent I/O error handling.
"""
import os
import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone

from app.config.settings import DATA_DIR

# ─── Constants ───
SERVICES = ['serper', 'ollama', 'brasilapi', 'maps']
USAGE_FILE = os.path.join(DATA_DIR, "api_usage.json")

logger = logging.getLogger(__name__)


def _today_brasilia() -> str:
    """
    Returns the current date in UTC-3 (Brasília time) as 'YYYY-MM-DD'.
    Uses only stdlib datetime + timedelta — no external dependencies.
    """
    utc_now = datetime.now(tz=timezone.utc)
    brasilia_now = utc_now - timedelta(hours=3)
    return brasilia_now.strftime("%Y-%m-%d")


def _load_usage_file() -> dict:
    """
    Reads DATA_DIR/api_usage.json.
    Returns {} if the file does not exist.
    If the file is corrupted (invalid JSON), logs a warning and returns {}.
    """
    if not os.path.exists(USAGE_FILE):
        return {}
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.warning(
            "api_usage.json is corrupted (invalid JSON). Resetting to empty state."
        )
        return {}


def _save_usage_file(data: dict) -> None:
    """
    Persists DATA_DIR/api_usage.json atomically using tempfile + os.replace().
    Raises OSError on write failure (caller is responsible for handling).
    """
    dir_path = os.path.dirname(USAGE_FILE)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=dir_path,
        delete=False,
        suffix=".tmp",
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
    os.replace(tmp_path, USAGE_FILE)


def track(service: str) -> None:
    """
    Increments the daily counter for the given service atomically.
    Silences all I/O errors — logs them but never raises, so the caller
    (external API call) is never interrupted by tracking failures.

    Req 3.1–3.4: increments counter for serper/ollama/brasilapi/maps.
    Req 3.5: uses UTC-3 date.
    Req 3.6: atomic write.
    Req 3.7: silences I/O errors.
    """
    if service not in SERVICES:
        logger.warning("track() called with unknown service: %r", service)
        return

    try:
        today = _today_brasilia()
        data = _load_usage_file()

        # Ensure the service key exists
        if service not in data:
            data[service] = {}

        # Increment the counter for today
        data[service][today] = data[service].get(today, 0) + 1

        _save_usage_file(data)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Usage tracker failed to record call for service %r: %s",
            service,
            exc,
        )


def get_usage(days: int = 7) -> dict:
    """
    Returns usage data for the last `days` days.

    Return format:
    {
        "today": {"serper": N, "ollama": N, "brasilapi": N, "maps": N},
        "history": [
            {"date": "YYYY-MM-DD", "serper": N, "ollama": N, "brasilapi": N, "maps": N},
            ...  # exactly `days` entries, oldest first, zeros for missing days
        ]
    }

    Req 4.1: returns last 7 days per service.
    Req 4.2: returns today's total per service separately.
    Req 4.6: returns zero for days with no data (never omits a date).
    """
    data = _load_usage_file()
    today = _today_brasilia()

    # Build the list of dates: from (today - days + 1) to today, inclusive
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    date_range = [
        (today_dt - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        for i in range(days)
    ]

    # Build history list (oldest → newest), zero-filling missing days
    history = []
    for date_str in date_range:
        entry = {"date": date_str}
        for svc in SERVICES:
            entry[svc] = data.get(svc, {}).get(date_str, 0)
        history.append(entry)

    # Build today's totals
    today_totals = {}
    for svc in SERVICES:
        today_totals[svc] = data.get(svc, {}).get(today, 0)

    return {
        "today": today_totals,
        "history": history,
    }
