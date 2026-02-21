"""Fila de downloads com Redis: enfileiramento e worker que processa em background."""

import asyncio
import json
import logging
import os
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING

from redis.asyncio import Redis

if TYPE_CHECKING:
    from telegram import Bot
    from telegram.ext import Application

from src.downloader import NoVideoInPostError, download_video, DownloadResult
from src.summary import generate_summary, normalize_hashtags
from src.transcribe import transcribe_video
from src.video_utils import get_video_dimensions, video_to_gif

logger = logging.getLogger(__name__)

QUEUE_KEY = "instagram_bot:download_queue"
TELEGRAM_CAPTION_MAX_LENGTH = 1024
TELEGRAM_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
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


async def push_job(
    redis: Redis,
    chat_id: int,
    status_message_id: int,
    url: str,
    *,
    telegram_user_id: int | None = None,
) -> None:
    """Coloca um job de download na fila (LPUSH)."""
    job = {
        "chat_id": chat_id,
        "status_message_id": status_message_id,
        "url": url,
        "telegram_user_id": telegram_user_id or chat_id,
    }
    await redis.lpush(QUEUE_KEY, json.dumps(job))
    logger.info("Job enfileirado: chat_id=%s url=%s", chat_id, url[:50])


async def _process_job(bot: "Bot", bot_data: dict, payload: dict) -> None:
    """Baixa o vídeo e envia no mesmo chat; atualiza a mensagem de status."""
    chat_id = payload["chat_id"]
    status_message_id = payload["status_message_id"]
    url = payload["url"]
    telegram_user_id = payload.get("telegram_user_id") or chat_id
    redis = bot_data.get("redis")
    payment_service = bot_data.get("payment_service")

    video_path: Path | None = None
    gif_path: Path | None = None
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

        # Caption: resumo (transcrição + GPT) se habilitado, senão descrição bruta
        caption: str | None = None
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        enable_summary = (os.getenv("ENABLE_VIDEO_SUMMARY", "true").strip().lower() not in ("0", "false", "no"))
        if enable_summary and api_key:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text="Transcrevendo e gerando resumo...",
            )
            transcription = await asyncio.to_thread(transcribe_video, video_path, api_key=api_key)
            summary_text = await asyncio.to_thread(
                generate_summary,
                transcription,
                description,
                api_key=api_key,
            )
            if summary_text:
                caption = summary_text
        if caption is None and description:
            caption = description
        if caption:
            caption = normalize_hashtags(caption)

        # Registrar uso (obter ID para caption); saldo só é debitado após envio com sucesso
        usage_id: int | None = None
        if payment_service:
            usage_id = await asyncio.to_thread(
                payment_service.record_usage,
                telegram_user_id,
                url,
                0.0,
            )
        if usage_id is None and payment_service:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text="Você não tem saldo de posts. Use /planos e /comprar para recarregar.",
            )
            return

        caption = f"[P{usage_id:04d}]\n\n" + (caption or "")
        if len(caption) > TELEGRAM_CAPTION_MAX_LENGTH:
            caption = caption[: TELEGRAM_CAPTION_MAX_LENGTH - 3] + "..."

        size = video_path.stat().st_size

        if size > TELEGRAM_MAX_FILE_SIZE_BYTES:
            # Vídeo > 50 MB: converte para GIF e envia com URL do vídeo na descrição
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text="Vídeo maior que 50 MB. Convertendo para GIF...",
            )
            try:
                gif_path = await asyncio.to_thread(video_to_gif, video_path)
            except Exception as e:
                logger.warning("Falha ao converter vídeo para GIF: %s", e)
                gif_path = None

            if (
                gif_path
                and gif_path.exists()
                and gif_path.stat().st_size <= TELEGRAM_MAX_FILE_SIZE_BYTES
            ):
                gif_caption_parts = []
                if caption:
                    gif_caption_parts.append(caption)
                gif_caption_parts.append(f"Vídeo original: {url}")
                gif_caption = "\n\n".join(gif_caption_parts)
                if len(gif_caption) > TELEGRAM_CAPTION_MAX_LENGTH:
                    gif_caption = gif_caption[: TELEGRAM_CAPTION_MAX_LENGTH - 3] + "..."

                with open(gif_path, "rb") as f:
                    await bot.send_animation(
                        chat_id=chat_id,
                        animation=f,
                        caption=gif_caption,
                        read_timeout=60,
                        write_timeout=60,
                    )
                if payment_service:
                    await asyncio.to_thread(
                        payment_service.deduct_balance,
                        telegram_user_id,
                    )
                if redis:
                    await increment_daily_download_count(redis, chat_id)
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=status_message_id,
                    text="Enviado como GIF (vídeo > 50 MB).",
                )
                _ensure_file_removed(gif_path)
                return

            # Conversão para GIF falhou ou GIF ficou > 50 MB
            if gif_path:
                _ensure_file_removed(gif_path)
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message_id,
                text=(
                    "O vídeo é maior que 50 MB e não foi possível converter para GIF. "
                    "Tente outro link."
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
        if dimensions:
            send_kwargs["width"], send_kwargs["height"] = dimensions
        if caption:
            send_kwargs["caption"] = caption

        with open(video_path, "rb") as f:
            await bot.send_video(
                chat_id=chat_id,
                video=f,
                **send_kwargs,
            )
        if payment_service:
            await asyncio.to_thread(
                payment_service.deduct_balance,
                telegram_user_id,
            )
        if redis:
            await increment_daily_download_count(redis, chat_id)
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_message_id,
            text="Video enviado.",
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
        _ensure_file_removed(gif_path)


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
            try:
                await asyncio.sleep(2)
            except RuntimeError as re:
                if "no running event loop" in str(re).lower():
                    break
                raise
