"""Handlers do bot: comandos e mensagens com link Instagram."""

import asyncio
import logging
import os
import shutil
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from src.cookies_sanitizer import MAX_COOKIES_FILE_SIZE, sanitize_cookies_content
from src.downloader import extract_instagram_urls, is_instagram_link
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
        "/help - Esta ajuda\n\n"
        "Envie uma mensagem com um link do Instagram (reel ou post) "
        "e eu baixo o vídeo e envio aqui no chat. Exemplo:\n"
        "https://www.instagram.com/reel/xxxxx/\n"
        "https://www.instagram.com/p/xxxxx/"
    )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando mantido por compatibilidade; o bot não usa mais servidor de download."""
    await update.message.reply_text(
        "O bot não usa mais servidor de download. Vídeos são enviados direto ou como GIF (se > 50 MB)."
    )


def _save_sanitized_cookies(temp_path: Path, path: Path, sanitized: str) -> None:
    """Grava conteúdo sanitizado em temp_path e move para path (ou fallback /tmp)."""
    temp_path.write_text(sanitized, encoding="utf-8")
    try:
        os.replace(temp_path, path)
    except PermissionError:
        fallback = Path("/tmp/cookies.txt")
        fallback.write_text(sanitized, encoding="utf-8")
        raise PermissionError(str(path))


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Se receber um arquivo chamado cookies.txt, valida/sanitiza e salva em /app/cookies/cookies.txt."""
    if not update.message or not update.message.document:
        return
    doc = update.message.document
    if doc.file_name.lower() != "cookies.txt":
        await update.message.reply_text(
            "Para atualizar os cookies do Instagram, envie um arquivo com o nome exato: cookies.txt"
        )
        return

    allowed_user_id = context.bot_data.get("allowed_user_id")
    if allowed_user_id is None:
        await update.message.reply_text(
            "Envio de cookies desativado (TELEGRAM_ALLOWED_USER_ID não configurado)."
        )
        return
    user_id = update.effective_user.id if update.effective_user else None
    if user_id != allowed_user_id:
        logger.warning("Tentativa de envio de cookies por usuário não autorizado: %s", user_id)
        await update.message.reply_text("Apenas o dono do bot pode enviar o arquivo de cookies.")
        return

    if doc.file_size is not None and doc.file_size > MAX_COOKIES_FILE_SIZE:
        await update.message.reply_text(
            f"Arquivo muito grande. Tamanho máximo: {MAX_COOKIES_FILE_SIZE // 1024} KB."
        )
        return

    cookies_path = context.bot_data.get("cookies_file")
    if not cookies_path:
        await update.message.reply_text("Cookies não configurado.")
        return

    path = Path(cookies_path)
    if path.exists() and path.is_dir():
        path = Path("/tmp/cookies.txt")
    path = path if path.suffix else path.with_name("cookies.txt")
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_name(path.name + ".tmp")
    try:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(temp_path)
        raw = await asyncio.to_thread(temp_path.read_bytes)
        if len(raw) > MAX_COOKIES_FILE_SIZE:
            temp_path.unlink(missing_ok=True)
            await update.message.reply_text(
                f"Arquivo muito grande. Tamanho máximo: {MAX_COOKIES_FILE_SIZE // 1024} KB."
            )
            return
        sanitized = sanitize_cookies_content(raw)
        await asyncio.to_thread(_save_sanitized_cookies, temp_path, path, sanitized)
    except ValueError as e:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning("Cookies rejeitados (sanitização): %s", e)
        await update.message.reply_text(f"Arquivo recusado por segurança: {e}")
        return
    except PermissionError as e:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.info("Cookies salvos em fallback /tmp (destino %s não gravável)", path)
        await update.message.reply_text(
            "Não foi possível gravar no volume montado. Cookies salvos em /tmp."
        )
        return
    except OSError as e:
        logger.exception("Erro ao salvar cookies: %s", e)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        await update.message.reply_text(
            "Não foi possível salvar o arquivo (verifique permissões ou se o volume está somente leitura)."
        )
        return
    except Exception as e:
        logger.exception("Erro ao processar cookies do Telegram: %s", e)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        await update.message.reply_text("Erro ao processar o arquivo. Tente de novo.")
        return

    await update.message.reply_text(
        f"Cookies atualizados e salvos em: {path}\n"
        "Os próximos downloads do Instagram usarão esse arquivo."
    )
    logger.info("Cookies recebidos pelo Telegram e salvos em %s", path)


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
