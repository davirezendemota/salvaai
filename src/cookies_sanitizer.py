"""Sanitização do arquivo cookies.txt para evitar injeção de conteúdo malicioso."""

import re

# Tamanho máximo do arquivo (512 KB é mais que suficiente para cookies)
MAX_COOKIES_FILE_SIZE = 512 * 1024

# Padrões considerados perigosos (script injection, etc.)
DANGEROUS_PATTERNS = re.compile(
    r"<\s*script|</\s*script|javascript\s*:|vbscript\s*:|data\s*:\s*text/html|"
    r"on\w+\s*=|expression\s*\(|<\s*iframe|<\s*object|<\s*embed|"
    r"eval\s*\(|document\.|window\.|\.cookie\s*=",
    re.IGNORECASE,
)

# Caracteres de controle permitidos em texto (newline, tab)
ALLOWED_CONTROL = frozenset({0x09, 0x0A, 0x0D})


def sanitize_cookies_content(raw: bytes) -> str:
    """
    Valida e sanitiza o conteúdo de um arquivo cookies (formato Netscape).
    Levanta ValueError se o conteúdo for inválido ou contiver padrões perigosos.
    """
    if len(raw) > MAX_COOKIES_FILE_SIZE:
        raise ValueError(f"Arquivo muito grande (máximo {MAX_COOKIES_FILE_SIZE // 1024} KB)")

    if b"\x00" in raw:
        raise ValueError("Arquivo contém caracteres nulos (inválido)")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Arquivo deve ser texto UTF-8 (formato Netscape)")

    if DANGEROUS_PATTERNS.search(text):
        raise ValueError("Conteúdo não permitido no arquivo de cookies")

    # Rejeitar outros caracteres de controle (exceto tab, LF, CR)
    for i, c in enumerate(text):
        if ord(c) < 0x20 and ord(c) not in ALLOWED_CONTROL:
            raise ValueError("Arquivo contém caracteres de controle inválidos")

    # Validar que as linhas parecem formato Netscape: comentário (#), vazia, ou campos separados por tab
    lines = text.splitlines()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # Linha de cookie: deve ter tabs (pelo menos 6 para domínio, flag, path, secure, expiry, name, value)
        if "\t" not in line:
            raise ValueError("Formato inválido: use arquivo cookies no formato Netscape (campos separados por tab)")
        # Não permitir conteúdo que pareça HTML/script na linha
        if "<" in line or ">" in line:
            raise ValueError("Conteúdo não permitido no arquivo de cookies")

    return text
