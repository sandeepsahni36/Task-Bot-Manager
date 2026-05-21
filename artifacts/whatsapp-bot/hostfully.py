import os
import httpx
import logging

logger = logging.getLogger(__name__)

# Hostfully v3 cursor pagination uses _limit and _paging._nextCursor.
# Pass _limit on every request; pass _cursor on requests after the first.
# Never pass _cursor on the first request — the properties endpoint returns a
# 500 Internal Server Error if _cursor is present on page 1.
CURSOR_LIMIT = 100


def get_hostfully_config() -> tuple[str, str, str]:
    api_key = os.getenv("HOSTFULLY_API_KEY")
    agency_uid = os.getenv("HOSTFULLY_AGENCY_UID")
    base_url = os.getenv("HOSTFULLY_BASE_URL", "https://api.hostfully.com/v2")

    if not api_key:
        raise ValueError("HOSTFULLY_API_KEY is not set")
    if not agency_uid:
        raise ValueError("HOSTFULLY_AGENCY_UID is not set")

    return api_key, agency_uid, base_url.rstrip("/")


def get_headers(api_key: str) -> dict:
    return {
        "X-HOSTFULLY-APIKEY": api_key,
        "Accept": "application/json",
    }


def _extract_next_cursor(data: dict) -> str | None:
    """
    Extract the next-page cursor from a Hostfully v3 response.
    Looks in: _paging._nextCursor, _paging.nextCursor, nextCursor, next_cursor.
    Returns None if this is the last page.
    """
    if not isinstance(data, dict):
        return None
    paging = data.get("_paging") or data.get("paging") or {}
    cursor = (
        paging.get("_nextCursor") or paging.get("nextCursor")
        or data.get("nextCursor") or data.get("next_cursor")
        or data.get("_cursor")
    )
    return cursor or None


def _extract_records(data, list_keys: list[str]) -> list:
    """
    Pull the records list out of a Hostfully v3 response dict or raw list.
    """
    if isinstance(data, list):
        return data
    for key in list_keys:
        val = data.get(key)
        if isinstance(val, list):
            return val
    return []


def _paging_info(data: dict) -> dict:
    """Return the raw _paging and _metadata fields for diagnostics."""
    if not isinstance(data, dict):
        return {}
    return {
        "_paging": data.get("_paging"),
        "_metadata": data.get("_metadata"),
    }


# ---------------------------------------------------------------------------
# Single-page fetchers (kept for backwards compat)
# ---------------------------------------------------------------------------

async def fetch_properties(api_key: str, agency_uid: str, base_url: str) -> tuple:
    url = f"{base_url}/properties"
    params = {"agencyUid": agency_uid, "_limit": CURSOR_LIMIT}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=get_headers(api_key), params=params)
    ct = response.headers.get("content-type", "")
    return response.status_code, response.json() if ct.startswith("application/json") else {}


async def fetch_guests(api_key: str, agency_uid: str, base_url: str) -> tuple:
    url = f"{base_url}/guests/{agency_uid}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=get_headers(api_key))
    ct = response.headers.get("content-type", "")
    return response.status_code, response.json() if ct.startswith("application/json") else {}


async def fetch_leads(
    api_key: str,
    agency_uid: str,
    base_url: str,
    checkout_from: str = None,
    checkout_to: str = None,
    checkin_from: str = None,
    checkin_to: str = None,
    property_uid: str = None,
    limit: int = CURSOR_LIMIT,
) -> tuple:
    """
    Fetch a single page of leads/reservations (first page only).
    For all pages use fetch_all_leads().
    """
    url = f"{base_url}/leads"
    params: dict = {"agencyUid": agency_uid, "_limit": limit}

    if checkout_from:
        params["checkOutAfter"] = checkout_from
    if checkout_to:
        params["checkOutBefore"] = checkout_to
    if checkin_from:
        params["checkInAfter"] = checkin_from
    if checkin_to:
        params["checkInBefore"] = checkin_to
    if property_uid:
        params["propertyUid"] = property_uid

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=get_headers(api_key), params=params)

    ct = response.headers.get("content-type", "")
    try:
        data = response.json() if ct.startswith("application/json") else {}
    except Exception:
        data = {}

    logger.info("Hostfully leads (single page): status=%d", response.status_code)
    return response.status_code, data


# ---------------------------------------------------------------------------
# Cursor-paginated fetchers — exhaust all pages via _nextCursor
# ---------------------------------------------------------------------------

async def fetch_all_leads(
    api_key: str,
    agency_uid: str,
    base_url: str,
    property_uid: str = None,
) -> tuple[list, int, dict]:
    """
    Paginate through ALL Hostfully leads using _limit / _nextCursor.

    Returns:
        (all_leads, pages_fetched, last_page_raw_response)

    - First request: no _cursor param (passing _cursor on page 1 can crash some endpoints)
    - Subsequent requests: _cursor=<_nextCursor from previous response>
    - Stops when _nextCursor is null/absent or page returns 0 records
    """
    url = f"{base_url}/leads"
    headers = get_headers(api_key)
    all_leads: list = []
    pages = 0
    cursor: str | None = None
    last_raw: dict = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params: dict = {"agencyUid": agency_uid, "_limit": CURSOR_LIMIT}
            if cursor:
                params["_cursor"] = cursor
            if property_uid:
                params["propertyUid"] = property_uid

            logger.info("fetch_all_leads: page=%d cursor=%s", pages + 1, cursor and cursor[:20])
            response = await client.get(url, headers=headers, params=params)
            pages += 1

            ct = response.headers.get("content-type", "")
            try:
                data = response.json() if ct.startswith("application/json") else {}
            except Exception:
                data = {}

            last_raw = data

            if response.status_code != 200:
                logger.error("fetch_all_leads: non-200 page=%d status=%d", pages, response.status_code)
                break

            page_records = _extract_records(data, ["leads", "bookings", "reservations", "results", "items"])
            all_leads.extend(page_records)

            logger.info("fetch_all_leads: page=%d got=%d total=%d", pages, len(page_records), len(all_leads))

            if not page_records:
                break

            cursor = _extract_next_cursor(data)
            if not cursor:
                break

    return all_leads, pages, last_raw


async def fetch_all_properties(
    api_key: str,
    agency_uid: str,
    base_url: str,
) -> tuple[list, int, dict]:
    """
    Paginate through ALL Hostfully properties using _limit / _nextCursor.

    Returns:
        (all_properties, pages_fetched, last_page_raw_response)

    IMPORTANT: Do NOT pass _cursor on the first request — the v3 properties
    endpoint returns 500 Internal Server Error if _cursor is present on page 1.
    """
    url = f"{base_url}/properties"
    headers = get_headers(api_key)
    all_props: list = []
    pages = 0
    cursor: str | None = None
    last_raw: dict = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params: dict = {"agencyUid": agency_uid, "_limit": CURSOR_LIMIT}
            if cursor:
                params["_cursor"] = cursor

            logger.info("fetch_all_properties: page=%d cursor=%s", pages + 1, cursor and cursor[:20])
            response = await client.get(url, headers=headers, params=params)
            pages += 1

            ct = response.headers.get("content-type", "")
            try:
                data = response.json() if ct.startswith("application/json") else {}
            except Exception:
                data = {}

            last_raw = data

            if response.status_code != 200:
                logger.error("fetch_all_properties: non-200 page=%d status=%d", pages, response.status_code)
                break

            page_records = _extract_records(data, ["properties", "results", "items"])
            all_props.extend(page_records)

            logger.info("fetch_all_properties: page=%d got=%d total=%d", pages, len(page_records), len(all_props))

            if not page_records:
                break

            cursor = _extract_next_cursor(data)
            if not cursor:
                break

    return all_props, pages, last_raw


# fetch_bookings is an alias for fetch_leads — /bookings does not exist in Hostfully v3.
fetch_bookings = fetch_leads
