"""Entrypoint do bot: configura Application e inicia polling."""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.file_server import init_storage, run_server
from src.handlers import cmd_delete, cmd_help, cmd_start, handle_message
from src.queue import run_worker

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(app: Application) -> None:
    """Chamado após a aplicação inicializar (deve ser coroutine)."""
    file_server_port = int(os.getenv("FILE_SERVER_PORT", "8080"))
    host_port_env = os.getenv("HOST_PORT", "").strip()
    url_port = int(host_port_env) if host_port_env else file_server_port
    host = (os.getenv("HOST") or "").strip()
    base_url = (os.getenv("BASE_URL") or "").strip()
    if host:
        app.bot_data["base_url"] = f"http://{host}:{url_port}"
        logger.info("URL de download (HOST): %s", app.bot_data["base_url"])
    else:
        app.bot_data["base_url"] = base_url if base_url else None

    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        raise SystemExit("Defina REDIS_URL no ambiente ou no .env para usar a fila de downloads")
    from redis.asyncio import Redis
    redis = Redis.from_url(redis_url, decode_responses=True)
    app.bot_data["redis"] = redis
    asyncio.create_task(run_worker(redis, app))
    logger.info("Bot iniciado (fila Redis ativa)")

    bot_api_base_url = (os.getenv("TELEGRAM_BOT_API_BASE_URL") or "").strip()
    app.bot_data["use_local_bot_api"] = bool(bot_api_base_url)
    local_mode_env = (os.getenv("TELEGRAM_LOCAL_MODE") or "true").strip().lower()
    app.bot_data["local_mode"] = local_mode_env not in ("0", "false", "no")
    if app.bot_data["use_local_bot_api"]:
        logger.info("Local Bot API ativo: vídeos até 2 GB serão enviados pelo bot")


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

    host = (os.getenv("HOST") or "").strip()
    base_url = (os.getenv("BASE_URL") or "").strip()
    has_download_host = bool(host or base_url)
    use_local_api = bool((os.getenv("TELEGRAM_BOT_API_BASE_URL") or "").strip())
    if has_download_host:
        port = int(os.getenv("FILE_SERVER_PORT", "8080"))
        storage_dir = Path(os.getenv("STORAGE_DIR", "/app/storage"))
        init_storage(storage_dir)
        run_server(host="0.0.0.0", port=port)
    elif not use_local_api:
        logger.warning(
            "Defina TELEGRAM_BOT_API_BASE_URL (vídeos até 2 GB) ou HOST/BASE_URL (link de download) para vídeos > 50 MB"
        )

    builder = Application.builder().token(token)
    bot_api_base_url = (os.getenv("TELEGRAM_BOT_API_BASE_URL") or "").strip()
    if bot_api_base_url:
        builder = builder.base_url(bot_api_base_url)
        local_mode_env = (os.getenv("TELEGRAM_LOCAL_MODE") or "true").strip().lower()
        if local_mode_env not in ("0", "false", "no"):
            builder = builder.local_mode(True)
    app = builder.post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_error_handler(error_handler)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
