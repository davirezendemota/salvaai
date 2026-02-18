"""Handlers do bot: comandos e mensagens com link Instagram."""

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from src.downloader import extract_instagram_urls, is_instagram_link
from src.file_server import clear_storage
from src.queue import can_download_today, push_job

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde ao comando /start."""
    await update.message.reply_text(
        "Olá! Envie um link de vídeo do Instagram (reel ou post) que eu baixo e envio o vídeo aqui no chat."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde ao comando /help."""
    await update.message.reply_text(
        "Comandos:\n"
        "/start - Início\n"
        "/help - Esta ajuda\n"
        "/delete - Apaga todos os vídeos em storage\n\n"
        "Envie uma mensagem com um link do Instagram (reel ou post) "
        "e eu baixo o vídeo e envio aqui no chat. Exemplo:\n"
        "https://www.instagram.com/reel/xxxxx/\n"
        "https://www.instagram.com/p/xxxxx/"
    )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Apaga todos os vídeos no diretório de storage."""
    try:
        count = await asyncio.to_thread(clear_storage)
    except Exception as e:
        logger.exception("Erro ao limpar storage: %s", e)
        await update.message.reply_text("Erro ao apagar os vídeos. Tente de novo.")
        return
    if count == 0:
        await update.message.reply_text(
            "Nenhum vídeo em storage (já estava vazio ou storage não configurado)."
        )
    else:
        await update.message.reply_text(f"Storage limpo: {count} vídeo(s) apagado(s).")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processa mensagens: se tiver link Instagram, enfileira o download; senão, pede o link."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not is_instagram_link(text):
        await update.message.reply_text(
            "Envie um link do Instagram (reel ou post). Exemplo:\n"
            "https://www.instagram.com/reel/xxxxx/"
        )
        return

    urls = extract_instagram_urls(text)
    url = urls[0]

    redis = context.bot_data.get("redis")
    if not redis:
        await update.message.reply_text(
            "Fila não configurada (REDIS_URL). Não é possível processar o link."
        )
        return

    chat_id = update.message.chat_id
    if not await can_download_today(redis, chat_id):
        await update.message.reply_text(
            "Limite de 10 downloads por dia atingido. Tente amanhã."
        )
        return

    status_msg = await update.message.reply_text("Na fila. Baixando em breve...")
    status_message_id = status_msg.message_id
    await push_job(redis, chat_id, status_message_id, url)
