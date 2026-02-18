"""Fila de downloads com Redis: enfileiramento e worker que processa em background."""

import asyncio
import json
import logging
import os
import shutil
import uuid
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING

from redis.asyncio import Redis

if TYPE_CHECKING:
    from telegram import Bot
    from telegram.ext import Application

from src.downloader import NoVideoInPostError, download_video, DownloadResult
from src.file_server import save_for_download
from src.video_utils import get_video_dimensions

logger = logging.getLogger(__name__)

QUEUE_KEY = "instagram_bot:download_queue"
TELEGRAM_CAPTION_MAX_LENGTH = 1024
TELEGRAM_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
TELEGRAM_LOCAL_MAX_FILE_SIZE_BYTES = 2000 * 1024 * 1024  # 2 GB com Local Bot API
DOWNLOADS_PER_DAY_LIMIT = 10
DAILY_KEY_PREFIX = "instagram_bot:daily"
DAILY_KEY_TTL_SECONDS = 86400 * 2  # 2 dias para expirar a chave do dia


def _daily_key(chat_id: int) -> str:
    """Chave Redis para contagem de downloads do chat no dia (UTC)."""
    from datetime import datetime
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"{DAILY_KEY_PREFIX}:{chat_id}:{today}"


async def get_daily_download_count(redis: Redis, chat_id: int) -> int:
    """Retorna quantos downloads o chat já fez hoje (UTC)."""
    try:
        n = await redis.get(_daily_key(chat_id))
        return int(n) if n else 0
    except Exception as e:
        logger.warning("Erro ao obter contagem diária para chat_id=%s: %s", chat_id, e)
        return 0


async def increment_daily_download_count(redis: Redis, chat_id: int) -> None:
    """Incrementa a contagem de downloads do chat hoje e define TTL na chave."""
    key = _daily_key(chat_id)
    try:
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, DAILY_KEY_TTL_SECONDS)
        await pipe.execute()
    except Exception as e:
        logger.warning("Erro ao incrementar contagem diária para chat_id=%s: %s", chat_id, e)


async def can_download_today(redis: Redis, chat_id: int) -> bool:
    """Verifica se o chat ainda pode baixar hoje (limite de DOWNLOADS_PER_DAY_LIMIT)."""
    return await get_daily_download_count(redis, chat_id) < DOWNLOADS_PER_DAY_LIMIT


def _ensure_file_removed(path: Path | None) -> None:
    """Remove o arquivo e o diretório pai se estiver vazio."""
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
        parent = path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as e:
        logger.warning("Erro ao remover arquivo temporário %s: %s", path, e)


async def push_job(redis: Redis, chat_id: int, status_message_id: int, url: str) -> None:
    """Coloca um job de download na fila (LPUSH)."""
    job = {
        "chat_id": chat_id,
        "status_message_id": status_message_id,
        "url": url,
    }
    await redis.lpush(QUEUE_KEY, json.dumps(job))
    logger.info("Job enfileirado: chat_id=%s url=%s", chat_id, url[:50])


async def _process_job(bot: "Bot", bot_data: dict, payload: dict) -> None:
    """Baixa o vídeo e envia no mesmo chat; atualiza a mensagem de status."""
    chat_id = payload["chat_id"]
    status_message_id = payload["status_message_id"]
    url = payload["url"]
    redis = bot_data.get("redis")

    video_path: Path | None = None
    shared_path: Path | None = None
    description: str | None = None
    try:
        try:
            result: DownloadResult = await asyncio.to_thread(download_video, url)
            video_path = result.path
            description = result.description
        except NoVideoInPostError:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text=(
                    "Não foi possível obter o vídeo deste post/reel. "
                    "Pode ser que não tenha vídeo ou o link esteja inválido."
                ),
            )
            return
        if video_path is None or not video_path.exists():
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text=(
                    "Não consegui baixar o vídeo. O post pode ser privado, não ter vídeo ou o link estar inválido."
                ),
            )
            return

        size = video_path.stat().st_size
        use_local_bot_api = bot_data.get("use_local_bot_api", False)
        max_size = TELEGRAM_LOCAL_MAX_FILE_SIZE_BYTES if use_local_bot_api else TELEGRAM_MAX_FILE_SIZE_BYTES
        size_limit_mb = max_size // (1024 * 1024)

        if size > max_size:
            base_url = bot_data.get("base_url")
            if base_url:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text="Disponibilizando link de download...",
                )
                try:
                    _file_id, download_url = save_for_download(
                        video_path, base_url, delete_after_seconds=3600
                    )
                    if description:
                        caption = (
                            description[:TELEGRAM_CAPTION_MAX_LENGTH - 3] + "..."
                            if len(description) > TELEGRAM_CAPTION_MAX_LENGTH
                            else description
                        )
                        await bot.send_message(chat_id=chat_id, text=caption)
                    await bot.send_message(
                        chat_id=chat_id,
                        text="Download (expira em 1 hora):",
                    )
                    await bot.send_message(chat_id=chat_id, text=download_url)
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_message_id,
                        text="O link expira em 1 hora.",
                    )
                    if redis:
                        await increment_daily_download_count(redis, chat_id)
                except Exception as e:
                    logger.exception("Erro ao hospedar vídeo: %s", e)
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=status_message_id,
                        text=f"O vídeo é maior que {size_limit_mb} MB e não foi possível gerar o link. Tente outro.",
                    )
            else:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text=(
                        f"O vídeo é maior que {size_limit_mb} MB (limite do Telegram para este bot). "
                        "Configure TELEGRAM_BOT_API_BASE_URL (Local Bot API) ou BASE_URL para link de download."
                    ),
                )
            return

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message_id,
            text="Enviando...",
        )
        dimensions = get_video_dimensions(video_path)
        send_kwargs: dict = {"read_timeout": 60, "write_timeout": 60}
        if use_local_bot_api:
            send_kwargs["read_timeout"] = 300
            send_kwargs["write_timeout"] = 300
        if dimensions:
            send_kwargs["width"], send_kwargs["height"] = dimensions
        if description:
            send_kwargs["caption"] = (
                description[:TELEGRAM_CAPTION_MAX_LENGTH - 3] + "..."
                if len(description) > TELEGRAM_CAPTION_MAX_LENGTH
                else description
            )

        send_as_path = use_local_bot_api and bot_data.get("local_mode", False)
        path_for_api: Path = video_path
        if send_as_path:
            storage_dir = Path(os.getenv("STORAGE_DIR", "/app/storage"))
            storage_dir.mkdir(parents=True, exist_ok=True)
            shared_path = storage_dir / f"{uuid.uuid4()}.mp4"
            await asyncio.to_thread(shutil.copy2, video_path, shared_path)
            path_for_api = shared_path

        # Sempre envia no mesmo chat (sem canal)
        if send_as_path:
            await bot.send_video(
                chat_id=chat_id,
                video=path_for_api,
                **send_kwargs,
            )
        else:
            with open(video_path, "rb") as f:
                await bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    **send_kwargs,
                )
        if redis:
            await increment_daily_download_count(redis, chat_id)
            count = await get_daily_download_count(redis, chat_id)
            status_text = f"Video enviado. {count}/{DOWNLOADS_PER_DAY_LIMIT}"
        else:
            status_text = "Video enviado."
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message_id,
            text=status_text,
        )
    except Exception as e:
        logger.exception("Erro ao processar link Instagram: %s", e)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text="Ocorreu um erro ao baixar ou enviar o vídeo. Tente outro link.",
            )
        except Exception:
            pass
    finally:
        _ensure_file_removed(video_path)
        if shared_path is not None:
            _ensure_file_removed(shared_path)


async def run_worker(redis: Redis, app: "Application") -> None:
    """
    Worker que consome a fila (BRPOP) e processa cada job.
    Usa app.bot e app.bot_data para enviar mensagens após o download.
    """
    while True:
        try:
            result = await redis.brpop(QUEUE_KEY, timeout=1)
            if not result:
                continue
            _key, raw = result
            payload = json.loads(raw)
            bot = app.bot
            bot_data = app.bot_data
            await _process_job(bot, bot_data, payload)
        except asyncio.CancelledError:
            logger.info("Worker de fila cancelado")
            break
        except Exception as e:
            logger.exception("Erro no worker da fila: %s", e)
            await asyncio.sleep(2)
