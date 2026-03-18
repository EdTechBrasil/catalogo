# API — Catálogo Gráfica Educar

Base URL em desenvolvimento: `http://localhost:8000`

---

## `POST /api/process-text`

Fluxo principal. Recebe texto já extraído pelo browser (via PDF.js) e retorna os records processados.

### Request

```json
{
  "files": [
    {
      "filename": "1º_ANO_INICIAIS_LA_MAT.pdf",
      "text": "...texto da página 2...",
      "page_count": 0
    },
    {
      "filename": "1º_ANO_MIOLO_LA_MAT.pdf",
      "text": "...primeiras páginas...",
      "page_count": 128
    }
  ]
}
```

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `filename` | string | Nome do arquivo — usado para inferir série, tipo, tema |
| `text` | string | Texto extraído do PDF (página 2 para INICIAIS) |
| `page_count` | int | Número de páginas (relevante para MIOLO; 0 se não disponível) |

### Response

```json
{
  "records": [
    {
      "Item": 1,
      "Opção": 1,
      "Coleção": "Meu Primeiro Código",
      "Faixa etária / nível": "1º Ano",
      "Título": "Matemática - livro de atividades",
      "Autor(es)": "Editora Gráfica Educar",
      "Ilustrador(es) 1": "João Silva",
      "Ilustrador(es) 2": "",
      "ISBN": "978-65-84577-34-3",
      "Ano de publicação": "2022",
      "Número de páginas": 128,
      "Sinopse": "1. Matemática. 2. Educação básica.",
      "Preço unitário": "",
      "Material de apoio pedagógico": ""
    }
  ]
}
```

---

## `POST /api/upload`

Fallback — processamento binário no servidor. Aceita ZIPs (com estrutura de pastas) ou PDFs avulsos.

### Request

`multipart/form-data` com campo `files` (um ou mais arquivos `.zip` ou `.pdf`).

- Se houver ZIP: extrai em diretório temporário e chama `scanner.scan_and_group()`
- Se forem PDFs: agrupa por nome de arquivo e processa individualmente

### Response

Mesmo formato de `/api/process-text`.

---

## `POST /api/import`

Importa um catálogo existente em XLSX ou CSV.

### Request

`multipart/form-data` com campo `file` (`.xlsx`, `.xls` ou `.csv`).

- XLSX: detecta automaticamente abas com dados de catálogo (≥4 colunas reconhecidas)
- CSV: lê com encoding UTF-8 BOM

Nomes de colunas são normalizados (sem acentos, case-insensitive) para mapear variações.

### Response

```json
{
  "records": [ { ...mesmo formato acima... } ]
}
```

**Erro 422:** nenhuma aba com dados de catálogo encontrada no XLSX.

---

## `POST /api/export/excel`

Gera arquivo `.xlsx` com 3 abas formatadas (Catálogo, Resumo por série, Totais).

### Request

```json
{
  "records": [ { ...records atuais do grid... } ]
}
```

### Response

Arquivo `catalogo_grafica_educar.xlsx` para download.

`Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`

---

## `POST /api/export/csv`

Gera arquivo `.csv` com encoding UTF-8 BOM (compatível com Excel).

### Request

```json
{
  "records": [ { ...records atuais do grid... } ]
}
```

### Response

Arquivo `catalogo_grafica_educar.csv` para download.

`Content-Type: text/csv; charset=utf-8-sig`

---

## Inferência de metadados a partir do nome do arquivo

O helper `_infer_from_filename()` extrai:

| Campo | Detectado por |
|-------|--------------|
| `serie` | Palavras-chave: `PRÉ_1`, `1º_ANO`, `2_ANO`, etc. |
| `tipo` | Sufixos: `ATV`→atividades, `DES`→desafios, `ILU`→ilustrado, `TAP`→tapetes |
| `variante` | `LA` (livro do aluno) ou `LP` (livro do professor) |
| `tema` | Partes restantes do nome (ex: `MATEMATICA`, `CIENCIAS`) |
