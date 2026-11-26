"""Los tests usan siempre los datos ficticios y el modo de prueba de tokens, aunque el `.env` local configure
la base de datos real o un token fijo. Se fija antes de importar la aplicación (python-dotenv no
sobrescribe variables ya definidas)."""
import os

os.environ["API_BACKEND"] = "mock"
os.environ["API_TOKEN"] = ""
os.environ["API_CLIENT_ID"] = ""
os.environ["API_CLIENT_SECRET"] = ""
os.environ["API_SYNC_ON_DEMAND"] = "false"
