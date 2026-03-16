import os

# Pasta raiz com os PDFs
SOURCE_FOLDER = "/Users/usuario/Downloads/GRÁFICA EDUCAR"

# ID da planilha Google Sheets (extraído da URL)
SPREADSHEET_ID = "1XxAOkYy_p_dzShnsk8qBtqd3AF4OYsHY"

# Nome da aba onde os dados serão escritos
SHEET_NAME = "Catálogo"

# Caminho para o arquivo de credenciais da Service Account
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
