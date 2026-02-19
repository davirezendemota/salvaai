"""Entrypoint do bot: configura Application e inicia polling."""

import asyncio
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.downloader import COOKIES_FILE_DEFAULT
from src.handlers import cmd_delete, cmd_help, cmd_start, handle_document, handle_message
from src.queue import run_worker

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(app: Application) -> None:
    """Chamado após a aplicação inicializar (deve ser coroutine)."""
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        raise SystemExit("Defina REDIS_URL no ambiente ou no .env para usar a fila de downloads")
    from redis.asyncio import Redis
    redis = Redis.from_url(redis_url, decode_responses=True)
    app.bot_data["redis"] = redis
    app.bot_data["cookies_file"] = COOKIES_FILE_DEFAULT
    _raw = (os.getenv("TELEGRAM_ALLOWED_USER_ID") or "").strip()
    try:
        app.bot_data["allowed_user_id"] = int(_raw) if _raw else None
    except ValueError:
        app.bot_data["allowed_user_id"] = None
    logger.info("Cookies do Instagram (para envio pelo Telegram): %s", COOKIES_FILE_DEFAULT)
    worker_task = asyncio.create_task(run_worker(redis, app))
    app.bot_data["worker_task"] = worker_task
    logger.info("Bot iniciado (fila Redis ativa)")


async def post_shutdown(app: Application) -> None:
    """Cancela o worker da fila e fecha Redis para shutdown limpo."""
    worker_task = app.bot_data.get("worker_task")
    redis = app.bot_data.get("redis")
    if worker_task and not worker_task.done():
        worker_task.cancel()
        try:
            await asyncio.wait_for(worker_task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    if redis:
        await redis.aclose()


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Log de exceções para não derrubar o processo."""
    logger.exception("Exceção ao processar update: %s", context.error)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Defina TELEGRAM_BOT_TOKEN no ambiente ou no .env")
    if not (os.getenv("REDIS_URL") or "").strip():
        raise SystemExit("Defina REDIS_URL no ambiente ou no .env para a fila de downloads")

    app = (
    Application.builder()
    .token(token)
    .post_init(post_init)
    .post_shutdown(post_shutdown)
    .build()
)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_error_handler(error_handler)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
