"""
TODO:
Verify data integrity after upload (to be implemented)

Load the data into the vector db by upserting the following artifacts:

- a_chunk.json: Contains the chunks and metadata
- a_embeddings.parquet: Contains embeddings (content + summary variants) with matching ids
- a_provenance.json: Provenance data stored in a separate table. An ID maps the file to upserted data

All chunk metadata plus the three embedding vectors now live in a single table (see DB_TABLE_NAME).
"""

import json
import os
import sys
import hashlib
from pathlib import Path
import psycopg2
from pgvector.psycopg2 import register_vector
from psycopg2.extras import execute_batch
from config.vectorconfig import *
from config.embedconfig import USE_PARQUET
from run_settings import CWD
from Logger.custom_logger import setup_global_logger

# Environment overrides win so secrets never need to live in the config file.
VECTOR_DB_PASSWORD = os.getenv("VECTOR_DB_PASSWORD", VECTOR_DB_PASSWORD)
VECTOR_DB_USER = os.getenv("VECTOR_DB_USER", VECTOR_DB_USER)
VECTOR_DB_HOST = os.getenv("VECTOR_DB_HOST", VECTOR_DB_HOST)

TABLE_NAME = DB_TABLE_NAME if 'DB_TABLE_NAME' in globals() else 'chunks'
EMBEDDING_FIELDS = (
    'embedding',
    'embedding_summary_chunk',
    'embedding_summary_page',
)

# Set up global logger with script-specific CSV header; overwrite existing log
script_base = os.path.splitext(os.path.basename(__file__))[0]
LOG_HEADER = ["Date", "Level", "Message"]
logger = setup_global_logger(script_name=script_base, log_level='INFO', headers=LOG_HEADER)

# 2. Load chunked data from chunks.json
chunks_path = CWD / CHUNKS_FILE_NAME
logger.info(f"Loading chunks from {chunks_path}")
try:
    with open(chunks_path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:
    logger.error(f"Failed to load chunks: {e}")
    sys.exit(1)

# Expect direct list format only (no legacy wrapper). Fail if not a list.
chunks = data
if not isinstance(chunks, list):
    logger.error("Expected chunks JSON to be a list of chunk objects. Found type: %s", type(chunks).__name__)
    sys.exit(1)
logger.info(f"Loaded chunks list with {len(chunks)} items")

# Require external provenance file a_provenance.json and abort if missing/unreadable
prov_file = CWD / "a_provenance.json"
external_prov = None
if not prov_file.exists():
    logger.warning(f"Required provenance file not found at {prov_file}. Aborting.")
    sys.exit(1)
try:
    with open(prov_file, "r", encoding="utf-8") as pf:
        external_prov = json.load(pf)
    logger.info(f"Loaded external provenance file: {prov_file}")
except Exception as e:
    logger.warning(f"Failed to read required provenance file {prov_file}: {e}. Aborting.")
    sys.exit(1)

# Optionally load embeddings from Parquet sidecar and merge into chunks by id
def _load_parquet_embeddings_if_available(cwd: Path):
    """
    Load (id, embedding) from a Parquet sidecar if present and return a dict id->list[float].

    Rationale:
    - When the embedding pipeline stores vectors in a Parquet file (USE_PARQUET=True), the JSON
      chunks file remains lightweight (embedding=None). For ingestion, we merge Parquet embeddings
      here by id so that downstream upsert code works unchanged.

    Notes:
    - Requires pyarrow to be installed. If not present, we skip with a warning.
    - Embeddings are expected to be float32 fixed-size lists per row; we convert to Python lists
      for psycopg2/pgvector.
    """
    sidecar_path = cwd / PARQUET_FILENAME
    if not sidecar_path.exists():
        return None
    try:
        import pyarrow.parquet as pq
    except Exception:
        logger.warning("pyarrow not installed; cannot read Parquet embeddings sidecar. Skipping.")
        return None
    try:
        table = pq.read_table(sidecar_path.as_posix())
        if 'id' not in table.column_names:
            logger.warning(f"Parquet file missing required 'id' column: {sidecar_path}")
            return None

        ids = table['id'].to_pylist()
        if 'embedding' not in table.column_names:
            logger.warning(f"Parquet file missing required 'embedding' column: {sidecar_path}")
            return None

        column_payloads = {}
        for field in EMBEDDING_FIELDS:
            if field in table.column_names:
                column_payloads[field] = table[field].to_pylist()
            else:
                column_payloads[field] = [None] * len(ids)

        mapping = {}
        for idx, cid in enumerate(ids):
            payload = {field: column_payloads[field][idx] for field in EMBEDDING_FIELDS}
            mapping[str(cid)] = payload

        logger.info(
            "Loaded %s embedding rows (content=%s, chunk_summary=%s, page_summary=%s) from Parquet sidecar: %s",
            len(mapping),
            sum(1 for row in column_payloads['embedding'] if row is not None),
            sum(1 for row in column_payloads['embedding_summary_chunk'] if row is not None),
            sum(1 for row in column_payloads['embedding_summary_page'] if row is not None),
            sidecar_path,
        )
        return mapping
    except Exception as e:
        logger.error(f"Failed to read Parquet embeddings: {e}")
        return None


def _coerce_vector(value, field_name=None, chunk_id=None):
    if value is None:
        return None
    if isinstance(value, list):
        return value if len(value) > 0 else None
    if isinstance(value, tuple):
        return list(value) if len(value) > 0 else None
    if field_name and chunk_id:
        logger.debug(
            "Dropping non-vector value for %s on chunk %s (type=%s)",
            field_name,
            chunk_id,
            type(value).__name__,
        )
    return None


def _merge_embeddings_into_chunks(chunk_list, embedding_map):
    """Merge embedding payloads (content + summary variants) into chunk metadata."""
    stats = {field: 0 for field in EMBEDDING_FIELDS}
    for chunk in chunk_list:
        cid = chunk.get('id')
        if cid is None:
            continue
        payload = None
        if embedding_map:
            payload = embedding_map.get(str(cid))
        if payload:
            for field in EMBEDDING_FIELDS:
                chunk[field] = _coerce_vector(payload.get(field), field, cid)
        for field in EMBEDDING_FIELDS:
            if _coerce_vector(chunk.get(field)) is not None:
                stats[field] += 1
    return stats


def _infer_vector_dimension(chunk_list):
    for chunk in chunk_list:
        vec = _coerce_vector(chunk.get('embedding'))
        if vec:
            return len(vec)
    return None

# 3. Validate and preprocess chunks (e.g., check for required metadata, clean up fields)
# If Parquet embeddings are available, merge them into the in-memory chunks before validation
parquet_map = _load_parquet_embeddings_if_available(CWD) if 'USE_PARQUET' in globals() and USE_PARQUET else None

# Prepare separate collections for metadata rows and embedding rows
valid_chunks = []
for chunk in chunks:
    chunk_id = chunk.get('id')
    if not chunk_id:
        logger.warning("Skipping chunk with missing id")
        continue
    valid_chunks.append(chunk)

if not valid_chunks:
    logger.error("No valid chunks were found in %s; aborting upsert.", chunks_path)
    sys.exit(1)

embedding_stats = _merge_embeddings_into_chunks(valid_chunks, parquet_map)
if embedding_stats.get('embedding', 0) == 0:
    logger.error("No primary content embeddings available from Parquet or JSON; aborting to avoid empty vector data.")
    sys.exit(1)

vector_dimension = _infer_vector_dimension(valid_chunks)
if not vector_dimension:
    logger.error("Unable to infer vector dimension from embedding data. Aborting.")
    sys.exit(1)

logger.info(
    "Validated %s chunks (skipped %s). Embedding availability -> content=%s, chunk_summary=%s, page_summary=%s | vector_dim=%s",
    len(valid_chunks),
    len(chunks) - len(valid_chunks),
    embedding_stats.get('embedding', 0),
    embedding_stats.get('embedding_summary_chunk', 0),
    embedding_stats.get('embedding_summary_page', 0),
    vector_dimension,
)


# 4. Connect to the vector database (PostgreSQL with pgvector)
def get_pg_connection():
    try:
        conn = psycopg2.connect(
            dbname=VECTOR_DB_NAME,
            user=VECTOR_DB_USER,
            password=VECTOR_DB_PASSWORD,
            host=VECTOR_DB_HOST,
            port=VECTOR_DB_PORT,
            options='-c search_path=public'  # Explicitly set the schema
        )
        # Register pgvector on this connection
        register_vector(conn)

        logger.info("Connected to PostgreSQL database.")
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        sys.exit(1)


def ensure_provenance_table(conn):
    """Create the provenance table if it does not already exist."""
    ddl = """
    CREATE TABLE IF NOT EXISTS public.provenance (
        prov_id TEXT PRIMARY KEY,
        chunk_model TEXT,
        chunk_size_range TEXT,
        chunk_keyword_density DOUBLE PRECISION,
        summary_model TEXT,
        summary_prompt TEXT,
        summary_size INTEGER,
        summary_temperature DOUBLE PRECISION,
        embed_model TEXT,
        embed_vectorsize INTEGER,
        created_date TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    );
    """
    with conn.cursor() as cur:
        cur.execute('SET search_path TO public;')
        cur.execute(ddl)
        conn.commit()
        logger.info("Ensured provenance table exists (public.provenance)")


def ensure_vector_extension(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        conn.commit()
        logger.info("Ensured pgvector extension is available")


def ensure_chunk_table(conn, vector_dim):
    qualified_name = TABLE_NAME if '.' in TABLE_NAME else f"public.{TABLE_NAME}"
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {qualified_name} (
        id TEXT PRIMARY KEY,
        filename TEXT,
        parent_id TEXT,
        id_prev TEXT,
        id_next TEXT,
        heading TEXT,
        header_level INTEGER,
        concat_header_path TEXT,
        chunk_type TEXT,
        content TEXT,
        code_friendly_name TEXT,
        examples JSONB,
        chunk_summary TEXT,
        page_summary TEXT,
        title TEXT,
        author TEXT,
        category TEXT,
        description TEXT,
        language TEXT,
        token_count INTEGER,
        embedding vector({vector_dim}),
        embedding_summary_chunk vector({vector_dim}),
        embedding_summary_page vector({vector_dim}),
        prov_id TEXT REFERENCES public.provenance (prov_id)
    );
    """
    with conn.cursor() as cur:
        cur.execute('SET search_path TO public;')
        cur.execute(ddl)
        cur.execute(f"ALTER TABLE {qualified_name} ADD COLUMN IF NOT EXISTS code_friendly_name TEXT;")

        # If table already exists with old vector dimensions, reconcile only when table is empty.
        cur.execute(
            "SELECT atttypmod FROM pg_attribute WHERE attrelid = %s::regclass AND attname = 'embedding' AND NOT attisdropped;",
            (qualified_name,),
        )
        row = cur.fetchone()
        existing_dim = None
        if row and row[0] and row[0] > 0:
            existing_dim = row[0] - 4

        if existing_dim and existing_dim != vector_dim:
            cur.execute(f"SELECT COUNT(*) FROM {qualified_name};")
            row_count = cur.fetchone()[0]
            if row_count == 0:
                logger.warning(
                    "Existing table %s has vector dimension %s; rebuilding vector columns to %s (table empty)",
                    qualified_name,
                    existing_dim,
                    vector_dim,
                )
                cur.execute(
                    f"""
                    ALTER TABLE {qualified_name}
                    DROP COLUMN IF EXISTS embedding,
                    DROP COLUMN IF EXISTS embedding_summary_chunk,
                    DROP COLUMN IF EXISTS embedding_summary_page,
                    ADD COLUMN embedding vector({vector_dim}),
                    ADD COLUMN embedding_summary_chunk vector({vector_dim}),
                    ADD COLUMN embedding_summary_page vector({vector_dim});
                    """
                )
            else:
                logger.error(
                    "Table %s uses vector dimension %s but incoming data is %s and table has %s rows. "
                    "Aborting to avoid destructive migration.",
                    qualified_name,
                    existing_dim,
                    vector_dim,
                    row_count,
                )
                conn.rollback()
                sys.exit(1)

        conn.commit()
        logger.info("Ensured chunks table exists (%s) with vector dimension %s", qualified_name, vector_dim)


"""
This is a debugging section. 
# Log environment variables that may affect connection
logger.info(f"PGHOST env: {os.environ.get('PGHOST')}")
logger.info(f"PGDATA env: {os.environ.get('PGDATA')}")

# Log all tables in the public schema to debug visibility
with conn.cursor() as cur:
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
    logger.info(f"Tables in public schema: {cur.fetchall()}")
    # Log the data_directory to confirm which instance is used
    cur.execute("SHOW data_directory;")
    logger.info(f"Script data_directory: {cur.fetchone()[0]}")
    # Log the current user
    cur.execute("SELECT current_user;")
    logger.info(f"Script user: {cur.fetchone()[0]}")
    # Log the current database
    cur.execute("SELECT current_database();")
    logger.info(f"Script database: {cur.fetchone()[0]}")
    # Log all tables in pg_tables for the public schema
    cur.execute("SELECT * FROM pg_tables WHERE schemaname = 'public';")
    logger.info(f"pg_tables: {cur.fetchall()}")

    cur.execute("SELECT current_database(), inet_server_addr(), inet_server_port();")
    print(cur.fetchone())
"""

def ensure_vector_indexes(conn, vector_dim):
    """Create ANN indexes on the vector columns so retrieval is not a seq scan.

    Prefers HNSW (pgvector >= 0.5); falls back to IVFFlat on older installs.
    Cosine ops match the normalized embeddings this pipeline produces.
    """
    qualified_name = TABLE_NAME if '.' in TABLE_NAME else f"public.{TABLE_NAME}"
    base = TABLE_NAME.split('.')[-1]
    columns = ('embedding', 'embedding_summary_chunk', 'embedding_summary_page')
    for column in columns:
        index_name = f"{base}_{column}_hnsw_idx"
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {index_name} ON {qualified_name} "
                    f"USING hnsw ({column} vector_cosine_ops);"
                )
            conn.commit()
            logger.info("Ensured HNSW index %s", index_name)
        except Exception as hnsw_err:
            conn.rollback()
            index_name = f"{base}_{column}_ivfflat_idx"
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"CREATE INDEX IF NOT EXISTS {index_name} ON {qualified_name} "
                        f"USING ivfflat ({column} vector_cosine_ops) WITH (lists = 100);"
                    )
                conn.commit()
                logger.info("Ensured IVFFlat index %s (HNSW unavailable: %s)", index_name, hnsw_err)
            except Exception as ivf_err:
                conn.rollback()
                logger.warning(
                    "Could not create ANN index on %s.%s (queries will seq-scan): %s",
                    qualified_name, column, ivf_err,
                )


conn = get_pg_connection()
logger.info(conn.get_dsn_parameters()['dbname'])
ensure_vector_extension(conn)
ensure_provenance_table(conn)
ensure_chunk_table(conn, vector_dimension)


# --- Upsert logic and batching implementation ---
def upsert_chunks(conn, chunks, batch_size=BATCH_SIZE):
    """
    Upsert chunks into the PostgreSQL database in batches.
    """
    # SQL for upsert (insert or update on conflict)
    upsert_sql = f"""
    INSERT INTO {TABLE_NAME} (
        id, filename, parent_id, id_prev, id_next,
        heading, header_level, concat_header_path, chunk_type,
        content, code_friendly_name, examples,
        chunk_summary, page_summary, title, author, category, description, language,
        token_count,
        embedding, embedding_summary_chunk, embedding_summary_page,
        prov_id
    ) VALUES (
        %(id)s, %(filename)s, %(parent_id)s, %(id_prev)s, %(id_next)s,
        %(heading)s, %(header_level)s, %(concat_header_path)s, %(chunk_type)s,
        %(content)s, %(code_friendly_name)s, %(examples)s,
        %(chunk_summary)s, %(page_summary)s, %(title)s, %(author)s, %(category)s, %(description)s, %(language)s,
        %(token_count)s,
        %(embedding)s, %(embedding_summary_chunk)s, %(embedding_summary_page)s,
        %(prov_id)s
    )
    ON CONFLICT (id) DO UPDATE SET
        filename = EXCLUDED.filename,
        parent_id = EXCLUDED.parent_id,
        id_prev = EXCLUDED.id_prev,
        id_next = EXCLUDED.id_next,
        heading = EXCLUDED.heading,
        header_level = EXCLUDED.header_level,
        concat_header_path = EXCLUDED.concat_header_path,
        chunk_type = EXCLUDED.chunk_type,
        content = EXCLUDED.content,
        code_friendly_name = EXCLUDED.code_friendly_name,
        examples = EXCLUDED.examples,
        chunk_summary = EXCLUDED.chunk_summary,
        page_summary = EXCLUDED.page_summary,
        title = EXCLUDED.title,
        author = EXCLUDED.author,
        category = EXCLUDED.category,
        description = EXCLUDED.description,
        language = EXCLUDED.language,
        token_count = EXCLUDED.token_count,
        embedding = EXCLUDED.embedding,
        embedding_summary_chunk = EXCLUDED.embedding_summary_chunk,
        embedding_summary_page = EXCLUDED.embedding_summary_page,
        prov_id = EXCLUDED.prov_id;
    """

    # Prepare data for upsert
    def prepare_chunk(chunk):
        # Prepare the dictionary for SQL upsert, handling missing fields and defaults
        # Field order matches chunker.py output order
        return {
            # 1) identity / linkage
            'id': chunk.get('id'),
            'filename': chunk.get('filename'),
            'parent_id': chunk.get('parent_id'),
            'id_prev': chunk.get('id_prev'),
            'id_next': chunk.get('id_next'),
            
            # 2) heading / structure
            'heading': chunk.get('heading'),
            'header_level': chunk.get('header_level'),
            'concat_header_path': chunk.get('concat_header_path'),
            'chunk_type': chunk.get('chunk_type'),
            
            # 3) content
            'content': chunk.get('content'),
            'code_friendly_name': chunk.get('code_friendly_name'),
            'examples': json.dumps(chunk.get('examples')) if 'examples' in chunk else None,
            
            # 4) summaries / metadata
            'chunk_summary': chunk.get('chunk_summary'),
            'page_summary': chunk.get('page_summary'),
            'title': chunk.get('title'),
            'author': chunk.get('author'),
            'category': chunk.get('category'),
            'description': chunk.get('description'),
            'language': chunk.get('language'),
            
            # 5) metrics / vectors
            'token_count': chunk.get('token_count'),
            'embedding': _coerce_vector(chunk.get('embedding'), 'embedding', chunk.get('id')),
            'embedding_summary_chunk': _coerce_vector(chunk.get('embedding_summary_chunk'), 'embedding_summary_chunk', chunk.get('id')),
            'embedding_summary_page': _coerce_vector(chunk.get('embedding_summary_page'), 'embedding_summary_page', chunk.get('id')),
            
            # 6) provenance reference
            'prov_id': chunk.get('prov_id'),
        }

    # Batch upsert. Track failures so the script can exit non-zero: a partial
    # upload must not report success to pipelinemanager / the web UI.
    failed_batches = 0
    failed_ids = []
    with conn.cursor() as cur:
        # Ensure the public schema is used
        cur.execute('SET search_path TO public;')
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i+batch_size]
            data = [prepare_chunk(chunk) for chunk in batch]
            try:
                execute_batch(cur, upsert_sql, data)
                conn.commit()
                logger.info(f"Upserted batch {i//batch_size + 1} ({len(batch)} chunks)")
            except Exception as e:
                conn.rollback()
                failed_batches += 1
                failed_ids.extend(c.get('id') for c in batch)
                logger.error(f"Failed to upsert batch {i//batch_size + 1}: {e}")

    if failed_batches:
        logger.error(
            "Upsert finished with %s failed batch(es); %s chunks not stored. First failed ids: %s",
            failed_batches, len(failed_ids), failed_ids[:10],
        )
    return failed_batches

def upsert_provenance(conn, provenance_data):
    """
    Upsert provenance data into the PostgreSQL provenance table.
    """
    if not provenance_data:
        logger.warning("No provenance data to upsert")
        return
    
    # SQL for provenance upsert
    provenance_upsert_sql = """
    INSERT INTO public.provenance (
        prov_id, 
        chunk_model, chunk_size_range, chunk_keyword_density,
        summary_model, summary_prompt, summary_size, summary_temperature,
        embed_model, embed_vectorsize,
        created_date
    ) VALUES (
        %(prov_id)s,
        %(chunk_model)s, %(chunk_size_range)s, %(chunk_keyword_density)s,
        %(summary_model)s, %(summary_prompt)s, %(summary_size)s, %(summary_temperature)s,
        %(embed_model)s, %(embed_vectorsize)s,
        CURRENT_TIMESTAMP
    )
    ON CONFLICT (prov_id) DO UPDATE SET
        chunk_model = EXCLUDED.chunk_model,
        chunk_size_range = EXCLUDED.chunk_size_range,
        chunk_keyword_density = EXCLUDED.chunk_keyword_density,
        summary_model = EXCLUDED.summary_model,
        summary_prompt = EXCLUDED.summary_prompt,
        summary_size = EXCLUDED.summary_size,
        summary_temperature = EXCLUDED.summary_temperature,
        embed_model = EXCLUDED.embed_model,
        embed_vectorsize = EXCLUDED.embed_vectorsize,
        created_date = CURRENT_TIMESTAMP;
    """
    
    # Prepare provenance data for upsert
    def prepare_provenance(prov_data):
        # New shape only: { chunk: {...}, summary: {...}, embed: {...}, prov_id: ... }
        if not isinstance(prov_data, dict):
            logger.error("Provenance payload must be an object with keys: chunk, summary, embed, prov_id")
            return None
        root = prov_data
        chunk_config = root.get('chunk', {})
        summary_config = root.get('summary', {})
        embed_config = root.get('embed', {})

        # derive a sane integer vector size (fall back to actual embedding length if present)
        try:
            vector_dim = embed_config.get('vector_dim')
            if vector_dim is None:
                vector_dim = embed_config.get('vectorsize')  # tolerate alt key if present
            if isinstance(vector_dim, str):
                # ignore non-numeric strings (e.g., "float32")
                vector_dim = int(vector_dim) if vector_dim.isdigit() else None
        except Exception:
            vector_dim = None
        if not vector_dim:
            # try to infer from data (first valid chunk)
            try:
                vector_dim = len(valid_chunks[0]['embedding'])
            except Exception:
                vector_dim = 1536  # safe default

        # coerce summary_size to int if present
        try:
            summary_size = summary_config.get('size')
            summary_size = int(summary_size) if summary_size is not None else None
        except Exception:
            summary_size = None

        # Require prov_id in the provenance file; if absent, generate a stable one
        prov_id = root.get('prov_id')
        if not prov_id:
            fingerprint_payload = {
                'chunk': root.get('chunk', {}),
                'summary': root.get('summary', {}),
                'embed': root.get('embed', {}),
            }
            digest = hashlib.sha1(
                json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False).encode('utf-8')
            ).hexdigest()[:12]
            prov_id = f"prov_{digest}"
            root['prov_id'] = prov_id
            logger.warning("Provenance file missing 'prov_id'. Generated deterministic prov_id=%s", prov_id)

        return {
            'prov_id': prov_id,
            'chunk_model': chunk_config.get('model'),
            'chunk_size_range': chunk_config.get('chunk_size_range'),
            'chunk_keyword_density': chunk_config.get('keyword_density'),
            'summary_model': summary_config.get('model'),
            'summary_prompt': summary_config.get('prompt'),
            'summary_size': summary_size,
            'summary_temperature': summary_config.get('temperature'),
            'embed_model': embed_config.get('basemodel') or embed_config.get('model'),
            'embed_vectorsize': vector_dim,
        }

    payload_items = provenance_data if isinstance(provenance_data, list) else [provenance_data]
    records = []
    for item in payload_items:
        prepared = prepare_provenance(item)
        if prepared:
            records.append(prepared)

    if not records:
        logger.error("No valid provenance payload generated; aborting to satisfy FK constraint.")
        sys.exit(1)

    with conn.cursor() as cur:
        cur.execute('SET search_path TO public;')
        try:
            execute_batch(cur, provenance_upsert_sql, records)
            conn.commit()
            logger.info(f"Upserted {len(records)} provenance record(s)")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to upsert provenance: {e}")
            raise


# Upsert provenance first (prefer external file), then chunks
provenance_payload = external_prov
if not provenance_payload:
    logger.warning("Required provenance payload missing after read. Aborting.")
    sys.exit(1)
upsert_provenance(conn, provenance_payload)

# Apply provenance id to chunks that do not already have one
if isinstance(provenance_payload, dict):
    resolved_prov_id = provenance_payload.get('prov_id')
elif isinstance(provenance_payload, list):
    resolved_prov_id = next((p.get('prov_id') for p in provenance_payload if isinstance(p, dict) and p.get('prov_id')), None)
else:
    resolved_prov_id = None

if resolved_prov_id:
    reassigned = 0
    for chunk in valid_chunks:
        if chunk.get('prov_id') != resolved_prov_id:
            chunk['prov_id'] = resolved_prov_id
            reassigned += 1
    if reassigned:
        logger.info("Normalized prov_id=%s on %s chunk(s) for consistent FK linkage", resolved_prov_id, reassigned)

# Call the upsert function with validated chunks
failed_batches = upsert_chunks(conn, valid_chunks)

# Create ANN indexes after data load (building indexes on populated tables is
# faster than maintaining them during the initial bulk insert).
ensure_vector_indexes(conn, vector_dimension)

if failed_batches:
    logger.error("Upsert completed with failures; exiting non-zero.")
    sys.exit(1)
logger.info("Upsert completed successfully.")



