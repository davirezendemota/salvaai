"""Utilitários para vídeo: dimensões via ffprobe, conversão para GIF, extração de áudio."""

import json
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Limite da API Whisper (25 MB) – vídeos maiores precisam ter áudio extraído
WHISPER_MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024

# Limites para GIF (manter sob 50 MB para Telegram)
GIF_MAX_WIDTH = 480
GIF_FPS = 8
GIF_MAX_DURATION_SEC = 45


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


def video_to_gif(
    video_path: Path,
    output_path: Path | None = None,
    max_width: int = GIF_MAX_WIDTH,
    fps: int = GIF_FPS,
    max_duration_sec: int = GIF_MAX_DURATION_SEC,
) -> Path | None:
    """
    Converte vídeo para GIF com ffmpeg (palette para melhor qualidade).
    Limita largura, fps e duração para manter o arquivo sob 50 MB (limite Telegram).
    Retorna o path do GIF ou None em caso de erro.
    """
    import tempfile

    if output_path is None:
        output_path = video_path.parent / f"{video_path.stem}.gif"
    output_path = Path(output_path)
    tmp_dir = Path(tempfile.mkdtemp(prefix="gif_"))
    palette_path = tmp_dir / "palette.png"
    try:
        # Passo 1: gerar palette
        scale = f"scale={max_width}:-1:flags=lanczos"
        filter_gen = f"fps={fps},{scale},palettegen"
        cmd_gen = [
            "ffmpeg",
            "-y",
            "-t",
            str(max_duration_sec),
            "-i",
            str(video_path),
            "-vf",
            filter_gen,
            str(palette_path),
        ]
        run = subprocess.run(cmd_gen, capture_output=True, text=True, timeout=120)
        if run.returncode != 0 or not palette_path.exists():
            logger.warning("ffmpeg palettegen falhou para %s: %s", video_path, run.stderr)
            return None

        # Passo 2: aplicar palette e gerar GIF
        filter_use = f"fps={fps},{scale}[x];[x][1:v]paletteuse"
        cmd_use = [
            "ffmpeg",
            "-y",
            "-t",
            str(max_duration_sec),
            "-i",
            str(video_path),
            "-i",
            str(palette_path),
            "-filter_complex",
            filter_use,
            str(output_path),
        ]
        run = subprocess.run(cmd_use, capture_output=True, text=True, timeout=120)
        if run.returncode != 0 or not output_path.exists():
            logger.warning("ffmpeg paletteuse falhou para %s: %s", video_path, run.stderr)
            return None

        return output_path
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("Erro ao converter para GIF %s: %s", video_path, e)
        return None
    finally:
        try:
            if palette_path.exists():
                palette_path.unlink()
            if tmp_dir.exists():
                tmp_dir.rmdir()
        except OSError:
            pass


def extract_audio(video_path: Path) -> Path | None:
    """
    Extrai apenas o áudio do vídeo para um arquivo M4A (menor que o vídeo).
    Útil para enviar à API Whisper quando o vídeo excede 25 MB.
    Retorna o path do arquivo de áudio temporário; o caller deve removê-lo após o uso.
    Retorna None em caso de erro.
    """
    if not video_path.exists():
        return None
    suffix = ".m4a"
    fd, out_path = tempfile.mkstemp(suffix=suffix, prefix="audio_")
    try:
        import os
        os.close(fd)
        out = Path(out_path)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "aac",
            "-b:a",
            "64k",
            str(out),
        ]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if run.returncode != 0 or not out.exists():
            logger.warning("ffmpeg extract_audio falhou para %s: %s", video_path, run.stderr)
            if out.exists():
                out.unlink()
            return None
        return out
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("Erro ao extrair áudio de %s: %s", video_path, e)
        try:
            Path(out_path).unlink(missing_ok=True)
        except OSError:
            pass
        return None
