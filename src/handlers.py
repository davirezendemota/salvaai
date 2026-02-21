"""Handlers do bot: comandos e mensagens com link Instagram."""

import asyncio
import logging
import os
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from src.cookies_sanitizer import MAX_COOKIES_FILE_SIZE, sanitize_cookies_content
from src.downloader import extract_instagram_urls, is_instagram_link
from src.queue import push_job

logger = logging.getLogger(__name__)

# Logo do bot (relativo à raiz do projeto)
_START_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "salvaai_logo.png"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde ao comando /start com logo, saudação e uso, em estilo bot Telegram."""
    user = update.effective_user
    nome = (user.first_name or user.username or "usuário") if user else "usuário"

    used = 0
    total = 0
    payment_service = context.bot_data.get("payment_service")
    if payment_service and user:
        used = await asyncio.to_thread(payment_service.get_usage_count, user.id)
        balance = await asyncio.to_thread(payment_service.get_balance, user.id)
        total = used + balance

    # Texto com cara de bot Telegram: limpo, markdown, amigável
    msg = (
        f"👋 *Olá, {nome}!*\n\n"
        "Eu sou o *SalvaAI* — salvo posts do Instagram, Facebook e Twitter "
        "direto aqui no Telegram.\n\n"
        f"📊 *Seu uso:* {used} / {total} posts\n\n"
        "*Comandos disponíveis:*\n"
        "/start — Início\n"
        "/saldo — Ver uso e saldo de posts\n"
        "/historico — Histórico de consumo\n"
        "/help — Ajuda\n"
        "/planos — Ver planos e preços\n"
        "/comprar plano — Comprar (ex: /comprar basic)\n\n"
        "Envie um link para começar."
    )
    reply_kw = {"parse_mode": "Markdown", "reply_to_message_id": update.message.message_id}
    if _START_LOGO_PATH.is_file():
        with open(_START_LOGO_PATH, "rb") as f:
            await update.message.reply_photo(photo=f, caption=msg, **reply_kw)
    else:
        await update.message.reply_text(msg, **reply_kw)


async def cmd_historico(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde ao comando /historico com histórico de consumo (ID, data, USD, link)."""
    user = update.effective_user
    if not user:
        return
    payment_service = context.bot_data.get("payment_service")
    if not payment_service:
        await update.message.reply_text("Sistema de pagamentos não configurado.")
        return
    history = await asyncio.to_thread(
        payment_service.get_usage_history,
        user.id,
        30,
    )
    if not history:
        await update.message.reply_text(
            "Nenhum consumo registrado ainda. Seus downloads aparecerão aqui.",
            reply_to_message_id=update.message.message_id,
        )
        return
    lines = ["📜 *Histórico de consumo*\n"]
    tz_sp = ZoneInfo("America/Sao_Paulo")
    for usage_id, used_at, cost_usd, video_link in history:
        if used_at:
            utc_dt = used_at.replace(tzinfo=timezone.utc) if used_at.tzinfo is None else used_at
            sp_dt = utc_dt.astimezone(tz_sp)
            data_str = sp_dt.strftime("%d/%m/%Y %H:%M")
        else:
            data_str = "—"
        link_str = (video_link or "—")[:60] + ("..." if (video_link and len(video_link) > 60) else "")
        lines.append(f"ID: `[P{usage_id:04d}]`")
        lines.append(f"Data: {data_str}")
        lines.append(f"Consumo: ${cost_usd:.4f}")
        lines.append(f"Link: {link_str}")
        lines.append("")
    msg = "\n".join(lines).strip()
    if len(msg) > 4000:
        msg = msg[:3997] + "..."
    await update.message.reply_text(
        msg,
        parse_mode="Markdown",
        reply_to_message_id=update.message.message_id,
    )


async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde ao comando /saldo com uso e saldo de posts."""
    user = update.effective_user
    used = 0
    total = 0
    balance = 0
    payment_service = context.bot_data.get("payment_service")
    if payment_service and user:
        used = await asyncio.to_thread(payment_service.get_usage_count, user.id)
        balance = await asyncio.to_thread(payment_service.get_balance, user.id)
        total = used + balance

    msg = (
        f"📊 *Seu uso:* {used} / {total} posts\n"
        f"💳 *Saldo:* {balance} posts restantes"
    )
    await update.message.reply_text(
        msg,
        parse_mode="Markdown",
        reply_to_message_id=update.message.message_id,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde ao comando /help."""
    await update.message.reply_text(
        "Comandos:\n"
        "/start - Início\n"
        "/saldo - Ver uso e saldo de posts\n"
        "/historico - Histórico de consumo (ID, data, USD, link)\n"
        "/help - Esta ajuda\n"
        "/planos - Ver planos e preços\n"
        "/comprar <slug> - Comprar plano (ex: /comprar basic)\n\n"
        "Envie uma mensagem com um link do Instagram (reel ou post) "
        "e eu baixo o vídeo e envio aqui no chat. Exemplo:\n"
        "https://www.instagram.com/reel/xxxxx/\n"
        "https://www.instagram.com/p/xxxxx/"
    )


async def cmd_planos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista planos disponíveis."""
    service = context.bot_data.get("payment_service")
    if not service:
        await update.message.reply_text("Pagamentos não configurados.")
        return
    plans = await asyncio.to_thread(service.get_plans)
    if not plans:
        await update.message.reply_text("Nenhum plano disponível no momento.")
        return
    emojis = {"basic": "🟢", "pro": "🔵", "creator": "🔴"}
    lines = []
    for p in plans:
        reais = p.price_cents / 100
        lines.append(f"{emojis.get(p.slug, '•')} *{p.name}* — R$ {reais:.0f} — {p.posts_included} posts")
    lines.append("")
    lines.append("Use /comprar basic, /comprar pro ou /comprar creator para comprar.")
    lines.append("Pagamento via PIX (recarga avulsa, sem assinatura).")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_comprar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comprar plano: /comprar basic|pro|creator."""
    if not update.message or not update.message.text:
        return
    parts = update.message.text.strip().split()
    slug = parts[1].lower() if len(parts) > 1 else None
    if slug not in ("basic", "pro", "creator"):
        await update.message.reply_text(
            "Uso: /comprar basic, /comprar pro ou /comprar creator"
        )
        return
    service = context.bot_data.get("payment_service")
    if not service:
        await update.message.reply_text("Pagamentos não configurados.")
        return
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        await update.message.reply_text("Erro ao identificar usuário.")
        return
    recharge, result = await asyncio.to_thread(
        service.create_recharge,
        user.id,
        chat.id,
        slug,
    )
    if not recharge or not result:
        await update.message.reply_text("Não foi possível criar a recarga. Tente outro plano.")
        return
    msg = (
        f"Recarga criada: {recharge.posts_granted} posts.\n\n"
        f"*Charge ID:* `{result.charge_id}`\n"
    )
    if result.link:
        msg += f"Link: {result.link}\n"
    if result.qr_code:
        msg += f"PIX (copia e cola): `{result.qr_code[:80]}...`\n"
    msg += "\nApós pagar, o saldo será creditado (webhook). Para teste, use o endpoint POST /payments/webhook com {\"charge_id\": \"...\"}."
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: /whitelist add <user_id> ou /whitelist remove <user_id>."""
    allowed = context.bot_data.get("allowed_user_id")
    if allowed is None or (update.effective_user and update.effective_user.id != allowed):
        await update.message.reply_text("Apenas o dono do bot pode usar este comando.")
        return
    if not update.message or not update.message.text:
        return
    parts = update.message.text.strip().split()
    if len(parts) < 3:
        await update.message.reply_text("Uso: /whitelist add <user_id> ou /whitelist remove <user_id>")
        return
    op, raw_id = parts[1].lower(), parts[2]
    try:
        uid = int(raw_id)
    except ValueError:
        await update.message.reply_text("user_id deve ser um número.")
        return
    service = context.bot_data.get("payment_service")
    if not service:
        await update.message.reply_text("Pagamentos não configurados.")
        return
    if op == "add":
        await asyncio.to_thread(service.whitelist_add, uid, reason="admin")
        await update.message.reply_text(f"Usuário {uid} adicionado à whitelist.")
    elif op == "remove":
        ok = await asyncio.to_thread(service.whitelist_remove, uid)
        await update.message.reply_text(
            f"Usuário {uid} removido da whitelist." if ok else f"Usuário {uid} não estava na whitelist."
        )
    else:
        await update.message.reply_text("Uso: /whitelist add <user_id> ou /whitelist remove <user_id>")


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

    payment_service = context.bot_data.get("payment_service")
    if not payment_service:
        await update.message.reply_text("Sistema de pagamentos não configurado.")
        return

    telegram_user_id = update.effective_user.id if update.effective_user else update.message.chat_id
    can_download = await asyncio.to_thread(payment_service.can_download, telegram_user_id)
    if not can_download:
        balance = await asyncio.to_thread(payment_service.get_balance, telegram_user_id)
        await update.message.reply_text(
            "Você não tem saldo de posts. Use /planos para ver os planos e /comprar <plano> para recarregar."
        )
        return

    status_msg = await update.message.reply_text("Na fila. Baixando em breve...")
    status_message_id = status_msg.message_id
    chat_id = update.message.chat_id
    await push_job(redis, chat_id, status_message_id, url, telegram_user_id=telegram_user_id)
