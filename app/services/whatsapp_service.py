import httpx

from app.config import settings


def build_whatsapp_reply(phone_number: str, text: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "text",
        "text": {"body": text},
    }


def build_whatsapp_image(phone_number: str, image_url: str, caption: str = "") -> dict:
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "image",
        "image": {"link": image_url},
    }
    if caption:
        payload["image"]["caption"] = caption
    return payload


async def send_whatsapp_message(phone_number: str, text: str) -> dict:
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        return {"sent": False, "reason": "WhatsApp credentials missing", "payload": build_whatsapp_reply(phone_number, text)}

    url = f"https://graph.facebook.com/v21.0/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    payload = build_whatsapp_reply(phone_number, text)

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, headers=headers, json=payload)
        return {"sent": response.is_success, "status_code": response.status_code, "body": response.text}


async def send_whatsapp_image(phone_number: str, image_url: str, caption: str = "") -> dict:
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        return {"sent": False, "reason": "WhatsApp credentials missing", "payload": build_whatsapp_image(phone_number, image_url, caption)}

    url = f"https://graph.facebook.com/v21.0/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    payload = build_whatsapp_image(phone_number, image_url, caption)

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, headers=headers, json=payload)
        return {"sent": response.is_success, "status_code": response.status_code, "body": response.text}
