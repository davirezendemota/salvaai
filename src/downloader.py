"""Download de vídeos do Instagram via yt-dlp."""

import logging
import re
import tempfile
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


def download_video(url: str) -> DownloadResult:
    """
    Baixa o vídeo da URL (Instagram) com yt-dlp e extrai a descrição/legenda do post.
    Retorna DownloadResult(path, description). path é None em caso de erro.
    O arquivo fica em um diretório temporário; o caller deve removê-lo após o uso.
    """
    tmp_dir = tempfile.mkdtemp(prefix="instagram_bot_")
    outtmpl = str(Path(tmp_dir) / "%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }

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
        _cleanup_dir(Path(tmp_dir))
        msg = str(e).lower()
        if "no video could be found" in msg or "no video" in msg:
            logger.warning("Post/reel sem vídeo: %s", url)
            raise NoVideoInPostError("Este post/reel não contém vídeo.") from e
        logger.exception("Erro ao baixar %s: %s", url, e)
        return DownloadResult(path=None, description=None)
    except Exception as e:
        logger.exception("Erro ao baixar %s: %s", url, e)
        _cleanup_dir(Path(tmp_dir))
        return DownloadResult(path=None, description=None)


def _cleanup_dir(path: Path) -> None:
    """Remove arquivos e o diretório."""
    try:
        for f in path.iterdir():
            f.unlink()
        path.rmdir()
    except OSError as e:
        logger.warning("Erro ao limpar %s: %s", path, e)
