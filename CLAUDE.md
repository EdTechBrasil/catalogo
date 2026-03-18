# ExtratorDados — Contexto do Projeto

## O que é

Catalogador de PDFs educacionais da **Gráfica Educar**. Extrai metadados (ISBN, coleção,
ilustradores, sinopse, ano) da ficha CIP (página 2 dos PDFs INICIAIS) e gera um catálogo
em Excel/CSV. Interface web (FastAPI + Vanilla JS SPA) para edição interativa.

**Rodar:**
```bash
# Servidor FastAPI (desenvolvimento)
cd api && ../.venv/bin/uvicorn index:app --reload --port 8000

# ou via Vercel CLI (produção local):
vercel dev

# ou via CLI (sem UI):
.venv/bin/python main.py
```

---

## Mapa de arquivos

| Arquivo | Responsabilidade |
|---------|-----------------|
| `api/index.py` | FastAPI backend — todos os endpoints REST |
| `public/index.html` | SPA shell — estrutura HTML da interface |
| `public/app.js` | Lógica da SPA — PDF.js, AG Grid, fetch, exportação |
| `public/style.css` | Estilos da SPA |
| `scanner.py` | Varre pastas; agrupa PDFs por chave; chama `extract_metadata` |
| `pdf_metadata.py` | Extração híbrida LLM + regex da ficha CIP (página 2) |
| `pdf_reader.py` | Contagem de páginas usando `pypdf` |
| `excel_writer.py` | Gera `.xlsx` com 3 abas formatadas |
| `main.py` | Entry point CLI |
| `config.py` | Constantes de configuração (SOURCE_FOLDER) |

---

## Estrutura de pastas esperada para os PDFs

```
/Users/usuario/Downloads/GRÁFICA EDUCAR/
├── PRÉ 1/
├── 1º ANO/
│   └── MAT - EDUCAÇÃO DE QUALIDADE/      ← "Disciplina - Tema"
│       └── Atividades/                   ← tipo da pasta
│           ├── 1º_ANO_INICIAIS_LA_*.pdf  ← fonte de metadados (ficha CIP)
│           └── 1º_ANO_MIOLO_LA_*.pdf     ← fonte do nº de páginas
├── 2º ANO/
└── 3º ANO/
```

- `INICIAIS_LA` → páginas iniciais + ficha CIP → extração de metadados
- `MIOLO_LA` → corpo do livro → contagem de páginas
- Prioridade de metadados: `iniciais_la` > `iniciais_lp` > `miolo_la` > qualquer PDF

---

## Constantes críticas

```python
# config.py
SOURCE_FOLDER  = "/Users/usuario/Downloads/GRÁFICA EDUCAR"

# scanner.py
SERIE_MAP = {
    "PRÉ 1":  "Pré-Escola / Pré I",
    "PRÉ 2":  "Pré-Escola / Pré II",
    "1º ANO": "1º Ano",
    "2º ANO": "2º Ano",
    "3º ANO": "3º Ano",
}
TIPO_SUFFIX = {
    "atividades": "livro de atividades",
    "desafios":   "livro de desafios",
    "ilustrado":  "livro ilustrado",
    "tapetes":    "Tapetes",
}

# excel_writer.py
COLUMNS = [
    "Item", "Opção", "Coleção", "Faixa etária / nível", "Título",
    "Autor(es)", "Ilustrador(es) 1", "Ilustrador(es) 2", "ISBN", "Ano de publicação",
    "Número de páginas", "Sinopse", "Preço unitário", "Material de apoio pedagógico",
]
```

---

## API TESS IA (LLM)

```python
# pdf_metadata.py
TESS_API_KEY  = "603628|8M6zIovdmUSxFTAEXnTBHSiCCcHGwb2WCXdu3ZG755e2b4e8"
TESS_AGENT_ID = 41394
TESS_MODEL    = "claude-3-5-haiku-latest"
TESS_ENDPOINT = "https://api.tess.im/agents/41394/openai/chat/completions"
```

Interface OpenAI-compatible (Bearer token no header Authorization). Timeout 60s.

---

## Fluxo de extração de metadados (`pdf_metadata.py`)

1. Abre a **página 2** do PDF INICIAIS com `pdfplumber`
2. Envia texto completo ao LLM (TESS) → recebe JSON com os campos abaixo
3. **Híbrido:** campos que o LLM deixou `""` são preenchidos pelo regex (`_extract_via_regex_fill_gaps`)
4. Log no console: `[LLM] OK — <path>` ou `[LLM] FALHOU — <path>`

### Campos do dict `meta` (saída de `extract_metadata`)

```python
{
    "isbn":           "",   # ex: "978-65-84577-34-3"
    "ano":            "",   # ex: "2022"
    "colecao":        "",   # ex: "Meu Primeiro Código"
    "autor":          "",   # ex: "Editora Gráfica Educar"
    "ilustradores_1": "",   # ex: "João Silva, Maria Souza"
    "ilustradores_2": "",   # segundo grupo, se houver
    "sinopse":        "",   # ex: "1. Matemática. 2. Educação básica."
}
```

### Regex — layout de duas colunas (threshold x=300px)
- Coluna esquerda → ilustradores (detecta seção "Ilustra", captura nomes até próximo header)
- Coluna direita → bloco CIP → ISBN, ano, coleção, sinopse

---

## Cores do Excel

```python
COR_HEADER_BG   = "1F4E79"  # azul escuro — cabeçalho
COR_HEADER_FG   = "FFFFFF"  # branco
COR_LINHA_PAR   = "D6E4F0"  # azul claro — linhas pares
COR_LINHA_IMPAR = "FFFFFF"  # branco — linhas ímpares
COR_RESUMO_BG   = "2E75B6"  # azul médio — resumo
COR_TOTAL_BG    = "1F4E79"  # azul escuro — totais
COR_SUBHEADER   = "BDD7EE"  # azul claro — subcabeçalho
```

---

## Decisões de design registradas

- **Migração Streamlit → FastAPI+SPA:** o frontend foi migrado de `app.py` (Streamlit) para
  uma SPA Vanilla JS (`public/`) servida pelo FastAPI. `app.py` e `sheets_client.py` foram
  removidos do repositório.
- **Extração no browser:** o fluxo principal usa PDF.js no browser para extrair texto dos PDFs
  e envia ao endpoint `/api/process-text` — evita upload binário e reduz carga no servidor.
- **Ilustradores separados:** o LLM retorna `ilustradores_1` e `ilustradores_2` diretamente —
  não dividir lista ao meio (decisão anterior era errada).
- **Regex só preenche gaps:** nunca sobrescreve campo que o LLM já preencheu.
- **Export CSV:** `api/index.py` usa `df.to_csv()` direto — não existe `csv_writer.py`.
- **Título formatado:** `"{tema} - {sufixo}"` onde sufixo vem de `TIPO_SUFFIX`.

---

## Comandos úteis de diagnóstico

```bash
# Testar extração de metadados de um PDF
.venv/bin/python -c "
from pdf_metadata import extract_metadata
print(extract_metadata('/Users/usuario/Downloads/GRÁFICA EDUCAR/1º ANO/MAT - EDUCAÇÃO DE QUALIDADE/Atividades/1º_ANO_LA_ATV_MAT_EDUCAÇÃO_DE_QUALIDADE.pdf'))
"

# Verificar que API importa sem erros
cd api && ../.venv/bin/python -c "import index; print('OK')"

# Verificar que módulos core importam sem erros
.venv/bin/python -c "import scanner; import excel_writer; import pdf_metadata; print('OK')"
```
