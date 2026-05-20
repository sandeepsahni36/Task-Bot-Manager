import os
import httpx
import logging

logger = logging.getLogger(__name__)


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
    limit: int = 200,
) -> tuple:
    """
    Fetch leads/reservations from the Hostfully v2 API.

    Hostfully uses /leads for bookings/reservations. The /bookings endpoint
    is not supported and returns "Unknown api: bookings".

    Date filter params:
      checkOutAfter / checkOutBefore  — filter by checkout date
      checkInAfter  / checkInBefore   — filter by check-in date
      propertyUid                     — filter by property
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


# fetch_bookings is an alias for fetch_leads — the /bookings endpoint does not
# exist in Hostfully v2; /leads is the correct reservations endpoint.
fetch_bookings = fetch_leads
