# Bot Telegram – Download de vídeos do Instagram

Bot em Python que recebe links de vídeos do Instagram (reel ou post) e envia o vídeo na mesma conversa. Stack baseada em `projects/telegram-bot`.

## Pré-requisitos

- Docker e Docker Compose (ou Python 3.12 + Redis local)
- Token do bot (obtido no [@BotFather](https://t.me/BotFather))

## Configuração

1. Crie o arquivo `.env` na raiz (copie de `.env.example`):

   ```bash
   cp .env.example .env
   ```

2. Edite o `.env` e defina:

   - `TELEGRAM_BOT_TOKEN` – token do bot (obrigatório)
   - `REDIS_URL` – URL do Redis, ex.: `redis://redis:6379/0` no Docker ou `redis://localhost:6379/0` local (obrigatório)

## Rodar com Docker

```bash
docker compose up
```

O código em `src/` é montado no container e o bot reinicia automaticamente (reload) ao alterar arquivos `.py`. Para rodar em background: `docker compose up -d`. Logs: `docker compose logs -f bot`. Parar: `docker compose down`.

## Uso

1. Abra o Telegram e inicie uma conversa com o seu bot.
2. Envie `/start` ou `/help` para ver os comandos.
3. Envie uma mensagem com um link do Instagram (reel ou post), por exemplo:
   - `https://www.instagram.com/reel/xxxxx/`
   - `https://www.instagram.com/p/xxxxx/`
4. O bot responde com "Na fila. Baixando em breve..." e, em seguida, envia o vídeo na mesma conversa.

**Vídeos > 50 MB:** o bot converte para GIF e envia com a URL do vídeo original na descrição.

## Desenvolvimento local (sem Docker)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Instale o **ffmpeg** no sistema (ex.: `brew install ffmpeg` no macOS). Suba um Redis (ex.: `docker run -d -p 6379:6379 redis:7-alpine`) e no `.env` use `REDIS_URL=redis://localhost:6379/0`.

```bash
python -m src.main
```

## Comandos do bot

- `/start` – Mensagem de boas-vindas
- `/help` – Lista de comandos e como enviar links
- `/delete` – Resposta informando que o bot não usa mais servidor de download
