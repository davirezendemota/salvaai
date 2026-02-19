"""Transcrição de áudio/vídeo via OpenAI Whisper API."""

import logging
from pathlib import Path

from src.video_utils import WHISPER_MAX_FILE_SIZE_BYTES, extract_audio

logger = logging.getLogger(__name__)


def transcribe_video(video_path: Path, *, api_key: str | None = None) -> str | None:
    """
    Transcreve o áudio do vídeo usando a API Whisper da OpenAI.
    Se o arquivo for maior que 25 MB, extrai apenas o áudio (ffmpeg) e envia o áudio.
    Retorna o texto da transcrição ou None em caso de erro/falha.
    """
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai não instalado; transcrição desabilitada")
        return None

    if not api_key or not api_key.strip():
        return None
    if not video_path or not video_path.exists():
        return None

    client = OpenAI(api_key=api_key.strip())
    file_path: Path = video_path
    audio_path_to_remove: Path | None = None

    try:
        if video_path.stat().st_size > WHISPER_MAX_FILE_SIZE_BYTES:
            audio_path_to_remove = extract_audio(video_path)
            if audio_path_to_remove is None:
                logger.warning("Vídeo > 25 MB e extração de áudio falhou; tentando enviar vídeo mesmo assim")
            else:
                file_path = audio_path_to_remove

        with open(file_path, "rb") as f:
            transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="text",
            )

        if isinstance(transcript, str):
            text = transcript.strip()
            return text if text else None
        return None
    except Exception as e:
        logger.warning("Erro ao transcrever %s: %s", video_path, e)
        return None
    finally:
        if audio_path_to_remove is not None and audio_path_to_remove.exists():
            try:
                audio_path_to_remove.unlink()
            except OSError as err:
                logger.warning("Erro ao remover áudio temporário %s: %s", audio_path_to_remove, err)
