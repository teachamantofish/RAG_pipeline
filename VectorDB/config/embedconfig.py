
# ====== Embedding configuration =======
ENABLE_EMBEDDING = True                 # Enable or disable embedding step for tests
EMBED_MODEL = "Qwen3Embed.6B-trained"   # HF model ID or local dir name (resolved relative to DATASET_ROOT)
ADAPTER_PATH = ""                       # Relative path to LoRA/PEFT adapter dir. Empty = no adapter (model already has fine-tuned weights).
EMBED_LOG_LEVEL = "INFO"                # Options: "DEBUG", "INFO", "WARNING", "ERROR".

# ====== Precision controls for embeddings ======
EMBED_COMPUTE_PRECISION = "float16"     # Rationale: fast on RTX; negligible impact for retrieval
EMBED_OUTPUT_PRECISION  = "float32"     # Rationale: pgvector and most tools expect float32
ENABLE_TF32 = True                      # Enable TensorFloat-32 on supported GPUs when EMBED_COMPUTE_PRECISION=tf32 or float32. TF32 accelerates matmul/convolution on Ampere+ architectures.
EMBED_VECTOR_DIM = 1024                 # Embedding dimension. Must have model support. Higher is better but bigger.
ENFORCE_EMBED_VECTOR_DIM = False        # When True, the script will fail if the model-reported dimension does not match EMBED_VECTOR_DIM. 

# ====== Parquet sidecar for embeddings ======
USE_PARQUET = True                      # Use lossless Parquet sidecar for embeddings to reduce JSON size and speed I/O
PARQUET_FILENAME = "a_embeddings.parquet" # Parquet output file name (next to a_chunks.json)
PARQUET_COMPRESSION = "zstd"            # Lossless compression codec. 'zstd' provides good ratio/speed. Alternatives: 'snappy', 'gzip'.
PARQUET_ROW_GROUP_SIZE = 8192           # Optional row group size for Parquet writes (number of rows per group).

# ====== Quality, batching, and serialization I/O ======
EMBED_BATCH_SIZE = 64                   # Texts per GPU batch for SentenceTransformer.encode. Lower if you hit CUDA OOM.
NORMALIZE_EMBEDDINGS = True             # Normalize to unit length (L2) before persistence.
JSON_INDENT = 2                         # 0 or None shrinks file size (harder to read); 2 is human-friendly.
STRIP_EMBEDDINGS_IN_JSON = True         # Strip embeddings from JSON (set them to None) to keep it lightweight.
PROVENANCE_REQUIRED = True              # If provenance file is missing, fail or create a minimal one.

# ====== Sampling controls for tests ============================
MAX_EMBED_CHUNKS = None                 # For test runs. None = embed all chunks. Override with EMBED_MAX_CHUNKS env var.
CHUNK_SAMPLE_SEED = 42                  # Seed for random sampling when CHUNK_SAMPLE_MODE="random".
CHUNK_SAMPLE_MODE = "head"              # For testing only: Use "head" for speed; use "random" to improve representativeness.

# ====== Hardware control ======
DEVICE_ID = 0                           # CUDA device index to use (0-based). Set to None to auto-select first visible GPU.


# ====== Precision controls for embeddings ======
#
# - EMBED_COMPUTE_PRECISION controls the model parameter and math precision used
#   during forward pass on the GPU. Lower precision (fp16/bf16) is typically
#   faster and uses less memory on modern NVIDIA GPUs, at the cost of small
#   numerical differences compared to fp32.
#   Allowed values: "float32", "float16", "bfloat16", "tf32"
#     * "float32"  -> standard single-precision compute
#     * "float16"  -> half-precision compute. Near-equal retrieval quality; models may upcast internally; db stores fp32 anyway.
#     * "bfloat16" -> brain-float 16 compute (fast on newer GPUs)
#     * "tf32"     -> uses float32 tensors but allows TF32 matmul onsupported GPUs. Improves speed with minor precision loss; set via CUDA/CuDNN flags.
#
# - EMBED_OUTPUT_PRECISION controls the dtype used for the vectors you persist
#   to disk (e.g., JSON/NPY). For JSON, dtype becomes plain floats but the
#   values will be cast from this dtype first. For binary formats, dtype is
#   preserved exactly.
#   Allowed values: "float32", "float16", "bfloat16"

# EMBED_VECTOR_DIM = 2560 This does NOT change the model's
# native output; it is an expectation used for validation/logging and to align
# downstream systems (e.g., pgvector column dimension). If you want a different
# dimension than the model outputs, add a dimensionality reduction step (e.g.,
# PCA to 1024) prior to persistence and ingestion.

# ====== Parquet sidecar for embeddings ======
# Use Parquet sidecar for embeddings to reduce JSON size and speed I/O, while
# preserving exact float values (lossless). The JSON chunks file will omit the
# heavy embedding arrays (set to None) and a separate Parquet file will contain
# (id, embedding) rows. Downstream ingestion should read Parquet and join on id.

# Optional row group size for Parquet writes (number of rows per group). None lets
# Arrow choose. Smaller groups improve parallelism and incremental reads; larger
# groups can improve compression. Typical values: 4_096 to 65_536.


#########################################
# Quality, batching, serialization I/O
#########################################
# Normalize embedding: 
#
# Normalize to unit length (L2) before persistence.
# Rationale: cosine similarity assumes unit vectors; some models already
# output normalized vectors but this ensures consistency across sources.
#
# JSON indentation level 
# 
# Rationale: 0 or None shrinks file size (harder to read); 2 is human-friendly.
# When USE_PARQUET=True, most size comes from the Parquet sidecar anyway.
# 
# Strip embeddings from JSON chunks
#
# When writing Parquet, also strip embeddings from JSON chunks (set them to None)
# to keep the JSON light. If set to False, embeddings are kept in JSON as well
# (redundant, larger files).
# Rationale: keep metadata human-readable and vectors in compact binary.
# 
# Provenance file required
#
# If provenance file is missing, decide whether to fail or create a minimal one.
# Rationale: pipelines in dev might not have provenance yet; production should.

# CHUNK_SAMPLE_MODE = "head"  # Use "head" for speed; use "random" to improve representativeness.
# How to choose the subset when MAX_EMBED_CHUNKS is set.
#   - "head": take the first N chunks (stable, fastest)
#   - "random": uniform random sample of N chunks (use CHUNK_SAMPLE_SEED)
# Rationale: control representativeness vs reproducibility of tests.

# Notes:
# - These settings do NOT change the embedding dimension; they affect speed,
#   memory, and the numeric precision of computations/outputs.
# - If you switch to a binary storage format later (e.g., .npy), the
#   EMBED_OUTPUT_PRECISION will directly map to file dtype.

# Notes:
# 
# If you embed with another model family (e.g. Cohere, Qwen, Instructor), you'd swap in their tokenizer.
# Back‑of‑the‑envelope for pgvector storage:
# 2560 dims × 4 bytes ≈ 10 KB per row (plus Postgres/JSON overhead).
# 35k rows ≈ 350 MB data; with HNSW/IVF indexes, expect more on-disk. Dim reduction to 1024 dims would cut that to ~140 MB before index overhead.


