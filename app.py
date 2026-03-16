"""
Catálogo de PDFs — interface Streamlit
Run: .venv/bin/streamlit run app.py
"""
import io
import os
import re
import tempfile
import unicodedata
import zipfile

import pandas as pd
import streamlit as st

from config import SOURCE_FOLDER
from excel_writer import OUTPUT_FILE, COLUMNS, write_excel
from scanner import scan_and_group, scan_individual_pdfs, SERIE_MAP, TIPO_SUFFIX
from pdf_reader import get_page_count
from pdf_metadata import extract_metadata

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
}

TIPO_KEYWORDS = {
    "ATV": "livro de atividades",
    "DES": "livro de desafios",
    "ILU": "livro ilustrado",
    "TAP": "Tapetes",
}

EMPTY_RECORD = {col: "" for col in COLUMNS}

# Colunas obrigatórias (sempre visíveis) e opcionais (podem ser escondidas)
CORE_COLS = ["Item", "Faixa etária / nível", "Título", "Número de páginas"]
OPTIONAL_COLS = [
    "Opção", "Coleção", "Ilustrador(es) 1", "Ilustrador(es) 2",
    "ISBN", "Ano de publicação", "Sinopse", "Preço unitário",
    "Material de apoio pedagógico",
]


def _normalize(text: str) -> str:
    """Remove acentos e lowercase para comparação flexível de nomes de colunas."""
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower().strip()


_COLUMNS_NORM = {_normalize(c): c for c in COLUMNS}


def _map_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Mapeia colunas do arquivo importado para os nomes esperados no app."""
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


def _merge_records(existing: list[dict], scanned: list[dict]) -> list[dict]:
    """
    Para cada registro do scanner, preenche apenas os campos vazios
    no registro existente correspondente. Registros novos são acrescentados ao final.
    Correspondência por chave composta (faixa_etária, título_normalizado).
    """
    def key(r):
        return (
            _normalize(r.get("Faixa etária / nível", "")),
            _normalize(r.get("Título", "")),
        )

    by_key = {key(r): r.copy() for r in existing}

    for new in scanned:
        k = key(new)
        if k in by_key:
            for col in COLUMNS:
                val = by_key[k].get(col)
                if (val is None or str(val).strip() == "") and new.get(col):
                    by_key[k][col] = new[col]
        else:
            by_key[k] = new

    return list(by_key.values())


def _append_scanned(existing: list[dict], scanned: list[dict]) -> list[dict]:
    """
    Versão de merge para PDFs individuais: cada registro escaneado
    preenche seu correspondente existente OU é adicionado como novo.
    Não deduplica entre os próprios registros escaneados — cada PDF
    selecionado gera (ou atualiza) sua própria linha.
    """
    def key(r):
        return (
            _normalize(r.get("Faixa etária / nível", "")),
            _normalize(r.get("Título", "")),
        )

    result = [r.copy() for r in existing]
    existing_keys = {key(r): i for i, r in enumerate(result)}

    for new in scanned:
        k = key(new)
        if k in existing_keys:
            idx = existing_keys[k]
            for col in COLUMNS:
                val = result[idx].get(col)
                if (val is None or str(val).strip() == "") and new.get(col):
                    result[idx][col] = new[col]
        else:
            result.append(new.copy())
            # não adiciona ao existing_keys → próximo PDF com mesmo título
            # também entra como nova linha independente

    return result


def _infer_from_filename(name: str) -> dict:
    """Infere chaves série/tipo/tema a partir do nome do arquivo."""
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

    # Remove tokens de série/tipo para isolar o tema
    skip = {"INICIAIS", "MIOLO", "LA", "LP", "ATV", "DES", "ILU", "TAP",
            "PRÉ", "PRE", "1", "2", "3", "ANO"}
    for k in SERIE_KEYWORDS:
        skip.update(k.split("_"))
    tema_parts = [p for p in parts if p not in skip and len(p) > 1]
    tema = " ".join(tema_parts).title()

    return {"serie": serie, "tipo": tipo, "tema": tema}


def _records_to_df(records: list[dict]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(records)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[COLUMNS]


def _build_folder_tree(folder: str) -> dict:
    tree = {}
    if not os.path.isdir(folder):
        return tree
    for serie in sorted(os.listdir(folder)):
        if serie.startswith("."):
            continue
        serie_path = os.path.join(folder, serie)
        if not os.path.isdir(serie_path):
            continue
        tree[serie] = {}
        for dt in sorted(os.listdir(serie_path)):
            if dt.startswith("."):
                continue
            dt_path = os.path.join(serie_path, dt)
            if not os.path.isdir(dt_path):
                continue
            tree[serie][dt] = {}
            for tipo in sorted(os.listdir(dt_path)):
                if tipo.startswith("."):
                    continue
                tipo_path = os.path.join(dt_path, tipo)
                if not os.path.isdir(tipo_path):
                    continue
                pdfs = sorted(
                    f for f in os.listdir(tipo_path)
                    if f.lower().endswith(".pdf") and not f.startswith(".")
                )
                if pdfs:
                    tree[serie][dt][tipo] = pdfs
    return tree


def _pdf_chk_key(serie: str, dt: str, tipo: str, pdf: str) -> str:
    """Chave de session_state para o checkbox de um PDF individual."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", f"pdf_{serie}_{dt}_{tipo}_{pdf}")


def _collect_selected_pdfs(tree: dict, folder: str) -> list[dict]:
    """Retorna um dict por PDF marcado, com caminho completo e contexto."""
    selected = []
    for serie, dts in tree.items():
        for dt, tipos in dts.items():
            parts = dt.split(" - ", maxsplit=1)
            disc = parts[0].strip()
            tema = parts[1].strip() if len(parts) == 2 else ""
            for tipo, pdfs in tipos.items():
                for pdf in pdfs:
                    if st.session_state.get(_pdf_chk_key(serie, dt, tipo, pdf), False):
                        selected.append({
                            "path": os.path.join(folder, serie, dt, tipo, pdf),
                            "serie": serie.strip(),
                            "disciplina": disc,
                            "tema": tema,
                            "tipo": tipo.strip(),
                        })
    return selected


def _render_folder_tree():
    folder = st.session_state.source_folder
    if not os.path.isdir(folder):
        st.info("Selecione uma pasta válida na sidebar.")
        return

    tree = _build_folder_tree(folder)
    if not tree:
        st.warning("Nenhuma subpasta encontrada.")
        return

    total_pdfs = sum(
        len(pdfs)
        for series in tree.values()
        for dts in series.values()
        for pdfs in dts.values()
    )
    st.caption(f"📁 `{folder}` — {len(tree)} série(s), {total_pdfs} PDFs")

    # ── Barra de ações (topo) ─────────────────────────────────────────────────
    sel_pdfs = _collect_selected_pdfs(tree, folder)
    n_sel = len(sel_pdfs)
    too_many = n_sel > 5

    col_btn, col_info = st.columns([3, 4])
    with col_btn:
        if n_sel == 0:
            btn_label = "🔍 Escanear pasta"
            btn_help  = "Processa todos os PDFs da pasta"
            btn_merge = False
        elif too_many:
            btn_label = f"🔄 Completar selecionados ({n_sel}/5)"
            btn_help  = None
            btn_merge = True
        else:
            btn_label = f"🔄 Completar selecionados ({n_sel})"
            btn_help  = f"Preenche campos vazios nos {n_sel} livro(s) marcado(s)"
            btn_merge = True

        if st.button(
            btn_label,
            key="scan_or_fill_from_tree",
            use_container_width=True,
            disabled=too_many,
            type="primary" if n_sel else "secondary",
            help=btn_help,
        ):
            _scan_folder(merge=btn_merge, selected_pdfs=sel_pdfs if btn_merge else None)
    with col_info:
        if too_many:
            st.warning(f"Máx. 5 livros por vez — desmarque {n_sel - 5}.")
        elif n_sel:
            st.info(f"{n_sel} livro(s) marcado(s) — clique para completar")

    st.divider()

    # ── Chaves dos registros já processados ───────────────────────────────────
    processed_keys = {
        (_normalize(r.get("Faixa etária / nível", "")),
         _normalize(r.get("Título", "")))
        for r in st.session_state.records
    }

    # ── Árvore com checkboxes nos PDFs ────────────────────────────────────────
    for serie, dts in tree.items():
        serie_pdfs = sum(len(p) for dt in dts.values() for p in dt.values())
        with st.expander(f"📂 {serie}  —  {serie_pdfs} PDFs", expanded=True):
            for dt, tipos in dts.items():
                dt_pdfs = sum(len(p) for p in tipos.values())
                with st.expander(f"📂 {dt}  —  {dt_pdfs} PDFs", expanded=False):
                    for tipo, pdfs in tipos.items():
                        names_up = [p.upper() for p in pdfs]
                        has_ini = any("INICIAIS" in n for n in names_up)
                        has_mio = any("MIOLO" in n for n in names_up)
                        status = "✅" if (has_ini and has_mio) else "⚠️"
                        st.markdown(f"**{status} {tipo}/**")
                        for pdf in pdfs:
                            dt_parts = dt.split(" - ", maxsplit=1)
                            tema = dt_parts[1].strip() if len(dt_parts) == 2 else dt.strip()
                            sufixo = TIPO_SUFFIX.get(tipo.strip().lower(), tipo.strip())
                            titulo = f"{tema} - {sufixo}"
                            faixa = SERIE_MAP.get(serie.strip(), serie.strip())
                            k = (_normalize(faixa), _normalize(titulo))
                            is_done = k in processed_keys
                            icon = "✅" if is_done else "📄"
                            st.checkbox(
                                f"{icon} `{pdf}`",
                                key=_pdf_chk_key(serie, dt, tipo, pdf),
                            )


def _pick_folder() -> str | None:
    """Abre o seletor de pasta nativo do macOS via osascript."""
    import subprocess
    script = 'POSIX path of (choose folder with prompt "Selecionar pasta com PDFs")'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=60,
        )
        path = result.stdout.strip()
        return path if path else None
    except Exception:
        return None


def _init_state():
    if "records" not in st.session_state:
        st.session_state.records = []
    if "df" not in st.session_state:
        st.session_state.df = _records_to_df([])
    if "source_folder" not in st.session_state:
        st.session_state.source_folder = SOURCE_FOLDER
    if "tree_selected_keys" not in st.session_state:
        st.session_state.tree_selected_keys = set()
    if "tree_selected_pdfs" not in st.session_state:
        st.session_state.tree_selected_pdfs = []
    if "catalog_search" not in st.session_state:
        st.session_state.catalog_search = ""
    if "catalog_visible_cols" not in st.session_state:
        st.session_state.catalog_visible_cols = OPTIONAL_COLS[:]


# ── Ações ─────────────────────────────────────────────────────────────────────

def _scan_folder(merge: bool = False, selected_pdfs=None):
    folder = st.session_state.source_folder
    if not os.path.isdir(folder):
        st.error(f"Pasta não encontrada: `{folder}`")
        return

    status_text = st.empty()
    progress_bar = st.progress(0)
    live_table   = st.empty()
    live_records: list[dict] = []

    def on_progress(current, total, serie, titulo):
        pct = int(current / total * 100) if total else 100
        progress_bar.progress(pct)
        status_text.markdown(f"⏳ **{current}/{total}** — `{serie}` › {titulo}")
        live_records.append({"Série": serie, "Título": titulo, "Status": "✅ pronto"})
        live_table.dataframe(pd.DataFrame(live_records), use_container_width=True, hide_index=True)

    if merge and not st.session_state.records:
        st.info("Nenhum dado existente — carregando do zero.")

    if selected_pdfs:
        scanned = scan_individual_pdfs(selected_pdfs, on_progress=on_progress)
    else:
        scanned = scan_and_group(folder, on_progress=on_progress)

    # limpa UI de progresso
    status_text.empty()
    progress_bar.empty()
    live_table.empty()

    if not scanned:
        st.warning("Nenhum PDF encontrado. Verifique se a pasta tem a estrutura correta: Série → Disciplina-Tema → Tipo → PDFs.")
        return

    from collections import Counter
    series_count = Counter(r.get("Faixa etária / nível", "?") for r in scanned)
    summary = ", ".join(f"{v}x {k}" for k, v in sorted(series_count.items()))

    if merge and st.session_state.records:
        if selected_pdfs:
            records = _append_scanned(st.session_state.records, scanned)
        else:
            records = _merge_records(st.session_state.records, scanned)
        added = len(records) - len(st.session_state.records)
        st.success(f"✅ {len(scanned)} título(s) processado(s): {summary} — {added} adicionado(s) / atualizado(s).")
    else:
        records = scanned
        st.success(f"✅ {len(scanned)} título(s) processado(s): {summary}")

    # Renumera Item sequencialmente para manter consistência
    for i, r in enumerate(records, 1):
        r["Item"] = i

    st.session_state.records = records
    st.session_state.df = _records_to_df(records)


def _process_zip(uploaded_file):
    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(uploaded_file) as zf:
            zf.extractall(tmpdir)
        with st.spinner("Processando ZIP…"):
            records = scan_and_group(tmpdir)
    st.session_state.records = records
    st.session_state.df = _records_to_df(records)
    st.success(f"{len(records)} títulos extraídos do ZIP.")


def _process_pdfs(uploaded_files):
    """Infere metadados pelo nome do arquivo para PDFs avulsos."""
    groups: dict[tuple, dict] = {}

    for uf in uploaded_files:
        info = _infer_from_filename(uf.name)
        key = (info["serie"], info["tipo"], info["tema"])
        if key not in groups:
            groups[key] = {"iniciais": None, "miolo": None, "files": [], "uf": []}
        groups[key]["uf"].append(uf)
        name_up = uf.name.upper()
        if "INICIAIS" in name_up:
            groups[key]["iniciais"] = uf
        elif "MIOLO" in name_up:
            groups[key]["miolo"] = uf
        else:
            groups[key]["files"].append(uf)

    records = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for (serie, tipo, tema), g in groups.items():
            # Gravar arquivos no tmpdir para usar as funções existentes
            meta = {}
            paginas = 0
            meta_uf = g["iniciais"] or (g["uf"][0] if g["uf"] else None)
            pages_uf = g["miolo"] or meta_uf

            if meta_uf:
                meta_path = os.path.join(tmpdir, meta_uf.name)
                with open(meta_path, "wb") as f:
                    f.write(meta_uf.getbuffer())
                meta = extract_metadata(meta_path)

            if pages_uf:
                pages_path = os.path.join(tmpdir, pages_uf.name)
                if not os.path.exists(pages_path):
                    with open(pages_path, "wb") as f:
                        f.write(pages_uf.getbuffer())
                paginas = get_page_count(pages_path)

            records.append({
                "Item":                        len(records) + 1,
                "Opção":                       1,
                "Coleção":                     meta.get("colecao", ""),
                "Faixa etária / nível":        serie,
                "Título":                      f"{tema} - {tipo}" if tema and tipo else tema or tipo,
                "Ilustrador(es) 1":            meta.get("ilustradores_1", ""),
                "Ilustrador(es) 2":            meta.get("ilustradores_2", ""),
                "ISBN":                        meta.get("isbn", ""),
                "Ano de publicação":           meta.get("ano", ""),
                "Número de páginas":           paginas,
                "Sinopse":                     meta.get("sinopse", ""),
                "Preço unitário":              "",
                "Material de apoio pedagógico": "",
            })

    st.session_state.records = records
    st.session_state.df = _records_to_df(records)
    st.warning(
        f"{len(records)} títulos inferidos pelo nome do arquivo. "
        "Para melhores resultados, faça upload do ZIP com a estrutura de pastas."
    )


def _load_from_file(uploaded_file):
    """Carrega dados de um .xlsx ou .csv, iterando todas as abas para encontrar dados válidos."""
    try:
        name = uploaded_file.name.lower()
        if name.endswith(".csv"):
            df = pd.read_csv(uploaded_file, encoding="utf-8-sig", dtype=str).fillna("")
            df = _map_columns(df)
        else:
            xls = pd.ExcelFile(uploaded_file)
            frames = []
            for sheet in xls.sheet_names:
                raw = pd.read_excel(xls, sheet_name=sheet, header=None, dtype=str).fillna("")
                if raw.empty or raw.shape[1] < 5:
                    continue
                # Verifica se a linha 0 contém cabeçalhos compatíveis com COLUMNS
                header_row = raw.iloc[0]
                matches = sum(
                    1 for v in header_row
                    if _normalize(str(v)) in _COLUMNS_NORM or "ilustrador" in _normalize(str(v))
                )
                if matches < 4:
                    continue  # aba não tem as colunas certas
                df_sheet = pd.read_excel(xls, sheet_name=sheet, header=0, dtype=str).fillna("")
                frames.append(df_sheet)

            if not frames:
                st.error("Nenhuma aba com dados de catálogo encontrada.")
                return
            df = pd.concat(frames, ignore_index=True)
            df = _map_columns(df)

        for col in COLUMNS:
            if col not in df.columns:
                df[col] = ""
        df = df[COLUMNS]

        # Remove linhas completamente vazias
        df = df[df.apply(lambda r: r.str.strip().ne("").any(), axis=1)].reset_index(drop=True)

        records = df.to_dict("records")
        st.session_state.records = records
        st.session_state.df = _records_to_df(records)
        st.success(f"{len(records)} registros carregados de `{uploaded_file.name}`.")
    except Exception as e:
        st.error(f"Erro ao carregar arquivo: {e}")
        import traceback
        st.code(traceback.format_exc())


def _export_excel() -> bytes:
    write_excel(st.session_state.records)
    with open(OUTPUT_FILE, "rb") as f:
        return f.read()


def _export_csv() -> bytes:
    return st.session_state.df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


# ── Layout ────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Catálogo Gráfica Educar",
        page_icon="📚",
        layout="wide",
    )
    _init_state()

    # ── Coleta seleções dos checkboxes da árvore ANTES da sidebar ─────────────
    # (sidebar renderiza antes da aba Pasta; session_state já tem os valores atuais)
    _folder = st.session_state.source_folder
    if os.path.isdir(_folder):
        _tree = _build_folder_tree(_folder)
        st.session_state._cached_tree = _tree
        _sel = _collect_selected_pdfs(_tree, _folder)
        st.session_state.tree_selected_pdfs = _sel
        st.session_state.tree_selected_keys = {(s["serie"], s["disciplina"], s["tema"], s["tipo"]) for s in _sel}
    else:
        st.session_state._cached_tree = {}

    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.title("📚 Catálogo Gráfica Educar")
        st.divider()

        # ── Seletor de pasta ──────────────────────────────────────────────
        col_path, col_browse = st.columns([4, 1])
        with col_path:
            new_folder = st.text_input(
                "Pasta dos PDFs",
                value=st.session_state.source_folder,
                label_visibility="collapsed",
                placeholder="Caminho da pasta...",
            )
            if new_folder != st.session_state.source_folder:
                st.session_state.source_folder = new_folder
        with col_browse:
            if st.button("...", help="Procurar pasta", use_container_width=True):
                picked = _pick_folder()
                if picked:
                    st.session_state.source_folder = picked
                    st.rerun()

        folder_ok = os.path.isdir(st.session_state.source_folder)
        if not folder_ok:
            st.warning("Pasta não encontrada.")
        else:
            _t = st.session_state.get("_cached_tree", {})
            _total = sum(len(p) for s in _t.values() for d in s.values() for p in d.values())
            st.success(f"✅ {len(_t)} série(s) · {_total} PDFs encontrados")


        st.divider()

        _n_sel = len(st.session_state.get("tree_selected_pdfs", []))
        _too_many = _n_sel > 5
        if _n_sel == 0:
            _sb_label = "🔍 Escanear pasta"
            _sb_help  = "Processa todos os PDFs da pasta"
            _sb_merge = False
        elif _too_many:
            _sb_label = f"🔄 Completar selecionados ({_n_sel}/5)"
            _sb_help  = None
            _sb_merge = True
        else:
            _sb_label = f"🔄 Completar selecionados ({_n_sel})"
            _sb_help  = f"Preenche campos vazios nos {_n_sel} livro(s) marcado(s)"
            _sb_merge = True

        if st.button(
            _sb_label,
            use_container_width=True,
            disabled=not folder_ok or _too_many,
            type="primary" if _n_sel and not _too_many else "secondary",
            help=_sb_help,
        ):
            _scan_folder(merge=_sb_merge, selected_pdfs=st.session_state.tree_selected_pdfs if _sb_merge else None)

        if _too_many:
            st.warning(f"Máx. 5 livros — desmarque {_n_sel - 5}.")
        elif _n_sel:
            st.caption(f"☑️ {_n_sel} livro(s) marcado(s) na aba Pasta")
        else:
            st.caption("Marque livros na aba **Pasta** para completar só alguns")

        sheets_file = st.file_uploader(
            "📊 Importar planilha (Sheets/Excel/CSV)",
            type=["xlsx", "csv"],
            help="Baixe do Google Sheets como .xlsx ou .csv e faça upload aqui.",
            key="sheets_uploader",
        )
        if sheets_file is not None:
            if st.session_state.get("loaded_file_id") != sheets_file.file_id:
                _load_from_file(sheets_file)
                st.session_state.loaded_file_id = sheets_file.file_id

        st.divider()
        st.subheader("Upload")
        uploaded = st.file_uploader(
            "PDFs ou ZIP",
            type=["pdf", "zip"],
            accept_multiple_files=True,
            help="ZIP com estrutura de pastas intacta = resultado mais preciso.",
        )

        if st.button("⚙️ Processar uploads", use_container_width=True, disabled=not uploaded):
            zips = [u for u in uploaded if u.name.lower().endswith(".zip")]
            pdfs = [u for u in uploaded if u.name.lower().endswith(".pdf")]

            if zips:
                _process_zip(zips[0])
                if len(zips) > 1:
                    st.info("Apenas o primeiro ZIP foi processado.")
            elif pdfs:
                _process_pdfs(pdfs)

        st.divider()
        st.subheader("Exportar")

        has_data = len(st.session_state.records) > 0  # reavalia após ações acima

        if has_data:
            st.download_button(
                "📥 Exportar Excel (.xlsx)",
                data=_export_excel(),
                file_name="catalogo_grafica_educar.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
            st.download_button(
                "📄 Exportar CSV",
                data=_export_csv(),
                file_name="catalogo_grafica_educar.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.button("📥 Exportar Excel (.xlsx)", disabled=True, use_container_width=True)
            st.button("📄 Exportar CSV", disabled=True, use_container_width=True)

        st.divider()
        if st.button("🗑️ Limpar catálogo", use_container_width=True, disabled=not has_data):
            st.session_state.records = []
            st.session_state.df = _records_to_df([])
            st.rerun()

    # ── Abas principais ───────────────────────────────────────────────────────
    tab_catalogo, tab_resumo, tab_pasta, tab_sobre = st.tabs(
        ["📋 Catálogo", "📊 Resumo", "📁 Pasta", "ℹ️ Sobre"]
    )

    # ── ABA: Catálogo ─────────────────────────────────────────────────────────
    with tab_catalogo:
        # ── Barra de controles ────────────────────────────────────────────────
        ctrl_left, ctrl_right = st.columns([4, 2])
        with ctrl_left:
            search = st.text_input(
                "busca",
                placeholder="🔍 Filtrar por título, série, ISBN…",
                value=st.session_state.catalog_search,
                label_visibility="collapsed",
                key="catalog_search_input",
            )
            st.session_state.catalog_search = search

        with ctrl_right:
            with st.expander("⚙️ Colunas"):
                visible_optional = st.multiselect(
                    "Colunas opcionais",
                    options=OPTIONAL_COLS,
                    default=st.session_state.catalog_visible_cols,
                    label_visibility="collapsed",
                )
                st.session_state.catalog_visible_cols = visible_optional

        # ── Filtrar e preparar df ─────────────────────────────────────────────
        df_full = st.session_state.df.copy()
        searching = bool(search.strip())

        if searching:
            mask = df_full.apply(
                lambda col: col.astype(str).str.contains(search, case=False, na=False)
            ).any(axis=1)
            df_show = df_full[mask].copy()
        else:
            df_show = df_full.copy()

        st.caption(f"{len(df_show)} de {len(df_full)} título(s)")

        # Adicionar coluna de deleção (view-only)
        df_show.insert(0, "_del", False)

        # Ordem de colunas visíveis
        visible_cols = ["_del"] + CORE_COLS + [c for c in OPTIONAL_COLS if c in visible_optional]

        # ── data_editor ───────────────────────────────────────────────────────
        edited = st.data_editor(
            df_show,
            num_rows="fixed",
            use_container_width=True,
            column_order=visible_cols,
            column_config={
                "_del":                        st.column_config.CheckboxColumn("🗑", width="small"),
                "Item":                        st.column_config.NumberColumn(width="small"),
                "Opção":                       st.column_config.NumberColumn(width="small"),
                "Coleção":                     st.column_config.TextColumn(width="medium"),
                "Faixa etária / nível":        st.column_config.TextColumn(width="medium"),
                "Título":                      st.column_config.TextColumn(width="large"),
                "Ilustrador(es) 1":            st.column_config.TextColumn(width="medium"),
                "Ilustrador(es) 2":            st.column_config.TextColumn(width="medium"),
                "ISBN":                        st.column_config.TextColumn(width="medium"),
                "Ano de publicação":           st.column_config.TextColumn(width="small"),
                "Número de páginas":           st.column_config.NumberColumn(width="small"),
                "Sinopse":                     st.column_config.TextColumn(width="large"),
                "Preço unitário":              st.column_config.NumberColumn(format="R$ %.2f", width="small"),
                "Material de apoio pedagógico": st.column_config.TextColumn(width="medium"),
            },
            key="data_editor",
        )

        # ── Salvar edições (merge de volta ao df completo) ────────────────────
        edited_data = edited.drop(columns=["_del"])
        df_full.update(edited_data)
        st.session_state.df = df_full
        st.session_state.records = df_full.to_dict("records")

        # ── Botão de deletar ──────────────────────────────────────────────────
        to_delete = edited.index[edited["_del"]].tolist()
        n_del = len(to_delete)
        if n_del:
            if st.button(f"🗑 Remover {n_del} selecionado(s)", type="primary"):
                new_df = df_full.drop(index=to_delete).reset_index(drop=True)
                for i, idx in enumerate(new_df.index):
                    new_df.at[idx, "Item"] = i + 1
                st.session_state.df = new_df
                st.session_state.records = new_df.to_dict("records")
                st.rerun()

        # ── Formulário para adicionar título ──────────────────────────────────
        st.divider()
        with st.expander("➕ Adicionar título manualmente"):
            with st.form("add_record_form", clear_on_submit=True):
                c1, c2 = st.columns(2)
                with c1:
                    f_titulo  = st.text_input("Título *")
                    f_faixa   = st.selectbox("Faixa etária / nível",
                                    [""] + sorted(set(SERIE_MAP.values())))
                    f_colecao = st.text_input("Coleção")
                    f_opcao   = st.text_input("Opção")
                    f_isbn    = st.text_input("ISBN")
                with c2:
                    f_ano    = st.text_input("Ano de publicação")
                    f_paginas = st.number_input("Nº de páginas", min_value=0, step=1)
                    f_preco  = st.number_input("Preço unitário (R$)", min_value=0.0, format="%.2f")
                    f_ilu1   = st.text_input("Ilustrador(es) 1")
                    f_ilu2   = st.text_input("Ilustrador(es) 2")
                f_sinopse = st.text_area("Sinopse", height=80)
                f_mat     = st.text_input("Material de apoio pedagógico")

                if st.form_submit_button("Adicionar ao catálogo"):
                    if not f_titulo.strip():
                        st.error("Título é obrigatório.")
                    else:
                        new_r = {col: "" for col in COLUMNS}
                        new_r["Item"]                         = len(st.session_state.records) + 1
                        new_r["Título"]                       = f_titulo.strip()
                        new_r["Faixa etária / nível"]         = f_faixa
                        new_r["Coleção"]                      = f_colecao.strip()
                        new_r["Opção"]                        = f_opcao.strip()
                        new_r["ISBN"]                         = f_isbn.strip()
                        new_r["Ano de publicação"]            = f_ano.strip()
                        new_r["Número de páginas"]            = f_paginas or ""
                        new_r["Preço unitário"]               = f_preco or ""
                        new_r["Ilustrador(es) 1"]             = f_ilu1.strip()
                        new_r["Ilustrador(es) 2"]             = f_ilu2.strip()
                        new_r["Sinopse"]                      = f_sinopse.strip()
                        new_r["Material de apoio pedagógico"] = f_mat.strip()
                        st.session_state.records.append(new_r)
                        st.session_state.df = _records_to_df(st.session_state.records)
                        st.success(f"✅ '{f_titulo}' adicionado ao catálogo.")
                        st.rerun()

    # ── ABA: Resumo ───────────────────────────────────────────────────────────
    with tab_resumo:
        st.subheader("Resumo do catálogo")
        df = st.session_state.df

        if df.empty:
            st.info("Nenhum dado carregado. Use a sidebar para escanear ou fazer upload.")
        else:
            total_titulos = len(df)
            paginas_col = pd.to_numeric(df["Número de páginas"], errors="coerce").fillna(0)
            total_paginas = int(paginas_col.sum())
            media_paginas = round(paginas_col.mean(), 1) if total_titulos else 0

            c1, c2, c3 = st.columns(3)
            c1.metric("Total de títulos", total_titulos)
            c2.metric("Total de páginas", total_paginas)
            c3.metric("Média páginas/título", media_paginas)

            st.divider()
            col_a, col_b = st.columns(2)

            with col_a:
                st.markdown("**Por Faixa Etária / Nível**")
                faixa = df["Faixa etária / nível"].value_counts().reset_index()
                faixa.columns = ["Faixa etária / nível", "Qtd"]
                st.dataframe(faixa, hide_index=True, use_container_width=True)

                st.markdown("**Por Coleção**")
                col_df = df[df["Coleção"] != ""]["Coleção"].value_counts().reset_index()
                col_df.columns = ["Coleção", "Qtd"]
                if col_df.empty:
                    st.caption("Sem dados de coleção.")
                else:
                    st.dataframe(col_df, hide_index=True, use_container_width=True)

            with col_b:
                st.markdown("**Por Tipo**")
                tipo_df = (
                    df["Título"]
                    .str.split(" - ")
                    .str[-1]
                    .value_counts()
                    .reset_index()
                )
                tipo_df.columns = ["Tipo", "Qtd"]
                st.dataframe(tipo_df, hide_index=True, use_container_width=True)

                st.markdown("**Por Ano de Publicação**")
                ano_df = (
                    df[df["Ano de publicação"] != ""]["Ano de publicação"]
                    .value_counts()
                    .sort_index()
                    .reset_index()
                )
                ano_df.columns = ["Ano", "Qtd"]
                if ano_df.empty:
                    st.caption("Sem dados de ano.")
                else:
                    st.dataframe(ano_df, hide_index=True, use_container_width=True)

    # ── ABA: Pasta ────────────────────────────────────────────────────────────
    with tab_pasta:
        st.subheader("Estrutura de pastas")
        _render_folder_tree()

    # ── ABA: Sobre ────────────────────────────────────────────────────────────
    with tab_sobre:
        st.subheader("Como usar")
        st.markdown("""
### Opções de carregamento

**1. Escanear pasta (recomendado)**
Clique em **Escanear pasta** para processar a pasta configurada em `config.py`.
Todos os PDFs são analisados automaticamente.

**2. Upload de ZIP**
Faça upload do arquivo `.zip` com a estrutura de pastas intacta
(ex.: `GRÁFICA EDUCAR/1º ANO/MAT - TEMA/atividades/*.pdf`).
É a opção mais confiável para uploads.

**3. Carregar do Google Sheets**
Clique em **Carregar do Google Sheets** para importar os dados já salvos na planilha configurada em `config.py`.
Requer o arquivo `credentials.json` na raiz do projeto.

**4. Upload de PDFs individuais**
Faça upload de arquivos `.pdf` avulsos. O sistema infere série, tipo e tema
a partir do nome do arquivo. Os resultados podem precisar de ajustes manuais na tabela.

### Edição inline
Clique em qualquer célula da aba **Catálogo** para editar.
Use o botão **+** no final da tabela para adicionar linhas.

### Exportação
- **Exportar Excel** → gera `.xlsx` com 3 abas formatadas (Catálogo, Resumo, Por Faixa Etária).
- **Exportar CSV** → exporta a tabela atual com todas as edições.

### Configuração
Edite `config.py` para alterar a pasta padrão (`SOURCE_FOLDER`).
        """)


if __name__ == "__main__":
    main()
