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


async def fetch_properties(api_key: str, agency_uid: str, base_url: str) -> dict:
    url = f"{base_url}/properties"
    params = {"agencyUid": agency_uid}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=get_headers(api_key), params=params)
    return response.status_code, response.json() if response.headers.get("content-type", "").startswith("application/json") else {}


async def fetch_guests(api_key: str, agency_uid: str, base_url: str) -> tuple:
    url = f"{base_url}/guests/{agency_uid}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=get_headers(api_key))
    return response.status_code, response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
