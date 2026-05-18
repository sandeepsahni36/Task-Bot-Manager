import os
import logging
from supabase import create_client, Client

logger = logging.getLogger(__name__)
_client: Client | None = None


def get_supabase_client() -> Client:
    """Return the singleton Supabase client, initialised from env vars."""
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must both be set. "
                "Add them as Replit Secrets before starting the server."
            )
        _client = create_client(url, key)
        logger.info("Supabase client initialised: %s", url)
    return _client


def get_supabase_dep():
    """FastAPI dependency that yields the Supabase client."""
    yield get_supabase_client()
