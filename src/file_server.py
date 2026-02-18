"""Servidor de arquivos para download temporário (vídeos > 50 MB)."""

import logging
import shutil
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

app = FastAPI(title="Download temporário", docs_url=None, redoc_url=None)
_storage_dir: Path | None = None
_base_url: str = ""


def init_storage(storage_dir: Path, max_age_seconds: int = 3600) -> None:
    """Cria o diretório de armazenamento e remove arquivos mais velhos que max_age_seconds."""
    global _storage_dir
    _storage_dir = storage_dir
    _storage_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for f in _storage_dir.iterdir():
        if f.is_file() and f.suffix.lower() == ".mp4" and (now - f.stat().st_mtime) > max_age_seconds:
            try:
                f.unlink()
                logger.info("Removido arquivo expirado: %s", f.name)
            except OSError as e:
                logger.warning("Erro ao remover %s: %s", f, e)
    logger.info("Storage de downloads em %s", _storage_dir)


def _get_storage_path(file_id: str) -> Path:
    """Retorna o path do arquivo no storage (sem validar existência)."""
    if _storage_dir is None:
        raise RuntimeError("Storage não inicializado")
    # Apenas o UUID é aceito no file_id (segurança)
    if not file_id.replace("-", "").isalnum() or len(file_id) > 64:
        raise ValueError("file_id inválido")
    return _storage_dir / f"{file_id}.mp4"


def save_for_download(source_path: Path, base_url: str, delete_after_seconds: int = 3600) -> tuple[str, str]:
    """
    Move o arquivo para o storage com nome UUID e agenda exclusão.
    Retorna (file_id, url_de_download).
    """
    if _storage_dir is None:
        raise RuntimeError("Storage não inicializado; chame init_storage antes")
    file_id = str(uuid.uuid4())
    dest = _storage_dir / f"{file_id}.mp4"
    shutil.move(str(source_path), str(dest))
    url = f"{base_url.rstrip('/')}/download/{file_id}"
    timer = threading.Timer(delete_after_seconds, _delete_file, [dest])
    timer.daemon = True
    timer.start()
    logger.info("Arquivo %s disponível em %s; exclusão em %ds", file_id, url, delete_after_seconds)
    return file_id, url


def _delete_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
            logger.info("Arquivo temporário removido: %s", path.name)
    except OSError as e:
        logger.warning("Erro ao remover %s: %s", path, e)


def clear_storage() -> int:
    """
    Remove todos os vídeos (.mp4) do storage.
    Retorna a quantidade de arquivos removidos.
    """
    if _storage_dir is None or not _storage_dir.exists():
        return 0
    count = 0
    for f in _storage_dir.iterdir():
        if f.is_file() and f.suffix.lower() == ".mp4":
            try:
                f.unlink()
                count += 1
                logger.info("Removido: %s", f.name)
            except OSError as e:
                logger.warning("Erro ao remover %s: %s", f, e)
    if count:
        logger.info("Storage limpo: %d vídeo(s) removido(s)", count)
    return count


@app.get("/download/{file_id}")
def download(file_id: str):
    """Serve o arquivo para download; 404 se não existir ou já tiver sido removido."""
    try:
        path = _get_storage_path(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID inválido")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Link expirado ou inexistente")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"video_{file_id[:8]}.mp4",
    )


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Sobe o servidor HTTP em uma thread daemon (não bloqueia o bot)."""
    import uvicorn
    thread = threading.Thread(
        target=lambda: uvicorn.run(app, host=host, port=port, log_level="warning"),
        daemon=True,
    )
    thread.start()
    logger.info("Servidor de download escutando em %s:%s", host, port)
