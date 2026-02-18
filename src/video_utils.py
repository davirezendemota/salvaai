"""Utilitários para vídeo: dimensões via ffprobe (preserva aspecto em vídeos verticais)."""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def get_video_dimensions(video_path: Path) -> tuple[int, int] | None:
    """
    Obtém largura e altura de exibição do vídeo usando ffprobe.
    Considera metadado de rotação (90/270): troca width/height para o Telegram exibir certo.
    Retorna (width, height) ou None se não for possível obter.
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "v:0",
            str(video_path),
        ]
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if out.returncode != 0 or not out.stdout.strip():
            logger.warning("ffprobe falhou para %s: %s", video_path, out.stderr)
            return None
        data = json.loads(out.stdout)
        streams = data.get("streams") or []
        if not streams:
            return None
        s = streams[0]
        w = int(s.get("width", 0))
        h = int(s.get("height", 0))
        if w <= 0 or h <= 0:
            return None
        # Vídeos verticais às vezes vêm com rotação 90/270 nos metadados
        rotate = None
        tags = s.get("tags") or {}
        if isinstance(tags.get("rotate"), str):
            try:
                rotate = int(tags["rotate"])
            except ValueError:
                pass
        if rotate in (90, 270):
            w, h = h, w
        return (w, h)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.warning("Erro ao obter dimensões de %s: %s", video_path, e)
        return None
