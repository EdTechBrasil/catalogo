"""
Extrai metadados da ficha técnica (CIP) dos arquivos INICIAIS (página 2).
Usa Claude API (LLM) como extrator principal; fallback para regex/pdfplumber.
"""
import os
import re
import json
import pdfplumber
from collections import defaultdict

# ---------------------------------------------------------------------------
# Helpers para layout de duas colunas (usados no fallback regex)
# ---------------------------------------------------------------------------

def _group_words_into_lines(words: list, x_threshold=300) -> tuple[list[str], list[str]]:
    """Agrupa palavras em linhas para coluna esquerda e direita."""
    left_lines: dict[int, list] = defaultdict(list)
    right_lines: dict[int, list] = defaultdict(list)

    for w in words:
        row_key = round(w["top"] / 5) * 5
        if w["x0"] < x_threshold:
            left_lines[row_key].append(w["text"])
        else:
            right_lines[row_key].append(w["text"])

    left = [" ".join(v) for k, v in sorted(left_lines.items())]
    right = [" ".join(v) for k, v in sorted(right_lines.items())]
    return left, right


SECTION_HEADERS = {
    "Edição", "Revisão", "Diagramação", "Projeto", "Coordenação",
    "Dados", "Impressão", "Produção", "Organização", "Pesquisa",
    "Capa", "Autores", "Texto",
}


def _extract_illustrators_from_lines(left_lines: list) -> str:
    """Extrai ilustradores das linhas da coluna esquerda baseado em texto, não posição."""
    capturing = False
    names = []
    skip = {"e", "Iconografia", "Ilustração", "e Iconografia"}
    for line in left_lines:
        if "Ilustra" in line:
            capturing = True
            continue
        if not capturing:
            continue
        if any(hdr in line for hdr in SECTION_HEADERS):
            break
        name = line.strip()
        if name and name not in skip:
            names.append(name)
    return ", ".join(names)


def _clean_cip_text(right_lines: list[str]) -> str:
    """Extrai o bloco CIP das linhas da coluna direita."""
    start = None
    for i, line in enumerate(right_lines):
        if "Dados Internacionais" in line or "CIP" in line:
            start = i
            break
    if start is None:
        return "\n".join(right_lines)
    return "\n".join(right_lines[start:])


# ---------------------------------------------------------------------------
# Normalização de ISBN
# ---------------------------------------------------------------------------

def _normalize_isbn(raw: str) -> str:
    """Normaliza qualquer string para ISBN-13 (XXX-XX-XXXXX-XX-X) ou ISBN-10 (X-XX-XXXXXX-X).
    Retorna '' se a quantidade de dígitos não for 13 nem 10."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 13:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:10]}-{digits[10:12]}-{digits[12]}"
    if len(digits) == 10:
        return f"{digits[0]}-{digits[1:3]}-{digits[3:9]}-{digits[9]}"
    return ""


_ISBN_RE = re.compile(
    r"ISBN(?:[- ]?1[03])?[:\s]*([0-9][0-9 \-\.–]{8,}[0-9X])",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# LLM via TESS IA (OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------

TESS_API_KEY    = os.environ.get("TESS_API_KEY", "603628|8M6zIovdmUSxFTAEXnTBHSiCCcHGwb2WCXdu3ZG755e2b4e8")
TESS_AGENT_ID   = 41394
TESS_MODEL      = "claude-4.5-haiku"
TESS_ENDPOINT   = f"https://api.tess.im/agents/{TESS_AGENT_ID}/openai/chat/completions"



def _extract_via_llm(cip_text: str, content_text: str = "") -> dict:
    """Extrai metadados usando TESS IA. Retorna dict vazio em caso de falha."""
    import requests

    prompt = f"""Você receberá o texto extraído de um livro educacional da Gráfica Educar.

## TAREFA 1 — Extração de metadados bibliográficos
Procure em QUALQUER parte do texto pelos campos abaixo.
NÃO invente — retorne "" se genuinamente não encontrar.

- isbn: número ISBN-13 no formato canônico "XXX-XX-XXXXX-XX-X"
- ano: ano de publicação com 4 dígitos (ex: "2023")
- colecao: nome da coleção ou série editorial (entre parênteses ou após "Coleção" / "Série")
- autor: nome(s) do(s) autor(es) / organizador(es) da obra, separados por vírgula
  Dica: procure seções "Texto", "Autoria", "Autor", "Organização", "Escrito por"
- ilustradores_1: nomes do primeiro grupo de ilustradores, separados por vírgula
  Dica: procure seções "Ilustração", "Ilustradores", "Desenhos"
- ilustradores_2: segundo grupo de ilustradores, se houver

## TAREFA 2 — Sinopse editorial
Com base em TODO o texto disponível, escreva uma sinopse de 2 a 3 frases para catálogo
editorial descrevendo: disciplina/tema, faixa etária/nível escolar, abordagem pedagógica.
Escreva em português, texto corrido. NÃO copie entradas bibliográficas.
Se não houver texto suficiente para uma sinopse, retorne "".

=== TEXTO DO DOCUMENTO ===
{cip_text}

=== AMOSTRA DO CONTEÚDO ===
{content_text}"""

    try:
        resp = requests.post(
            TESS_ENDPOINT,
            headers={
                "Authorization": f"Bearer {TESS_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": TESS_MODEL,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        status = getattr(getattr(e, 'response', None), 'status_code', None)
        body = ""
        try:
            body = e.response.text[:300]
        except Exception:
            pass
        print(f"  [LLM ERRO] {type(e).__name__}: {e} | status={status} | resp={body!r}")
        return {}


# ---------------------------------------------------------------------------
# Fallback: extração via regex (lógica original)
# ---------------------------------------------------------------------------

def _extract_via_regex_fill_gaps(meta: dict, words: list, left_lines: list, right_lines: list) -> None:
    """Preenche in-place APENAS os campos vazios de meta usando regex."""
    cip_text = _clean_cip_text(right_lines)

    # Remove APENAS espaços (não newlines) entre dígitos consecutivos
    cip_clean = re.sub(r"(?<=\d) (?=\d)", "", cip_text)

    # ISBN
    if not meta["isbn"]:
        text_condensed = re.sub(r"(?<=\d) (?=\d)", "", cip_text)
        for candidate in (text_condensed, re.sub(r"\s+", "", cip_text)):
            matches = _ISBN_RE.findall(candidate)
            for raw in matches:
                normalized = _normalize_isbn(raw)
                if normalized and len(re.sub(r"\D", "", raw)) == 13:
                    meta["isbn"] = normalized
                    break
            if meta["isbn"]:
                break
            for raw in matches:
                normalized = _normalize_isbn(raw)
                if normalized:
                    meta["isbn"] = normalized
                    break
            if meta["isbn"]:
                break
        # Fallback: busca sequência de 13 dígitos começando com 978/979 (sem prefixo "ISBN")
        if not meta["isbn"]:
            bare = re.search(r"\b(97[89][\d\s\-\.–]{10,17})\b", text_condensed)
            if bare:
                normalized = _normalize_isbn(bare.group(1))
                if normalized:
                    meta["isbn"] = normalized

    # Ano de publicação
    if not meta["ano"]:
        year_match = re.search(r"\b(20\d{2})\b", cip_clean)
        if year_match:
            meta["ano"] = year_match.group(1)

    # Coleção
    if not meta["colecao"]:
        colecao_match = re.search(r"\(([Mm]eu\s+[Pp]rimeiro\s+[Cc][oó]digo[^)]*)\)", cip_clean)
        if colecao_match:
            meta["colecao"] = "Meu Primeiro Código"
        else:
            colecao_match2 = re.search(r"p\.\s*:.*?[–\-]\s*\(([^)]+)\)", cip_clean)
            if colecao_match2:
                meta["colecao"] = colecao_match2.group(1).replace("Projeto ", "").strip()

    # Sinopse
    if not meta["sinopse"]:
        sinopse_match = re.search(
            r"(1\.\s+(?!ed\.\s)(?!a\s+ed)[A-ZÀ-Ú].+?)(?:\s+I\.\s+|\s+CDD\s+|\s+Índices)",
            cip_clean,
            re.DOTALL,
        )
        if sinopse_match:
            meta["sinopse"] = re.sub(r"\s+", " ", sinopse_match.group(1)).strip()

    # Autor / organizador
    if not meta.get("autor"):
        autor_match = re.search(
            r"(?:Texto|Autori[ae]|Autor[ae]?s?|Organiza[çc][aã]o)[:\s]+([^\n]+(?:\n[^\n]+){0,2})",
            "\n".join(left_lines), re.IGNORECASE,
        )
        if autor_match:
            names = [
                n.strip() for n in re.split(r"[,\n]", autor_match.group(1))
                if n.strip() and not any(h in n for h in SECTION_HEADERS)
            ]
            meta["autor"] = ", ".join(names)

    # Ilustradores (apenas se ambos vazios)
    if not meta["ilustradores_1"]:
        all_ilu = _extract_illustrators_from_lines(left_lines)
        names = [n.strip() for n in all_ilu.split(",") if n.strip()]
        mid = len(names) // 2 if len(names) > 2 else len(names)
        meta["ilustradores_1"] = ", ".join(names[:mid])
        meta["ilustradores_2"] = ", ".join(names[mid:]) if len(names) > mid else ""


# ---------------------------------------------------------------------------
# Função pública
# ---------------------------------------------------------------------------

def extract_metadata(iniciais_pdf_path: str, miolo_pdf_path: str = None) -> dict:
    """
    Extrai: isbn, ano, colecao, ilustradores, sinopse
    das páginas iniciais de um arquivo INICIAIS.
    Tenta LLM primeiro; fallback para regex se a API falhar.
    Aceita miolo_pdf_path opcional para enriquecer a sinopse gerada pelo LLM.
    """
    meta = {
        "isbn": "",
        "ano": "",
        "colecao": "",
        "autor": "",
        "ilustradores_1": "",
        "ilustradores_2": "",
        "sinopse": "",
    }

    try:
        with pdfplumber.open(iniciais_pdf_path) as pdf:
            if len(pdf.pages) < 2:
                return meta

            # Página 2 para o fallback regex
            page = pdf.pages[1]
            words = page.extract_words()
            if not words:
                return meta

            # Texto completo do INICIAIS (todas as páginas)
            cip_text = "\n".join(
                p.extract_text() or "" for p in pdf.pages
            ).strip()

            # Amostra do MIOLO (primeiras 3 páginas, limitado a 2000 chars)
            content_text = ""
            if miolo_pdf_path:
                try:
                    with pdfplumber.open(miolo_pdf_path) as mpdf:
                        content_text = "\n".join(
                            p.extract_text() or "" for p in mpdf.pages[:3]
                        ).strip()[:2000]
                except Exception as e:
                    print(f"  [AVISO] Não foi possível ler miolo {miolo_pdf_path}: {e}")

            # Tenta LLM primeiro
            llm_result = _extract_via_llm(cip_text, content_text)
            print(f"  [LLM] {'OK' if llm_result else 'FALHOU'} — {iniciais_pdf_path}")

            if llm_result:
                meta["isbn"]           = _normalize_isbn(llm_result.get("isbn", ""))
                meta["ano"]            = llm_result.get("ano", "")
                meta["colecao"]        = llm_result.get("colecao", "")
                meta["autor"]          = llm_result.get("autor", "")
                meta["sinopse"]        = llm_result.get("sinopse", "")
                meta["ilustradores_1"] = llm_result.get("ilustradores_1", "")
                meta["ilustradores_2"] = llm_result.get("ilustradores_2", "")

            # Complementa campos ainda vazios com regex (híbrido LLM + regex)
            if any(not meta[k] for k in ("isbn", "ano", "colecao", "autor", "sinopse", "ilustradores_1")):
                left_lines, right_lines = _group_words_into_lines(words)
                _extract_via_regex_fill_gaps(meta, words, left_lines, right_lines)

    except Exception as e:
        print(f"  [AVISO] Erro ao extrair metadados de {iniciais_pdf_path}: {e}")

    return meta


def _extract_via_regex_text(meta: dict, text: str) -> None:
    """Preenche campos vazios via regex em texto plano (sem layout de colunas)."""

    # ISBN
    if not meta["isbn"]:
        text_condensed = re.sub(r"(?<=\d) (?=\d)", "", text)
        for candidate in (text_condensed, re.sub(r"\s+", "", text)):
            matches = _ISBN_RE.findall(candidate)
            for raw in matches:
                normalized = _normalize_isbn(raw)
                if normalized and len(re.sub(r"\D", "", raw)) == 13:
                    meta["isbn"] = normalized
                    break
            if meta["isbn"]:
                break
            for raw in matches:
                normalized = _normalize_isbn(raw)
                if normalized:
                    meta["isbn"] = normalized
                    break
            if meta["isbn"]:
                break
        # Fallback: busca sequência de 13 dígitos começando com 978/979 (sem prefixo "ISBN")
        if not meta["isbn"]:
            bare = re.search(r"\b(97[89][\d\s\-\.–]{10,17})\b", text_condensed)
            if bare:
                normalized = _normalize_isbn(bare.group(1))
                if normalized:
                    meta["isbn"] = normalized

    # Ano
    if not meta["ano"]:
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            meta["ano"] = year_match.group(1)

    # Coleção
    if not meta["colecao"]:
        colecao_match = re.search(r"\(([Mm]eu\s+[Pp]rimeiro\s+[Cc][oó]digo[^)]*)\)", text)
        if colecao_match:
            meta["colecao"] = "Meu Primeiro Código"
        else:
            colecao_match2 = re.search(r"p\.\s*:.*?[–\-]\s*\(([^)]+)\)", text)
            if colecao_match2:
                meta["colecao"] = colecao_match2.group(1).replace("Projeto ", "").strip()

    # Sinopse
    if not meta["sinopse"]:
        sinopse_match = re.search(
            r"(1\.\s+(?!ed\.\s)(?!a\s+ed)[A-ZÀ-Ú].+?)(?:\s+I\.\s+|\s+CDD\s+|\s+Índices)",
            text, re.DOTALL,
        )
        if sinopse_match:
            meta["sinopse"] = re.sub(r"\s+", " ", sinopse_match.group(1)).strip()

    # Autor / organizador
    if not meta.get("autor"):
        autor_match = re.search(
            r"(?:Texto|Autori[ae]|Autor[ae]?s?|Organiza[çc][aã]o)[:\s]+([^\n]+(?:\n[^\n]+){0,2})",
            text, re.IGNORECASE,
        )
        if autor_match:
            names = [
                n.strip() for n in re.split(r"[,\n]", autor_match.group(1))
                if n.strip() and not any(h in n for h in SECTION_HEADERS)
            ]
            if names:
                meta["autor"] = ", ".join(names)

    # Ilustradores
    if not meta["ilustradores_1"]:
        ilu_match = re.search(
            r"Ilustra[çc][aã]o[:\s]+([^\n]+(?:\n[^\n]+){0,3})", text
        )
        if ilu_match:
            names = [
                n.strip() for n in re.split(r"[,\n]", ilu_match.group(1))
                if n.strip() and len(n.strip()) > 2
                and not any(h in n for h in SECTION_HEADERS)
            ]
            if names:
                mid = len(names) // 2 if len(names) > 2 else len(names)
                meta["ilustradores_1"] = ", ".join(names[:mid])
                if len(names) > mid:
                    meta["ilustradores_2"] = ", ".join(names[mid:])


def extract_metadata_from_text(cip_text: str, content_text: str = "") -> dict:
    """
    Versão de extract_metadata que opera sobre texto já extraído
    (sem abrir o PDF — usado quando a extração é feita no browser).
    """
    meta = {
        "isbn": "", "ano": "", "colecao": "", "autor": "",
        "ilustradores_1": "", "ilustradores_2": "", "sinopse": "",
    }

    if not cip_text.strip():
        print("  [LLM] Texto vazio — pulando extração")
        return meta

    llm_result = _extract_via_llm(cip_text, content_text)
    print(f"  [LLM] {'OK' if llm_result else 'FALHOU'} — (text input)")

    if llm_result:
        meta["isbn"]           = _normalize_isbn(llm_result.get("isbn", ""))
        meta["ano"]            = llm_result.get("ano", "")
        meta["colecao"]        = llm_result.get("colecao", "")
        meta["autor"]          = llm_result.get("autor", "")
        meta["sinopse"]        = llm_result.get("sinopse", "")
        meta["ilustradores_1"] = llm_result.get("ilustradores_1", "")
        meta["ilustradores_2"] = llm_result.get("ilustradores_2", "")

    if any(not meta[k] for k in ("isbn", "ano", "colecao", "autor", "sinopse", "ilustradores_1")):
        _extract_via_regex_text(meta, cip_text)

    return meta


# ---------------------------------------------------------------------------
# Wrapper async para paralelismo no servidor
# ---------------------------------------------------------------------------

import asyncio
from concurrent.futures import ThreadPoolExecutor

_LLM_EXECUTOR = ThreadPoolExecutor(max_workers=5)


async def extract_metadata_from_text_async(cip_text: str, content_text: str = "") -> dict:
    """Versão async — executa LLM em thread pool para permitir paralelismo."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _LLM_EXECUTOR, extract_metadata_from_text, cip_text, content_text
    )
