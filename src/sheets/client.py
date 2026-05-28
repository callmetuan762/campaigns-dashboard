"""Google Sheets client for fetching Stripe payment records.

Supports three auth methods (checked in order):
  1. Service account JSON file path (GOOGLE_SERVICE_ACCOUNT_JSON_PATH) — reuses the
     same service account already used for GA4 (secrets/ga4.json).
  2. Service account JSON string (GOOGLE_SERVICE_ACCOUNT_JSON) — full JSON in an env var.
  3. OAuth token file (GOOGLE_OAUTH_TOKEN_PATH) — for interactive/personal use.

The simplest setup: share the Google Sheet with the GA4 service account email, then set:
  GOOGLE_SHEETS_SPREADSHEET_ID=<sheet_id>
  GOOGLE_SERVICE_ACCOUNT_JSON_PATH=./secrets/ga4.json

Returns normalised dicts with keys: uid, submitted_at, email, source, status, session_id.
Wraps API calls with tenacity retry (3 attempts, exponential backoff) on APIError.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import gspread
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from src.config import Settings

logger = structlog.get_logger(__name__)


def get_sheets_credentials(settings: "Settings"):
    """Factory: return an authenticated gspread client.

    Preference order:
      1. GOOGLE_SERVICE_ACCOUNT_JSON_PATH — file path to a service-account JSON key.
         Reuses the same credentials file already used for GA4 (secrets/ga4.json).
      2. GOOGLE_SERVICE_ACCOUNT_JSON — full service-account JSON as an env-var string.
      3. GOOGLE_OAUTH_TOKEN_PATH — path to a saved OAuth2 token file.

    Raises ValueError if none of the above is configured.
    """
    # Method 1: service account file path (reuse GA4 service account)
    if settings.google_service_account_json_path:
        key_path = Path(settings.google_service_account_json_path)
        sa_info = json.loads(key_path.read_text(encoding="utf-8"))
        logger.info("sheets_auth_service_account_file", path=str(key_path))
        return gspread.service_account_from_dict(sa_info)

    # Method 2: service account JSON string in env var
    if settings.google_service_account_json:
        sa_info = json.loads(settings.google_service_account_json)
        logger.info("sheets_auth_service_account_json")
        return gspread.service_account_from_dict(sa_info)

    # Method 3: OAuth token file
    if settings.google_oauth_token_path:
        logger.info("sheets_auth_oauth", path=settings.google_oauth_token_path)
        return gspread.oauth(credentials_filename=str(settings.google_oauth_token_path))

    raise ValueError(
        "No Google Sheets credentials configured. "
        "Simplest setup: set GOOGLE_SERVICE_ACCOUNT_JSON_PATH=./secrets/ga4.json "
        "and share the sheet with your GA4 service-account email."
    )


def _parse_timestamp(raw: str) -> str:
    """Normalise an ISO-8601 timestamp string to YYYY-MM-DD HH:MM:SS (no timezone suffix).

    Handles:
      - '2026-05-11T20:11:23.446Z'  -> '2026-05-11 20:11:23'
      - '2026-05-11T20:11:23Z'      -> '2026-05-11 20:11:23'
      - '2026-05-11 20:11:23'       -> '2026-05-11 20:11:23'  (already normalised)
    """
    cleaned = raw.strip()
    # Drop fractional seconds and timezone suffix, replace T separator
    if "T" in cleaned:
        cleaned = cleaned.replace("T", " ")
    if "." in cleaned:
        cleaned = cleaned.split(".")[0]
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1]
    # Truncate to 19 chars: YYYY-MM-DD HH:MM:SS
    return cleaned[:19]


@retry(
    retry=retry_if_exception_type(gspread.exceptions.APIError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def fetch_stripe_payments(spreadsheet_id: str, client: gspread.Client) -> list[dict]:
    """Fetch all rows from the Stripe payments Google Sheet.

    Parameters
    ----------
    spreadsheet_id:
        The Google Sheets spreadsheet ID (from the URL).
    client:
        An authenticated gspread client returned by ``get_sheets_credentials()``.

    Returns
    -------
    list[dict]
        Normalised rows with keys: uid, submitted_at, email, source, status, session_id.
        Rows with an empty UID are silently skipped.
    """
    logger.info("sheets_fetch_start", spreadsheet_id=spreadsheet_id)

    spreadsheet = client.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.get_worksheet(0)
    records = worksheet.get_all_records()

    rows: list[dict] = []
    skipped = 0

    for record in records:
        uid = str(record.get("UID", "")).strip()
        if not uid:
            skipped += 1
            continue

        # Prefer "ISO 8601" column (GMT+7 local time) — strip header whitespace
        # to handle trailing spaces in the sheet header row.
        iso_key = next((k for k in record if k.strip() == "ISO 8601"), None)
        raw_ts = str(record.get(iso_key, "") if iso_key else record.get("Timestamp", "")).strip()
        submitted_at = _parse_timestamp(raw_ts) if raw_ts else ""

        session_id = str(record.get("Session ID", "")).strip() or None

        rows.append(
            {
                "uid": uid,
                "submitted_at": submitted_at,
                "email": str(record.get("Email", "")).strip() or None,
                "source": str(record.get("Source", "")).strip() or None,
                "status": str(record.get("Status", "pending")).strip(),
                "session_id": session_id,
            }
        )

    logger.info(
        "sheets_fetch_done",
        spreadsheet_id=spreadsheet_id,
        rows=len(rows),
        skipped=skipped,
    )
    return rows
