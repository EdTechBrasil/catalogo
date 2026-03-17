"""
Varre /GRÁFICA EDUCAR recursivamente e agrupa PDFs por título.
Para cada grupo (Série, Disciplina, Tema, Tipo) extrai:
  - Metadados do INICIAIS_LA (ficha CIP)
  - Número de páginas do MIOLO_LA (ou qualquer LA disponível)
"""
import os
import re
from collections import defaultdict
from pdf_reader import get_page_count
from pdf_metadata import extract_metadata


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


def _title_case_pt(text: str) -> str:
    """Title case preservando artigos/preposições em minúsculas."""
    lower_words = {"a", "e", "o", "da", "de", "do", "das", "dos", "na", "no", "nas", "nos",
                   "em", "com", "por", "para", "ao", "aos", "à", "às"}
    words = text.lower().split()
    return " ".join(
        w.capitalize() if i == 0 or w not in lower_words else w
        for i, w in enumerate(words)
    )


def _parse_disciplina_tema(folder_name: str) -> tuple[str, str]:
    parts = folder_name.split(" - ", maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return folder_name.strip(), ""


def scan_individual_pdfs(items: list[dict], on_progress=None) -> list[dict]:
    """
    Processa PDFs individualmente, um registro por arquivo.

    items: [{"path": str, "serie": str, "disciplina": str, "tema": str, "tipo": str}]
    """
    records = []
    total = len(items)

    for i, item in enumerate(items, 1):
        serie = item["serie"]
        tema = item["tema"]
        tipo = item["tipo"]
        pdf_path = item["path"]

        print(f"  [{i:03d}] {serie} | {item['disciplina']} | {_title_case_pt(tema)} | {tipo} | {os.path.basename(pdf_path)}")

        # Busca MIOLO irmão para contexto de conteúdo
        miolo_path = None
        dt_path = os.path.dirname(os.path.dirname(pdf_path))
        for sib_tipo in os.listdir(dt_path):
            sib_dir = os.path.join(dt_path, sib_tipo)
            if not os.path.isdir(sib_dir):
                continue
            for f in os.listdir(sib_dir):
                if "MIOLO" in f.upper() and f.lower().endswith(".pdf"):
                    miolo_path = os.path.join(sib_dir, f)
                    break
            if miolo_path:
                break

        meta = extract_metadata(pdf_path, miolo_pdf_path=miolo_path)
        paginas = get_page_count(pdf_path)

        # Se não extraiu metadados, procura em PDFs irmãos (mesmo tema, outro tipo)
        if not any(meta.values()):
            dt_path = os.path.dirname(os.path.dirname(pdf_path))  # .../serie/dt/
            for sibling_tipo in sorted(os.listdir(dt_path)):
                sibling_path = os.path.join(dt_path, sibling_tipo)
                if not os.path.isdir(sibling_path) or sibling_tipo == item["tipo"]:
                    continue
                siblings = sorted(
                    os.path.join(sibling_path, f)
                    for f in os.listdir(sibling_path)
                    if f.lower().endswith(".pdf") and "_LA_" in f.upper()
                )
                if not siblings:
                    siblings = sorted(
                        os.path.join(sibling_path, f)
                        for f in os.listdir(sibling_path)
                        if f.lower().endswith(".pdf")
                    )
                if siblings:
                    candidate_meta = extract_metadata(siblings[0])
                    if any(candidate_meta.values()):
                        meta = candidate_meta
                        break

        tipo_key = tipo.lower()
        sufixo = TIPO_SUFFIX.get(tipo_key, tipo)
        titulo = f"{_title_case_pt(tema)} - {sufixo}"

        record = {
            "Item":                        i,
            "Opção":                       1,
            "Coleção":                     meta.get("colecao", ""),
            "Faixa etária / nível":        SERIE_MAP.get(serie, serie),
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
        }
        records.append(record)

        if on_progress:
            on_progress(i, total, serie, titulo)

    return records


def scan_and_group(source_folder: str, on_progress=None, filter_keys=None) -> list[dict]:
    """
    Retorna uma lista de registros, um por (Série, Disciplina, Tema, Tipo),
    com todos os metadados extraídos.
    """
    source_folder = os.path.abspath(source_folder)

    # Primeiro passo: coletar todos os PDFs agrupados por chave
    groups: dict[tuple, dict] = defaultdict(lambda: {
        "iniciais_la": None,
        "iniciais_lp": None,
        "miolo_la": None,
        "miolo_lp": None,
        "la_files": [],
        "lp_files": [],
        "all_files": [],
    })

    for serie in sorted(os.listdir(source_folder)):
        serie_path = os.path.join(source_folder, serie)
        if not os.path.isdir(serie_path) or serie.startswith("."):
            continue

        for disc_tema in sorted(os.listdir(serie_path)):
            disc_tema_path = os.path.join(serie_path, disc_tema)
            if not os.path.isdir(disc_tema_path) or disc_tema.startswith("."):
                continue

            disciplina, tema = _parse_disciplina_tema(disc_tema)

            for tipo_folder in sorted(os.listdir(disc_tema_path)):
                tipo_path = os.path.join(disc_tema_path, tipo_folder)
                if not os.path.isdir(tipo_path) or tipo_folder.startswith("."):
                    continue

                tipo = tipo_folder.strip()
                key = (serie.strip(), disciplina, tema, tipo)
                g = groups[key]

                for filename in sorted(os.listdir(tipo_path)):
                    if not filename.lower().endswith(".pdf") or filename.startswith("."):
                        continue

                    full_path = os.path.join(tipo_path, filename)
                    g["all_files"].append(full_path)
                    fname_up = filename.upper()

                    is_la = "_LA_" in fname_up or fname_up.startswith("INICIAIS_LA") or fname_up.startswith("MIOLO_LA")
                    is_lp = "_LP_" in fname_up or fname_up.startswith("INICIAIS_LP") or fname_up.startswith("MIOLO_LP")

                    if "INICIAIS" in fname_up and is_la:
                        g["iniciais_la"] = full_path
                    elif "INICIAIS" in fname_up and is_lp:
                        g["iniciais_lp"] = full_path
                    elif "MIOLO" in fname_up and is_la:
                        g["miolo_la"] = full_path
                    elif "MIOLO" in fname_up and is_lp:
                        g["miolo_lp"] = full_path

                    if is_la:
                        g["la_files"].append(full_path)
                    elif is_lp:
                        g["lp_files"].append(full_path)

    # Segundo passo: montar registros finais
    records = []
    item_num = 1
    sorted_groups = sorted(groups.items())
    total = len(filter_keys) if filter_keys is not None else len(sorted_groups)

    for key, g in sorted_groups:
        if filter_keys is not None and key not in filter_keys:
            continue
        serie, disciplina, tema, tipo = key

        # PDF de metadados: preferir INICIAIS_LA → INICIAIS_LP → miolo_la → qualquer
        meta_pdf = g["iniciais_la"] or g["iniciais_lp"] or g["miolo_la"]
        if not meta_pdf and g["all_files"]:
            meta_pdf = g["all_files"][0]

        # PDF de contagem de páginas: MIOLO_LA → LA → qualquer
        pages_pdf = g["miolo_la"] or g["miolo_lp"]
        if not pages_pdf and g["la_files"]:
            pages_pdf = g["la_files"][0]
        if not pages_pdf and g["all_files"]:
            pages_pdf = g["all_files"][0]

        print(f"  [{item_num:03d}] {serie} | {disciplina} | {_title_case_pt(tema)} | {tipo}")

        # Extrair metadados
        meta = extract_metadata(meta_pdf, miolo_pdf_path=pages_pdf) if meta_pdf else {}

        # Número de páginas do conteúdo principal
        paginas = get_page_count(pages_pdf) if pages_pdf else 0

        # Título formatado
        tipo_key = tipo.lower()
        sufixo = TIPO_SUFFIX.get(tipo_key, tipo)
        titulo = f"{_title_case_pt(tema)} - {sufixo}"

        record = {
            "Item":                        item_num,
            "Opção":                       1,
            "Coleção":                     meta.get("colecao", ""),
            "Faixa etária / nível":        SERIE_MAP.get(serie, serie),
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
        }
        records.append(record)

        if on_progress:
            on_progress(item_num, total, serie, titulo)

        item_num += 1

    return records
