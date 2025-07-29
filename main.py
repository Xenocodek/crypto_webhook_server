"""
Основной файл FastAPI webhook-сервера для Crypto Pay.
"""
import logging
import hmac
import hashlib
import json
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException, Header
from pydantic import BaseModel, Field
import requests # Для отправки сообщений через Telegram Bot API

import config # Файл конфигурации webhook-сервера

# Настройка логирования
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Crypto Pay Webhook Server",
    description="Принимает уведомления от Crypto Pay и уведомляет пользователей через Telegram.",
    version="1.0.0"
)

# --- Модели данных Pydantic для входящих вебхуков ---
# Структура вебхука от Crypto Pay может варьироваться.
# Это примерная модель, основанная на общих ожиданиях.
# Обратитесь к официальной документации Crypto Pay для точной структуры.
class InvoicePayload(BaseModel):
    invoice_id: int
    status: str
    amount: Optional[str] = None # Сумма в криптовалюте
    asset: Optional[str] = None
    fee: Optional[str] = None
    fiat_amount: Optional[str] = None # Сумма в фиате, если была указана
    fiat_currency: Optional[str] = None
    description: Optional[str] = None
    custom_payload: Optional[str] = Field(None, alias="payload") # Поле payload из CryptoPay
    # ... другие поля, которые может присылать Crypto Pay

class CryptoPayWebhook(BaseModel):
    update_id: int
    update_type: str # Например, "invoice_paid"
    request_date: str # ISO 8601 format date string
    payload: InvoicePayload

# --- Вспомогательные функции ---
async def send_telegram_message(chat_id: int, text: str):
    """Отправляет сообщение пользователю через Telegram Bot API."""
    telegram_api_url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(telegram_api_url, json=payload, timeout=5)
        response.raise_for_status()
        logger.info(f"Successfully sent message to chat_id {chat_id}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram message to chat_id {chat_id}: {e}")
        logger.error(f"Response content: {response.content if 'response' in locals() else 'No response'}")
        return False

def verify_signature(request_body: bytes, signature_header: Optional[str]) -> bool:
    """
    Проверяет подпись вебхука от Crypto Pay.
    Это ОБЯЗАТЕЛЬНО для безопасности, чтобы убедиться, что запрос пришел от Crypto Pay.
    Метод проверки зависит от того, как Crypto Pay формирует подпись.
    Обычно это HMAC-SHA256 от тела запроса с использованием вашего секретного токена.
    """
    if not config.CRYPTO_PAY_WEBHOOK_SECRET or not signature_header:
        logger.warning("Webhook secret or signature header not configured/provided. Skipping signature verification. THIS IS INSECURE.")
        # В рабочей среде здесь должна быть ошибка или строгая проверка
        return True # Временно разрешаем без подписи для упрощения, но это небезопасно

    try:
        # Пример: Crypto Pay может отправлять подпись в заголовке 'Crypto-Pay-Signature'
        # Тело запроса должно быть в том виде, в котором оно было при формировании подписи
        key = config.CRYPTO_PAY_WEBHOOK_SECRET.encode("utf-8")
        hasher = hmac.new(key, request_body, hashlib.sha256)
        calculated_signature = hasher.hexdigest()

        if hmac.compare_digest(calculated_signature, signature_header):
            logger.info("Webhook signature verified successfully.")
            return True
        else:
            logger.warning(f"Webhook signature mismatch. Calculated: {calculated_signature}, Received: {signature_header}")
            return False
    except Exception as e:
        logger.error(f"Error during webhook signature verification: {e}")
        return False

# --- Эндпоинт для вебхуков от Crypto Pay ---
@app.post("/webhook/crypto_pay")
async def crypto_pay_webhook_handler(request: Request, crypto_pay_signature: Optional[str] = Header(None)):
    """
    Обрабатывает входящие вебхуки от Crypto Pay.
    """
    raw_body = await request.body()
    logger.info(f"Received webhook. Headers: {request.headers}")
    logger.info(f"Received webhook. Body: {raw_body.decode()}")

    # 1. Проверка подписи (ВАЖНО ДЛЯ БЕЗОПАСНОСТИ!)
    # if not verify_signature(raw_body, crypto_pay_signature):
    #     logger.error("Webhook signature verification failed.")
    #     raise HTTPException(status_code=403, detail="Invalid signature")
    # Пока проверка подписи закомментирована, т.к. требует точного знания алгоритма CryptoPay
    # и наличия CRYPTO_PAY_WEBHOOK_SECRET. Пользователь должен будет это настроить.
    logger.warning("Signature verification is currently lenient. Ensure CRYPTO_PAY_WEBHOOK_SECRET is set and verify_signature is enabled and correct for production.")

    try:
        webhook_data_dict = json.loads(raw_body.decode())
        webhook_data = CryptoPayWebhook(**webhook_data_dict)
        logger.info(f"Parsed webhook data: {webhook_data.model_dump_json(indent=2)}")
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}. Body: {raw_body.decode()}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except Exception as e: # Pydantic ValidationError
        logger.error(f"Webhook payload validation error: {e}. Body: {raw_body.decode()}")
        raise HTTPException(status_code=400, detail=f"Invalid payload structure: {e}")

    invoice_payload = webhook_data.payload
    invoice_id = invoice_payload.invoice_id
    status = invoice_payload.status
    amount = invoice_payload.amount
    asset = invoice_payload.asset
    custom_payload_str = invoice_payload.custom_payload

    chat_id: Optional[int] = None
    if custom_payload_str:
        try:
            # Ожидаем payload в формате "user_id:CHAT_ID"
            if "user_id:" in custom_payload_str:
                chat_id = int(custom_payload_str.split("user_id:")[-1])
                logger.info(f"Extracted chat_id {chat_id} from custom_payload.")
        except Exception as e:
            logger.error(f"Could not parse chat_id from custom_payload 	'{custom_payload_str}	': {e}")

    if not chat_id:
        # Если chat_id не удалось извлечь, мы не можем уведомить пользователя.
        # Это может произойти, если custom_payload не был установлен или имеет другой формат.
        # В боте `crypto_pay_service.py` мы сохраняем `temporary_invoice_storage`.
        # Если бы это был монолит, мы бы могли его здесь прочитать.
        # В микросервисной архитектуре, `chat_id` ДОЛЖЕН быть в `custom_payload`.
        logger.error(f"chat_id not found for invoice_id {invoice_id}. Cannot notify user.")
        # Можно вернуть 200 OK, чтобы CryptoPay не повторял отправку, но залогировать проблему.
        return {"status": "error", "message": "chat_id not found in payload, notification skipped"}

    notification_text = f"Статус вашего платежа (Инвойс ID: {invoice_id}) обновлен: <b>{status.upper()}</b>."
    if status == "paid":
        notification_text = f"✅ Ваш платеж (Инвойс ID: {invoice_id}) успешно получен!"
        if amount and asset:
            notification_text = f"✅ Ваш платеж на сумму {amount} {asset} (Инвойс ID: {invoice_id}) успешно получен!"
        # Здесь бот должен был бы удалить инвойс из своего temporary_invoice_storage
        # crypto_pay_service.remove_invoice_from_storage(invoice_id) # Недоступно напрямую
    elif status == "expired":
        notification_text = f"⌛️ Срок действия вашего платежа (Инвойс ID: {invoice_id}) истек."
        # crypto_pay_service.remove_invoice_from_storage(invoice_id) # Недоступно напрямую

    await send_telegram_message(chat_id, notification_text)

    # CryptoPay ожидает ответ 200 OK, если вебхук успешно обработан.
    return {"status": "ok", "message": "Webhook processed"}

# Для локального запуска (не используется Vercel)
if __name__ == "__main__":
    import uvicorn
    logger.info("Starting webhook server locally on http://localhost:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001)

