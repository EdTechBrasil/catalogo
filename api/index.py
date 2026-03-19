"""
FastAPI backend — Catálogo Gráfica Educar
Run: uvicorn api.index:app --reload --port 8000
"""
import asyncio
import io
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Adiciona raiz do projeto ao sys.path para importar módulos locais
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from excel_writer import COLUMNS, write_excel_to_bytes
from pdf_metadata import extract_metadata, extract_metadata_from_text, extract_metadata_from_text_async
from pdf_reader import get_page_count
from scanner import SERIE_MAP, TIPO_SUFFIX, scan_and_group

app = FastAPI(title="Catálogo Gráfica Educar")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Helpers ───────────────────────────────────────────────────────────────────

SERIE_KEYWORDS = {
    "PRÉ_1": "Pré-Escola / Pré I",
    "PRE_1": "Pré-Escola / Pré I",
    "PRÉ_2": "Pré-Escola / Pré II",
    "PRE_2": "Pré-Escola / Pré II",
    "1º_ANO": "1º Ano",
    "1_ANO": "1º Ano",
    "2º_ANO": "2º Ano",
    "2_ANO": "2º Ano",
    "3º_ANO": "3º Ano",
    "3_ANO": "3º Ano",
    # Série G (Grupos Pré-Escola)
    "G1": "Grupo 1 (Pré-Escola)",
    "G2": "Grupo 2 (Pré-Escola)",
    "G3": "Grupo 3 (Pré-Escola)",
    "G4": "Grupo 4 (Pré-Escola)",
    "G5": "Grupo 5 (Pré-Escola)",
    "G6": "Grupo 6 (Pré-Escola)",
}

TIPO_KEYWORDS = {
    "ATV": "livro de atividades",
    "DES": "livro de desafios",
    "ILU": "livro ilustrado",
    "TAP": "Tapetes",
}


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower().strip()


_COLUMNS_NORM = {_normalize(c): c for c in COLUMNS}


def _map_columns(df: pd.DataFrame) -> pd.DataFrame:
    ilu_idx = 0
    rename = {}
    for col in df.columns:
        norm = _normalize(col)
        if norm in _COLUMNS_NORM:
            rename[col] = _COLUMNS_NORM[norm]
        elif "ilustrador" in norm:
            ilu_idx += 1
            rename[col] = f"Ilustrador(es) {ilu_idx}"
    return df.rename(columns=rename)


def _infer_from_filename(name: str) -> dict:
    stem = os.path.splitext(name)[0].upper()
    parts = re.split(r"[_\-\s]+", stem)

    serie = ""
    for key, val in SERIE_KEYWORDS.items():
        if key in stem:
            serie = val
            break

    tipo = ""
    for key, val in TIPO_KEYWORDS.items():
        if key in parts:
            tipo = val
            break

    variante = ""
    for v in ("LA", "LP"):
        if v in parts:
            variante = v
            break

    skip = {"INICIAIS", "MIOLO", "CAPA", "LA", "LP", "ATV", "DES", "ILU", "TAP",
            "PRÉ", "PRE", "1", "2", "3", "ANO"}
    for k in SERIE_KEYWORDS:
        skip.update(k.split("_"))
    tema_parts = [
        p for p in parts
        if p not in skip and len(p) > 1
        and not re.match(r"^\d{4}$", p)
        and not re.match(r"^\(\d+\)$", p)
    ]
    tema = " ".join(tema_parts).title()

    return {"serie": serie, "tipo": tipo, "tema": tema, "variante": variante}


def _renumber(records: list[dict]) -> list[dict]:
    for i, r in enumerate(records, 1):
        r["Item"] = i
    return records


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """
    Recebe ZIPs ou PDFs. Retorna records[].
    Se houver ZIP, extrai e chama scan_and_group.
    Se forem PDFs avulsos, agrupa por nome de arquivo.
    """
    try:
        return await _do_upload(files)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


async def _do_upload(files: list[UploadFile]):
    zips = [f for f in files if f.filename.lower().endswith(".zip")]
    pdfs = [f for f in files if f.filename.lower().endswith(".pdf")]

    records = []

    if zips:
        uf = zips[0]
        content = await uf.read()
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                zf.extractall(tmpdir)
            records = scan_and_group(tmpdir)

    elif pdfs:
        groups: dict[tuple, dict] = {}

        for uf in pdfs:
            content = await uf.read()
            info = _infer_from_filename(uf.filename)
            key = (info["serie"], info["tipo"], info["tema"], info["variante"])
            if key not in groups:
                groups[key] = {"iniciais": None, "miolo": None, "uf": [], "content": {}}
            groups[key]["uf"].append(uf.filename)
            groups[key]["content"][uf.filename] = content
            name_up = uf.filename.upper()
            if "INICIAIS" in name_up:
                groups[key]["iniciais"] = uf.filename
            elif "MIOLO" in name_up:
                groups[key]["miolo"] = uf.filename

        with tempfile.TemporaryDirectory() as tmpdir:
            for (serie, tipo, tema, _variante), g in groups.items():
                all_names = list(g["content"].keys())
                meta_name = g["iniciais"] or (all_names[0] if all_names else None)
                pages_name = g["miolo"] or meta_name

                # Gravar arquivos necessários no tmpdir
                meta_path = None
                if meta_name:
                    meta_path = os.path.join(tmpdir, meta_name)
                    with open(meta_path, "wb") as f:
                        f.write(g["content"][meta_name])

                pages_path = None
                if pages_name:
                    pages_path = os.path.join(tmpdir, pages_name)
                    if not os.path.exists(pages_path):
                        with open(pages_path, "wb") as f:
                            f.write(g["content"][pages_name])

                meta = extract_metadata(meta_path) if meta_path else {}
                paginas = get_page_count(pages_path) if pages_path else 0

                titulo = f"{tema} - {tipo}" if tema and tipo else tema or tipo
                records.append({
                    "Item":                        len(records) + 1,
                    "Opção":                       1,
                    "Coleção":                     meta.get("colecao", ""),
                    "Faixa etária / nível":        serie,
                    "Título":                      titulo,
                    "Autor(es)":                   meta.get("autor", ""),
                    "Ilustrador(es) 1":            meta.get("ilustradores_1", ""),
                    "Ilustrador(es) 2":            meta.get("ilustradores_2", ""),
                    "ISBN":                        meta.get("isbn", ""),
                    "Ano de publicação":           meta.get("ano", ""),
                    "Número de páginas":           paginas,
                    "Sinopse":                     meta.get("sinopse", ""),
                    "Preço unitário":              "",
                    "Material de apoio pedagógico": "",
                })
    else:
        raise HTTPException(status_code=400, detail="Envie arquivos .zip ou .pdf")

    return {"records": _renumber(records)}


@app.post("/api/import")
async def import_file(file: UploadFile = File(...)):
    """Importa XLSX ou CSV existente e retorna records[]."""
    name = file.filename.lower()
    content = await file.read()

    try:
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", dtype=str).fillna("")
            df = _map_columns(df)
        elif name.endswith((".xlsx", ".xls")):
            xls = pd.ExcelFile(io.BytesIO(content))
            frames = []
            for sheet in xls.sheet_names:
                raw = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=str).fillna("")
                if raw.empty or raw.shape[1] < 5:
                    continue
                header_row = raw.iloc[0]
                matches = sum(
                    1 for v in header_row
                    if _normalize(str(v)) in _COLUMNS_NORM or "ilustrador" in _normalize(str(v))
                )
                if matches < 4:
                    continue
                df_sheet = pd.read_excel(xls, sheet_name=sheet, header=0, dtype=str).fillna("")
                frames.append(df_sheet)

            if not frames:
                raise HTTPException(status_code=422, detail="Nenhuma aba com dados de catálogo encontrada.")
            df = pd.concat(frames, ignore_index=True)
            df = _map_columns(df)
        else:
            raise HTTPException(status_code=400, detail="Formato não suportado. Use .xlsx ou .csv")

        for col in COLUMNS:
            if col not in df.columns:
                df[col] = ""
        df = df[COLUMNS]
        df = df[df.apply(lambda r: r.str.strip().ne("").any(), axis=1)].reset_index(drop=True)

        records = df.to_dict("records")
        return {"records": records}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ExportBody(BaseModel):
    records: list[dict]


class FileText(BaseModel):
    filename: str
    text: str
    page_count: int = 0

class ProcessTextPayload(BaseModel):
    files: list[FileText]


@app.post("/api/process-text")
async def process_text(payload: ProcessTextPayload):
    """
    Recebe texto já extraído dos PDFs (extração feita no browser).
    Agrupa por (serie, tipo, tema), chama LLM + regex, retorna records[].
    """
    try:
        groups: dict[tuple, dict] = {}

        for item in payload.files:
            info = _infer_from_filename(item.filename)
            key = (info["serie"], info["tipo"], info["tema"], info["variante"])
            if key not in groups:
                groups[key] = {"iniciais_text": "", "capa_text": "", "content_text": "", "page_count": 0}

            name_up = item.filename.upper()
            if "INICIAIS" in name_up:
                groups[key]["iniciais_text"] = item.text
            elif "MIOLO" in name_up:
                groups[key]["content_text"] = item.text[:8000]
                groups[key]["page_count"] = item.page_count
            elif "CAPA" in name_up:
                if not groups[key]["capa_text"]:
                    groups[key]["capa_text"] = item.text  # pode conter ficha CIP
                if not groups[key]["page_count"]:
                    groups[key]["page_count"] = item.page_count
            else:
                if not groups[key]["iniciais_text"]:
                    groups[key]["iniciais_text"] = item.text
                if not groups[key]["page_count"]:
                    groups[key]["page_count"] = item.page_count

        _empty_meta = {
            "isbn": "", "ano": "", "colecao": "", "autor": "",
            "ilustradores_1": "", "ilustradores_2": "", "sinopse": "",
        }

        async def _process_group(serie, tipo, tema, g):
            warning = None
            if g["iniciais_text"]:
                effective_text = g["iniciais_text"]
            else:
                # Sem INICIAIS: combinar CAPA + primeiras páginas do MIOLO
                # A ficha CIP pode estar em qualquer uma das duas fontes
                combined = "\n".join(filter(None, [g["capa_text"], g["content_text"]]))
                effective_text = combined[:12000]
            try:
                meta = await asyncio.wait_for(
                    extract_metadata_from_text_async(effective_text, g["content_text"]),
                    timeout=75.0,
                )
            except asyncio.TimeoutError:
                meta = dict(_empty_meta)
                warning = "timeout"
            except Exception as e:
                meta = dict(_empty_meta)
                warning = f"erro: {e}"
            all_empty = not any(meta.get(k) for k in ("isbn", "ano", "colecao", "autor"))
            if effective_text.strip() and all_empty and not warning:
                warning = "LLM não encontrou metadados — verifique se o PDF contém ficha CIP em texto (não imagem)"
            titulo = f"{tema} - {tipo}" if tema and tipo else tema or tipo
            record = {
                "Item":                        0,
                "Opção":                       1,
                "Coleção":                     meta.get("colecao", ""),
                "Faixa etária / nível":        serie,
                "Título":                      titulo,
                "Autor(es)":                   meta.get("autor", ""),
                "Ilustrador(es) 1":            meta.get("ilustradores_1", ""),
                "Ilustrador(es) 2":            meta.get("ilustradores_2", ""),
                "ISBN":                        meta.get("isbn", ""),
                "Ano de publicação":           meta.get("ano", ""),
                "Número de páginas":           g["page_count"],
                "Sinopse":                     meta.get("sinopse", ""),
                "Preço unitário":              "",
                "Material de apoio pedagógico": "",
            }
            if warning:
                record["_warning"] = warning
            # Diagnóstico ISBN
            isbn_pos = effective_text.lower().find("isbn")
            if isbn_pos >= 0:
                debug_isbn = effective_text[max(0, isbn_pos-10):isbn_pos+80]
            else:
                debug_isbn = f"[NÃO ENCONTRADO] primeiros 200 chars: {effective_text[:200]}"
            record["_debug_isbn"] = debug_isbn
            return record

        tasks = [
            _process_group(serie, tipo, tema, g)
            for (serie, tipo, tema, _variante), g in groups.items()
        ]
        records = list(await asyncio.gather(*tasks))

        # Coletar avisos e avisar sobre PDFs sem texto
        warnings = []
        for r in records:
            w = r.pop("_warning", None)
            if w:
                warnings.append({"titulo": r["Título"], "motivo": w})

        for (serie, tipo, tema, _variante), g in groups.items():
            if not g["iniciais_text"].strip() and not g["capa_text"].strip() and not g["content_text"].strip():
                titulo = f"{tema} - {tipo}" if tema and tipo else tema or tipo
                warnings.append({"titulo": titulo, "motivo": "texto vazio (PDF pode ser escaneado)"})

        return {"records": _renumber(records), "warnings": warnings}

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


@app.post("/api/export/excel")
async def export_excel(body: ExportBody):
    """Gera .xlsx com 3 abas formatadas e retorna para download."""
    try:
        data = write_excel_to_bytes(body.records)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="catalogo_grafica_educar.xlsx"'},
    )


@app.post("/api/export/csv")
async def export_csv(body: ExportBody):
    """Exporta catálogo como CSV UTF-8 BOM."""
    try:
        df = pd.DataFrame(body.records)
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = ""
        df = df[COLUMNS]
        csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv; charset=utf-8-sig",
        headers={"Content-Disposition": 'attachment; filename="catalogo_grafica_educar.csv"'},
    )


# ── Static files (deve ser montado por último) ────────────────────────────────

PUBLIC_DIR = ROOT / "public"
if PUBLIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")
