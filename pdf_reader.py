import pypdf


def get_page_count(pdf_path: str) -> int:
    """Retorna o número de páginas de um PDF. Retorna 0 em caso de erro."""
    try:
        with open(pdf_path, "rb") as f:
            reader = pypdf.PdfReader(f)
            return len(reader.pages)
    except Exception as e:
        print(f"  [AVISO] Não foi possível ler {pdf_path}: {e}")
        return 0
