import requests

from config import TELEGRAM_TOKEN


def enviar(chat_id, texto, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": texto}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    r = requests.post(url, json=payload, timeout=15)
    if r.status_code != 200 and parse_mode:
        requests.post(url, json={"chat_id": chat_id, "text": texto}, timeout=15)


def enviar_largo(chat_id, texto, parse_mode=None):
    for i in range(0, len(texto), 4000):
        enviar(chat_id, texto[i : i + 4000], parse_mode=parse_mode)


def enviar_foto(chat_id, img_bytes, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    requests.post(
        url,
        data={"chat_id": chat_id, "caption": caption[:1024]},
        files={"photo": ("wb.png", img_bytes, "image/png")},
        timeout=30,
    )