# Arquitetura — ExtratorDados

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | FastAPI (Python 3.11+) |
| Frontend | Vanilla JS SPA (sem framework) |
| Grid | AG Grid (Community) |
| Extração PDF (browser) | PDF.js |
| Extração PDF (servidor) | pdfplumber |
| LLM | TESS IA / claude-3-5-haiku (API OpenAI-compatible) |
| Export | openpyxl (Excel), pandas (CSV) |
| Deploy | Vercel (serverless) |

---

## Estrutura de pastas

```
ExtratorDados/
├── api/
│   ├── index.py          # FastAPI app — todos os endpoints
│   └── README.md         # Documentação dos endpoints
├── public/
│   ├── index.html        # SPA shell
│   ├── app.js            # Lógica da SPA (PDF.js, AG Grid, fetch)
│   └── style.css         # Estilos
├── config.py             # SOURCE_FOLDER
├── scanner.py            # Varredura de pastas de PDFs
├── pdf_metadata.py       # Extração LLM + regex
├── pdf_reader.py         # Contagem de páginas
├── excel_writer.py       # Geração de .xlsx
├── main.py               # Entry point CLI
├── requirements.txt
├── vercel.json
└── CLAUDE.md
```

---

## Mapa de módulos e dependências

```
config.py
    └── SOURCE_FOLDER

scanner.py
    ├── imports: config.SOURCE_FOLDER, pdf_metadata, pdf_reader
    ├── exports: scan_and_group(), SERIE_MAP, TIPO_SUFFIX
    └── responsabilidade: varre pastas, agrupa PDFs por (serie, tipo, tema)

pdf_metadata.py
    ├── imports: pdfplumber, requests (TESS API)
    ├── exports: extract_metadata(path), extract_metadata_from_text(text, context)
    └── responsabilidade: extração LLM + regex, híbrido fill-gaps

pdf_reader.py
    ├── imports: pypdf
    ├── exports: get_page_count(path)
    └── responsabilidade: conta páginas de um PDF

excel_writer.py
    ├── imports: openpyxl, pandas
    ├── exports: write_excel_to_bytes(records), COLUMNS
    └── responsabilidade: gera .xlsx com 3 abas formatadas

api/index.py
    ├── imports: fastapi, scanner, pdf_metadata, pdf_reader, excel_writer
    └── responsabilidade: endpoints REST, serve SPA estática

public/app.js
    ├── usa: PDF.js (CDN), AG Grid (CDN)
    └── responsabilidade: UI, extração de texto no browser, fetch para API
```

---

## Grafo de imports (simplificado)

```
api/index.py
 ├── excel_writer  ──→  openpyxl, pandas
 ├── pdf_metadata  ──→  pdfplumber, requests
 ├── pdf_reader    ──→  pypdf
 └── scanner       ──→  pdf_metadata, pdf_reader, config
```

---

## Fluxos de dados

### Fluxo 1 — Upload ZIP/PDF (fluxo servidor)
```
Browser: seleciona ZIP/PDFs
  → POST /api/upload (multipart)
    → extrai ZIP em tmpdir (ou grava PDFs avulsos)
    → scanner.scan_and_group() ou agrupamento manual
      → pdf_metadata.extract_metadata() → TESS LLM + regex
      → pdf_reader.get_page_count()
    → retorna records[]
  → AG Grid renderiza tabela
```

### Fluxo 2 — Processar texto (fluxo principal / browser)
```
Browser: seleciona PDFs
  → PDF.js extrai texto de cada PDF no browser
  → POST /api/process-text (JSON: [{filename, text, page_count}])
    → agrupa por (serie, tipo, tema) via _infer_from_filename()
    → pdf_metadata.extract_metadata_from_text() → TESS LLM + regex
    → retorna records[]
  → AG Grid renderiza tabela
```

### Fluxo 3 — Importar planilha existente
```
Browser: seleciona .xlsx ou .csv
  → POST /api/import (multipart)
    → pandas lê arquivo, detecta abas com dados de catálogo
    → normaliza nomes de colunas
    → retorna records[]
  → AG Grid renderiza tabela
```

### Fluxo 4 — Exportar
```
AG Grid: dados editados pelo usuário
  → POST /api/export/excel ou /api/export/csv (JSON: {records})
    → excel_writer.write_excel_to_bytes() ou df.to_csv()
    → retorna arquivo para download
```

---

## Endpoints da API

| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/api/upload` | Upload binário de ZIPs ou PDFs; extração no servidor |
| `POST` | `/api/process-text` | Recebe texto extraído pelo browser; extração via LLM |
| `POST` | `/api/import` | Importa XLSX ou CSV existente |
| `POST` | `/api/export/excel` | Gera e retorna `.xlsx` formatado |
| `POST` | `/api/export/csv` | Gera e retorna `.csv` UTF-8 BOM |
| `GET` | `/*` | Serve SPA estática de `public/` |

Veja `api/README.md` para detalhes de payload e response de cada rota.

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

Convenção de nomes de arquivo:
- `INICIAIS` → páginas iniciais + ficha CIP → fonte de metadados
- `MIOLO` → corpo do livro → fonte de contagem de páginas
- Tipo inferido do nome: `ATV`=atividades, `DES`=desafios, `ILU`=ilustrado, `TAP`=tapetes
- Variante: `LA`=livro do aluno, `LP`=livro do professor
