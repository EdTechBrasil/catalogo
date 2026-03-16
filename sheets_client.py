import gspread
from google.oauth2.service_account import Credentials
from config import SPREADSHEET_ID, SHEET_NAME, CREDENTIALS_FILE

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

COLUMNS = ["Série", "Disciplina", "Tema", "Tipo", "Versão", "Nome do Arquivo", "Páginas", "Caminho"]


def get_or_create_sheet(spreadsheet) -> gspread.Worksheet:
    """Retorna a aba 'Catálogo', criando-a se não existir."""
    try:
        return spreadsheet.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        print(f"  Aba '{SHEET_NAME}' não encontrada. Criando...")
        return spreadsheet.add_worksheet(title=SHEET_NAME, rows=300, cols=len(COLUMNS))


