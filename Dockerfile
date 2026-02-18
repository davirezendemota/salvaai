FROM python:3.12-slim

WORKDIR /app

# ffmpeg necessário para o yt-dlp mesclar vídeo+áudio
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

CMD ["python", "-m", "src.main"]
