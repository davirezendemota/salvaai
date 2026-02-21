"""Entrypoint do bot: configura Application e inicia polling."""

import asyncio
import logging
import os
import threading

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.db.session import create_all_tables, seed_plans_if_empty, seed_test_balance_if_set
from src.downloader import COOKIES_FILE_DEFAULT
from src.handlers import (
    cmd_comprar,
    cmd_delete,
    cmd_help,
    cmd_historico,
    cmd_planos,
    cmd_saldo,
    cmd_start,
    cmd_whitelist,
    handle_document,
    handle_message,
)
from src.payments.service import PaymentService
from src.queue import run_worker
from src.webhook import app as webhook_app

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


# Não emite logs de requisição HTTP do httpx (evita log de getUpdates a cada polling)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _run_webhook_server() -> None:
    import uvicorn
    port = int(os.getenv("WEBHOOK_PORT", "8080"))
    uvicorn.run(webhook_app, host="0.0.0.0", port=port, log_level="warning")


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

    create_all_tables()
    seed_plans_if_empty()
    seed_test_balance_if_set()
    app.bot_data["payment_service"] = PaymentService()
    _raw_whitelist = (os.getenv("TELEGRAM_WHITELIST_USER_IDS") or "").strip()
    if _raw_whitelist:
        svc = app.bot_data["payment_service"]
        for part in _raw_whitelist.split(","):
            part = part.strip()
            if part:
                try:
                    uid = int(part)
                    svc.whitelist_add(uid, reason="env")
                except ValueError:
                    pass

    webhook_thread = threading.Thread(target=_run_webhook_server, daemon=True)
    webhook_thread.start()
    logger.info("Webhook de pagamentos iniciado (porta %s)", os.getenv("WEBHOOK_PORT", "8080"))

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
    app.add_handler(CommandHandler("saldo", cmd_saldo))
    app.add_handler(CommandHandler("historico", cmd_historico))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("planos", cmd_planos))
    app.add_handler(CommandHandler("comprar", cmd_comprar))
    app.add_handler(CommandHandler("whitelist", cmd_whitelist))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_error_handler(error_handler)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
