import sys
import os
from config import SOURCE_FOLDER
from scanner import scan_and_group
from excel_writer import write_excel


def main():
    print("=== Catalogador de PDFs - GRÁFICA EDUCAR ===\n")

    if not os.path.isdir(SOURCE_FOLDER):
        print(f"[ERRO] Pasta de origem não encontrada: {SOURCE_FOLDER}")
        sys.exit(1)

    print(f"Varrendo: {SOURCE_FOLDER}\n")
    records = scan_and_group(SOURCE_FOLDER)

    print(f"\nTotal de títulos agrupados: {len(records)}")

    if not records:
        print("[AVISO] Nenhum registro encontrado. Encerrando.")
        sys.exit(0)

    print("\nGerando planilha Excel...")
    output_path = write_excel(records)

    print(f"\n✓ Arquivo gerado: {output_path}")
    print("\nPara usar no Google Sheets:")
    print("  1. Acesse drive.google.com")
    print("  2. Arraste o arquivo .xlsx para o Drive")
    print("  3. Clique duplo → abre direto no Sheets com todas as abas e fórmulas")


if __name__ == "__main__":
    main()
