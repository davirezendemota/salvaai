"""Download de vídeos do Instagram via yt-dlp."""

import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

import yt_dlp

logger = logging.getLogger(__name__)


class DownloadResult(NamedTuple):
    """Resultado do download: path do arquivo e descrição/legenda do post (se houver)."""
    path: Path | None
    description: str | None


class NoVideoInPostError(Exception):
    """Post/reel existe mas não contém vídeo (só imagem, etc.)."""


# Padrão para links do Instagram (reel ou post)
INSTAGRAM_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|p)/[^\s]+",
    re.IGNORECASE,
)

# Extensões de vídeo que o yt-dlp costuma gerar
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov")


def extract_instagram_urls(text: str) -> list[str]:
    """Extrai URLs do Instagram (reel/p) de um texto."""
    if not text or not text.strip():
        return []
    return INSTAGRAM_URL_PATTERN.findall(text)


def is_instagram_link(text: str) -> bool:
    """Verifica se o texto contém ao menos um link do Instagram (reel ou post)."""
    return bool(extract_instagram_urls(text))


# Retry em caso de rate limit (429) ou "rate-limit/login required" do Instagram
MAX_DOWNLOAD_RETRIES = 3
RETRY_BACKOFF_BASE_SEC = 5


def _is_retryable_error(e: Exception) -> bool:
    """Indica se o erro é 429 / rate-limit e vale retentar."""
    msg = str(e).lower()
    if "429" in msg or "too many requests" in msg:
        return True
    if "rate-limit" in msg or "rate_limit" in msg or "login required" in msg:
        return True
    return False


# Path fixo de cookies (igual telegram-bot): volume montado em /app/cookies; override via env em local.
COOKIES_FILE_DEFAULT = Path("/app/cookies/cookies.txt")


def _cookies_file() -> Path | None:
    """Retorna o path do arquivo de cookies se existir (env override ou path padrão)."""
    env_path = (os.getenv("INSTAGRAM_COOKIES_FILE") or "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        return p if p.is_file() else None
    return COOKIES_FILE_DEFAULT if COOKIES_FILE_DEFAULT.is_file() else None


def download_video(url: str) -> DownloadResult:
    """
    Baixa o vídeo da URL (Instagram) com yt-dlp e extrai a descrição/legenda do post.
    Retorna DownloadResult(path, description). path é None em caso de erro.
    O arquivo fica em um diretório temporário; o caller deve removê-lo após o uso.
    Em caso de 429/rate-limit, faz retry com backoff exponencial.
    Cookies: arquivo em /app/cookies/cookies.txt (envie pelo Telegram). Opcional em local: INSTAGRAM_COOKIES_FILE.
    """
    cookies_src = _cookies_file()
    last_error: Exception | None = None

    for attempt in range(MAX_DOWNLOAD_RETRIES):
        tmp_dir = tempfile.mkdtemp(prefix="instagram_bot_")
        outtmpl = str(Path(tmp_dir) / "%(id)s.%(ext)s")
        cookies_copy_path: Path | None = None

        ydl_opts = {
            "outtmpl": outtmpl,
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
        if cookies_src and cookies_src.exists():
            cookies_copy_path = Path(tmp_dir) / "cookies.txt"
            shutil.copy2(cookies_src, cookies_copy_path)
            ydl_opts["cookiefile"] = str(cookies_copy_path)
            logger.info("Usando cookies do Instagram em %s", cookies_src)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            description: str | None = None
            if info:
                description = (info.get("description") or info.get("title") or "").strip() or None

            tmp_path = Path(tmp_dir)
            for f in tmp_path.iterdir():
                if f.suffix.lower() in VIDEO_EXTENSIONS and f.is_file():
                    return DownloadResult(path=f, description=description)

            logger.warning("yt-dlp não gerou arquivo de vídeo em %s", tmp_dir)
            _cleanup_dir(tmp_path)
            return DownloadResult(path=None, description=description)
        except yt_dlp.utils.DownloadError as e:
            last_error = e
            _cleanup_dir(Path(tmp_dir))
            msg = str(e).lower()
            if "no video could be found" in msg or "no video" in msg:
                logger.warning("Post/reel sem vídeo: %s", url)
                raise NoVideoInPostError("Este post/reel não contém vídeo.") from e
            if _is_retryable_error(e) and attempt < MAX_DOWNLOAD_RETRIES - 1:
                wait_sec = RETRY_BACKOFF_BASE_SEC * (3**attempt)
                logger.warning(
                    "Rate limit/429 ao baixar %s (tentativa %d/%d). Aguardando %ds...",
                    url,
                    attempt + 1,
                    MAX_DOWNLOAD_RETRIES,
                    wait_sec,
                )
                time.sleep(wait_sec)
                continue
            logger.exception("Erro ao baixar %s: %s", url, e)
            return DownloadResult(path=None, description=None)
        except Exception as e:
            last_error = e
            _cleanup_dir(Path(tmp_dir))
            logger.exception("Erro ao baixar %s: %s", url, e)
            return DownloadResult(path=None, description=None)
        finally:
            if cookies_copy_path is not None and cookies_copy_path.exists():
                try:
                    cookies_copy_path.unlink()
                except OSError:
                    pass

    if last_error:
        logger.exception("Erro ao baixar %s após %d tentativas: %s", url, MAX_DOWNLOAD_RETRIES, last_error)
    return DownloadResult(path=None, description=None)


def _cleanup_dir(path: Path) -> None:
    """Remove arquivos e o diretório."""
    try:
        for f in path.iterdir():
            f.unlink()
        path.rmdir()
    except OSError as e:
        logger.warning("Erro ao limpar %s: %s", path, e)
