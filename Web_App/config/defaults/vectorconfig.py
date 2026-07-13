# --- VECTOR CONFIGURATION ---
VECTOR_DB_NAME = "postgres"
VECTOR_DB_USER = "postgres"
VECTOR_DB_PASSWORD = "5774"             # Local dev default; the VECTOR_DB_PASSWORD env var overrides this. Don't commit real secrets.
VECTOR_DB_HOST = "127.0.0.1"
VECTOR_DB_PORT = "5432"
BATCH_SIZE = 100
LOG_LEVEL = "ERROR"                     # Options: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
CHUNKS_FILE_NAME = "a_chunks.json"
PARQUET_FILENAME = "a_embeddings.parquet"
DB_TABLE_NAME = "mif_jsx"

