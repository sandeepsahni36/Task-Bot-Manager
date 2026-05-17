import os
import httpx
import logging

logger = logging.getLogger(__name__)

WHATSAPP_API_BASE = "https://graph.facebook.com/v19.0"


def get_access_token() -> str:
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    if not token:
        raise ValueError("WHATSAPP_ACCESS_TOKEN is not set")
    return token


def get_phone_number_id() -> str:
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not phone_number_id:
        raise ValueError("WHATSAPP_PHONE_NUMBER_ID is not set")
    return phone_number_id


async def send_template_message(
    to: str,
    staff_name: str,
    property_name: str,
    task_description: str,
    due_time: str,
) -> dict:
    phone_number_id = get_phone_number_id()
    access_token = get_access_token()

    url = f"{WHATSAPP_API_BASE}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": "hello_world",
            "language": {"code": "en_US"},
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        response_data = response.json()

    if response.status_code not in (200, 201):
        logger.error(
            "WhatsApp API error: status=%d body=%s",
            response.status_code,
            response_data,
        )
        raise ValueError(
            f"WhatsApp API returned {response.status_code}: {response_data}"
        )

    logger.info("WhatsApp template message sent to %s: %s", to, response_data)
    return response_data
