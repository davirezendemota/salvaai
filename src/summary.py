"""Geração de resumo e hashtags a partir de transcrição e descrição do post (GPT)."""

import logging
import re

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM_PROMPT = """Você vai atuar como um **resumidor inteligente de vídeos do Instagram**.

**Entrada:**
* Transcrição ou conteúdo do vídeo.
* Descrição completa do post do Instagram.

**Tarefa:**
1. Analise o conteúdo do vídeo e a descrição.
2. Ignore completamente todas as hashtags originais da descrição.
3. Extraia apenas os assuntos relevantes, ideias principais e informações úteis.
4. Se a descrição contiver lista ordenada (1., 2., 3. etc.), incorpore essas informações no resumo de forma estruturada e clara.
5. Não invente informações e não inclua opiniões próprias.
6. Seja direto, objetivo e evite repetições.

**Hashtags (obrigatório):**
* A **primeira hashtag** deve ser sempre a **categoria/tema principal do vídeo** (ex.: #receitas, #fitness, #dicasdecarreira, #tutoriais). Uma única palavra ou expressão curta.
* As **outras 9 hashtags** devem ser estratégicas para indexação futura, baseadas exclusivamente no conteúdo real do vídeo.
* Não usar hashtags genéricas como #fyp, #viral, #reels etc.
* Evitar variações repetidas da mesma palavra.
* Formato: todas iniciando com `#` e separadas por espaço em uma única linha (total 10 hashtags).

**Formato de saída obrigatório:**

Resumo:
<parágrafo claro e objetivo com as ideias centrais>

Hashtags:
#categoria #hashtag2 #hashtag3 #hashtag4 #hashtag5 #hashtag6 #hashtag7 #hashtag8 #hashtag9 #hashtag10

Linguagem: português claro, direto e informativo."""


def _parse_summary_response(response: str) -> str | None:
    """
    Extrai o bloco Resumo e a linha Hashtags da resposta do modelo.
    Retorna texto no formato "resumo\n\n#cat #..." ou None se não conseguir parsear.
    """
    if not response or not response.strip():
        return None
    text = response.strip()
    # Procura "Resumo:" e "Hashtags:" (case insensitive, com possíveis espaços)
    resumo_match = re.search(r"Resumo\s*:\s*(.+?)(?=Hashtags\s*:|\Z)", text, re.DOTALL | re.IGNORECASE)
    hashtags_match = re.search(r"Hashtags\s*:\s*(.+)", text, re.DOTALL | re.IGNORECASE)
    resumo = resumo_match.group(1).strip() if resumo_match else None
    hashtags_line = hashtags_match.group(1).strip() if hashtags_match else None
    if hashtags_line:
        # uma linha só, sem quebras
        hashtags_line = " ".join(hashtags_line.split())
    if resumo and hashtags_line:
        return f"{resumo}\n\n{hashtags_line}"
    if resumo:
        return resumo
    if hashtags_line:
        return hashtags_line
    # Fallback: retorna a resposta inteira (pode ser útil)
    return text[:2000]


def generate_summary(
    transcription: str | None,
    description: str | None,
    *,
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
) -> str | None:
    """
    Gera resumo + hashtags (10 no total; primeira = categoria) a partir da transcrição e da descrição.
    Retorna o texto formatado para usar como caption, ou None em caso de erro.
    """
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai não instalado; resumo desabilitado")
        return None

    if not api_key or not api_key.strip():
        return None

    has_transcription = bool(transcription and transcription.strip())
    has_description = bool(description and description.strip())
    if not has_transcription and not has_description:
        return None

    parts = []
    if has_transcription:
        parts.append("Transcrição do vídeo:\n" + (transcription or "").strip())
    if has_description:
        parts.append("Descrição do post do Instagram:\n" + (description or "").strip())
    user_content = "\n\n---\n\n".join(parts)

    client = OpenAI(api_key=api_key.strip())
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=1024,
        )
        choice = response.choices[0] if response.choices else None
        if not choice or not choice.message or not choice.message.content:
            return None
        return _parse_summary_response(choice.message.content)
    except Exception as e:
        logger.warning("Erro ao gerar resumo: %s", e)
        return None
