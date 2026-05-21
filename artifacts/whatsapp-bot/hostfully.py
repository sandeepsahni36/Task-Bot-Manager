import os
import httpx
import logging

logger = logging.getLogger(__name__)

PAGE_LIMIT = 100  # records per page for paginated requests


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


# ---------------------------------------------------------------------------
# Single-page fetchers (kept for backwards compat / internal use)
# ---------------------------------------------------------------------------

async def fetch_properties(api_key: str, agency_uid: str, base_url: str) -> tuple:
    url = f"{base_url}/properties"
    params = {"agencyUid": agency_uid}
    async with httpx.AsyncClient(timeout=15.0) as client:
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
    limit: int = PAGE_LIMIT,
) -> tuple:
    """
    Fetch a single page of leads/reservations from the Hostfully v3 API.

    NOTE: v3 ignores server-side date filters. Use fetch_all_leads() +
    client-side filtering instead.
    """
    url = f"{base_url}/leads"
    params: dict = {"agencyUid": agency_uid, "limit": limit, "offset": 0}

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

    safe_params = {k: v for k, v in params.items() if k != "agencyUid"}
    logger.info("Fetching Hostfully leads: url=%s params=%s", url, safe_params)

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=get_headers(api_key), params=params)

    ct = response.headers.get("content-type", "")
    try:
        data = response.json() if ct.startswith("application/json") else {}
    except Exception:
        data = {}

    logger.info("Hostfully leads response: status=%d", response.status_code)
    return response.status_code, data


# ---------------------------------------------------------------------------
# Paginated fetchers — always exhaust all pages
# ---------------------------------------------------------------------------

def _extract_list(data) -> list:
    """Extract the records list from a Hostfully API response (list or dict)."""
    if isinstance(data, list):
        return data
    for key in ("leads", "bookings", "reservations", "results", "items", "properties"):
        if key in data and isinstance(data[key], list):
            return data[key]
    return []


async def fetch_all_leads(
    api_key: str,
    agency_uid: str,
    base_url: str,
    property_uid: str = None,
) -> tuple[list, int]:
    """
    Paginate through all Hostfully leads using limit/offset.

    Returns:
        (all_leads: list, pages_fetched: int)

    Stops when a page returns fewer than PAGE_LIMIT records.
    Client-side date filtering must be applied by the caller — the v3 API
    ignores server-side date params.
    """
    url = f"{base_url}/leads"
    headers = get_headers(api_key)
    all_leads: list = []
    pages = 0
    offset = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params: dict = {
                "agencyUid": agency_uid,
                "limit": PAGE_LIMIT,
                "offset": offset,
            }
            if property_uid:
                params["propertyUid"] = property_uid

            logger.info(
                "fetch_all_leads: page=%d offset=%d url=%s",
                pages + 1, offset, url,
            )
            response = await client.get(url, headers=headers, params=params)
            pages += 1

            ct = response.headers.get("content-type", "")
            try:
                data = response.json() if ct.startswith("application/json") else {}
            except Exception:
                data = {}

            if response.status_code != 200:
                logger.error(
                    "fetch_all_leads: non-200 on page %d: status=%d body=%s",
                    pages, response.status_code, data,
                )
                # Return what we have so far + the error status
                return all_leads, pages

            page_records = _extract_list(data)
            all_leads.extend(page_records)

            logger.info(
                "fetch_all_leads: page=%d got=%d total_so_far=%d",
                pages, len(page_records), len(all_leads),
            )

            if len(page_records) < PAGE_LIMIT:
                # Last page reached
                break

            offset += PAGE_LIMIT

    return all_leads, pages


async def fetch_all_properties(
    api_key: str,
    agency_uid: str,
    base_url: str,
) -> tuple[list, int]:
    """
    Paginate through all Hostfully properties using limit/offset.

    The Hostfully v3 /properties endpoint ignores the ``limit`` param and
    always returns its own page size (≈20). We therefore:
      - increment offset by the actual count returned each page, not PAGE_LIMIT
      - stop when a page returns 0 records (true end)
      - use a seen-UID set to guard against infinite loops if the API wraps around
      - cap at MAX_PAGES as a hard safety limit

    Returns:
        (all_properties: list, pages_fetched: int)
    """
    MAX_PAGES = 100
    url = f"{base_url}/properties"
    headers = get_headers(api_key)
    all_props: list = []
    seen_uids: set = set()
    pages = 0
    offset = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while pages < MAX_PAGES:
            params: dict = {
                "agencyUid": agency_uid,
                "limit": PAGE_LIMIT,
                "offset": offset,
            }

            logger.info(
                "fetch_all_properties: page=%d offset=%d",
                pages + 1, offset,
            )
            response = await client.get(url, headers=headers, params=params)
            pages += 1

            ct = response.headers.get("content-type", "")
            try:
                data = response.json() if ct.startswith("application/json") else {}
            except Exception:
                data = {}

            if response.status_code != 200:
                logger.error(
                    "fetch_all_properties: non-200 on page %d: status=%d",
                    pages, response.status_code,
                )
                return all_props, pages

            page_records = _extract_list(data)

            if not page_records:
                # Empty page = genuine end of data
                break

            # Dedup guard: if all UIDs on this page already seen, the API has
            # wrapped around or is returning duplicates — stop to avoid looping.
            new_records = []
            for r in page_records:
                uid = r.get("uid") or r.get("id")
                if uid and uid in seen_uids:
                    continue
                if uid:
                    seen_uids.add(uid)
                new_records.append(r)

            if not new_records:
                logger.warning(
                    "fetch_all_properties: all %d records on page %d already seen; stopping",
                    len(page_records), pages,
                )
                break

            all_props.extend(new_records)

            logger.info(
                "fetch_all_properties: page=%d got=%d new=%d total_so_far=%d",
                pages, len(page_records), len(new_records), len(all_props),
            )

            # Increment by actual count returned (not PAGE_LIMIT) because the
            # API may ignore the limit param and return a fixed page size.
            offset += len(page_records)

    return all_props, pages


# fetch_bookings is an alias for fetch_leads — the /bookings endpoint does not
# exist in Hostfully v3; /leads is the correct reservations endpoint.
fetch_bookings = fetch_leads
