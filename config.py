"""
Файл конфигурации для FastAPI webhook-сервера.
"""
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


CRYPTO_PAY_API_TOKEN = os.getenv("CRYPTO_PAY_API_TOKEN")
