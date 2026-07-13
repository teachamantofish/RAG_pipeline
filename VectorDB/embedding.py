'''
Create embeddings for the chunk file and persist them.

Vectors are persisted to a Parquet sidecar (USE_PARQUET=True) or written back
into the chunks JSON (USE_PARQUET=False / STRIP_EMBEDDINGS_IN_JSON=False), so
downstream ingestion always has them on disk.

Performance notes:
- Texts are encoded in GPU batches (EMBED_BATCH_SIZE) instead of one call per
  chunk.
- Summary texts are deduplicated before encoding: the page summary is identical
  for every chunk in an H1 group, so it is embedded once and reused.
'''

import os
import copy
import json
import sys
import torch
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer
from config.embedconfig import *
from run_settings import CWD as DATASET_ROOT
from Logger.custom_logger import setup_global_logger

embedfile = DATASET_ROOT / "a_chunks.json"  # Get json file with chunks to embed.
post_embed_file = embedfile.with_name("a_chunks_postembedding.json")
ADAPTER_DIR = (DATASET_ROOT / Path(ADAPTER_PATH)).resolve() if ADAPTER_PATH else None

# Set up global logger with script-specific CSV header; overwrite existing log
script_base = os.path.splitext(os.path.basename(__file__))[0]
LOG_HEADER = ["Date", "Level", "Message", "Chunk ID"]
logger = setup_global_logger(script_name=script_base, log_level=EMBED_LOG_LEVEL, headers=LOG_HEADER)

SUMMARY_EMBED_FIELDS = (
    ("chunk_summary", "embedding_summary_chunk"),
    ("page_summary", "embedding_summary_page"),
)


def _attach_lora_adapter(sentence_model: SentenceTransformer, adapter_dir: Path) -> None:
    """Load PEFT LoRA weights into the underlying transformer inside SentenceTransformer."""
    try:
        from peft import PeftModel
    except Exception as exc:  # pragma: no cover - import guard
        logger.error(
            "Adapter path %s provided but 'peft' is unavailable (%s). Install peft to use fine-tuned adapters.",
            adapter_dir,
            exc,
        )
        sys.exit(1)

    first_module = None
    base_transformer = None
    if hasattr(sentence_model, "_first_module"):
        try:
            first_module = sentence_model._first_module()
            base_transformer = getattr(first_module, "auto_model", None)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to access base transformer module for adapter loading: %s", exc)
            base_transformer = None

    if base_transformer is None:
        logger.error("Unable to locate underlying transformer to attach adapter; skipping adapter load.")
        return

    logger.info("Loading LoRA adapter weights from %s", adapter_dir)
    try:
        peft_model = PeftModel.from_pretrained(base_transformer, adapter_dir.as_posix(), is_trainable=False)
        # Replace the auto_model reference so downstream encode() uses the adapted weights.
        if first_module is None:
            logger.error("SentenceTransformer structure changed unexpectedly; adapter not applied.")
            return
        first_module.auto_model = peft_model
        logger.info("LoRA adapter applied successfully; embeddings will use fine-tuned weights.")
    except Exception as exc:
        logger.error("Failed to load adapter from %s: %s", adapter_dir, exc)
        raise


def _fmt_size(n_bytes: int) -> str:
    """Human-readable file size formatter."""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n_bytes)
    for u in units:
        if size < 1024.0 or u == units[-1]:
            return f"{size:.1f} {u}"
        size /= 1024.0


def update_provenance_with_embedding():
    """Load a_provenance.json, add embedding details, and save back."""
    prov_path = DATASET_ROOT / "a_provenance.json"

    try:
        with open(prov_path, "r", encoding="utf-8") as f:
            provenance = json.load(f)
    except FileNotFoundError:
        if globals().get('PROVENANCE_REQUIRED'):
            logger.error(f"Provenance file not found at {prov_path}. Aborting.")
            sys.exit(1)
        logger.warning(f"Provenance file not found at {prov_path}. Creating a new one.")
        provenance = {}

    provenance["embed"] = {
        "basemodel": EMBED_MODEL,
        "adaptermodel": ADAPTER_PATH,
        # Precision and dimension settings captured for reproducibility and auditing
        "compute_precision": EMBED_COMPUTE_PRECISION,
        "output_precision": EMBED_OUTPUT_PRECISION,
        "vector_dim": EMBED_VECTOR_DIM,
    }

    with open(prov_path, "w", encoding="utf-8") as f:
        json.dump(provenance, f, indent=2, ensure_ascii=False)

    logger.info(f"Updated provenance file at {prov_path}")


# ---------------- sampling controls (test runs) ----------------
def _get_max_chunks_from_env(default_val):
    val = os.getenv("EMBED_MAX_CHUNKS")
    if not val:
        return default_val
    try:
        n = int(val)
        return n if n > 0 else None
    except Exception:
        logger.warning(f"Ignoring invalid EMBED_MAX_CHUNKS env value: {val}")
        return default_val


def _get_sample_mode_from_env(default_val):
    val = os.getenv("EMBED_SAMPLE_MODE")
    if not val:
        return default_val
    v = val.strip().lower()
    if v in ("head", "random"):
        return v
    logger.warning(f"Ignoring invalid EMBED_SAMPLE_MODE env value: {val}")
    return default_val


def _select_indices(chunks):
    """Return the set of chunk indices to embed, or None for all."""
    max_n = _get_max_chunks_from_env(globals().get('MAX_EMBED_CHUNKS', None))
    if isinstance(max_n, str):
        # Defensive: a config edited through the UI may hold "None"/"" strings.
        max_n = int(max_n) if max_n.strip().isdigit() else None
    sample_mode = _get_sample_mode_from_env(globals().get('CHUNK_SAMPLE_MODE', 'head'))
    sample_seed = globals().get('CHUNK_SAMPLE_SEED', 42)

    if not isinstance(chunks, list) or max_n is None:
        logger.info("Embedding all available chunks (no sampling limit configured)")
        return None

    total = len(chunks)
    n = min(max_n, total)
    if n >= total:
        logger.info("Sampling configured but N >= total; embedding all chunks")
        return None
    if sample_mode == 'random':
        import random
        rng = random.Random(sample_seed)
        selected = set(rng.sample(range(total), n))
        logger.info(f"Sampling {n} chunks uniformly at random (seed={sample_seed}) out of {total}")
        return selected
    logger.info(f"Taking first {n} chunks (head mode) out of {total}")
    return set(range(n))


# ---------------- device & precision ----------------
def _dtype_from_string(name: str):
    name = (name or "").strip().lower()
    if name in ("fp32", "float32"): return torch.float32
    if name in ("fp16", "float16", "half"): return torch.float16
    if name in ("bf16", "bfloat16"): return torch.bfloat16
    if name == "tf32": return torch.float32  # tensors remain float32; TF32 enabled via flags below
    raise ValueError(f"Unsupported precision string: {name}")


def _setup_device() -> str:
    """Enforce CUDA usage and return the device string (exits on failure)."""
    if not torch.cuda.is_available():
        logger.error(
            "CUDA is required for embeddings but was not found. "
            "Please install a CUDA-enabled PyTorch build and ensure an NVIDIA GPU is available."
        )
        sys.exit(1)

    try:
        device_id = globals().get('DEVICE_ID')
        if device_id is not None and not isinstance(device_id, int):
            device_id = None
        if device_id is not None:
            count = torch.cuda.device_count()
            if device_id < 0 or device_id >= count:
                logger.error(f"Configured DEVICE_ID={device_id} is out of range. Visible GPUs: {count}")
                sys.exit(1)
            device_index = device_id
        else:
            device_index = 0
        device = f"cuda:{device_index}"
        logger.info(f"Using GPU {device_index}: {torch.cuda.get_device_name(device_index)}")
        return device
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"Failed to select/query CUDA device: {e}")
        sys.exit(1)


def _load_model(device: str, compute_dtype) -> SentenceTransformer:
    # Configure TF32 acceleration for float32 compute if requested and supported
    try:
        if ENABLE_TF32 and EMBED_COMPUTE_PRECISION.strip().lower() in ("tf32", "float32", "fp32"):
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            if hasattr(torch, "set_float32_matmul_precision"):
                torch.set_float32_matmul_precision("high")
            logger.info("TF32 acceleration enabled for float32 compute paths")
    except Exception:
        pass

    # Resolve model: try DATASET_ROOT-relative local path first, then treat as HF model ID
    local_model_path = DATASET_ROOT / EMBED_MODEL
    if local_model_path.exists():
        resolved_model = str(local_model_path)
        logger.info("Using local model at %s", resolved_model)
    else:
        resolved_model = EMBED_MODEL  # Treat as HuggingFace model ID
        logger.info("Loading model from HuggingFace: %s", resolved_model)
    model = SentenceTransformer(resolved_model, device=device)

    if ADAPTER_DIR:
        if ADAPTER_DIR.exists():
            _attach_lora_adapter(model, ADAPTER_DIR)
        else:
            logger.warning(
                "Configured adapter path '%s' not found at %s; falling back to base model weights.",
                ADAPTER_PATH,
                ADAPTER_DIR,
            )

    try:
        model = model.to(dtype=compute_dtype, device=device)
        logger.info(f"Embedding compute precision set to {EMBED_COMPUTE_PRECISION}")
    except Exception as e:
        logger.warning(f"Could not set model dtype to {EMBED_COMPUTE_PRECISION}: {e}")

    # Defensive check: ensure the model is targeting CUDA (no silent CPU fallback)
    td_str = str(getattr(model, "device", "unknown"))
    if not td_str.startswith("cuda"):
        logger.error(f"Model target device is not CUDA (got: {td_str}). Aborting.")
        sys.exit(1)
    return model


def _check_vector_dim(model) -> int:
    emb_dim = None
    try:
        emb_dim = model.get_sentence_embedding_dimension()
        logger.info(f"This model's embedding dimension: {emb_dim}")
    except Exception:
        pass

    cfg_dim = globals().get('EMBED_VECTOR_DIM')
    if isinstance(cfg_dim, int) and cfg_dim > 0 and emb_dim is not None and cfg_dim != emb_dim:
        msg = (
            f"Configured EMBED_VECTOR_DIM={cfg_dim} does not match model-reported dimension {emb_dim}. "
            "No automatic dimensionality change is applied in this script. Ensure downstream consumers "
            "(e.g., pgvector column and indexes) match the actual dimension, or add an explicit projection/"
            "reduction step to produce the configured dimension before persistence."
        )
        if globals().get('ENFORCE_EMBED_VECTOR_DIM'):
            logger.error(msg)
            sys.exit(1)
        logger.warning(msg)
    return emb_dim


# ---------------- encoding ----------------
def _should_skip_chunk(chunk) -> bool:
    """Chunks flagged non-embeddable by the chunker (empty headings)."""
    if chunk.get('embed') is False:
        return True
    return str(chunk.get('embedding')).lower() == 'false'


def _encode_batch(model, texts, device, output_dtype):
    """Encode a list of texts; returns a list of Python float lists."""
    batch_size = int(globals().get('EMBED_BATCH_SIZE', 64) or 64)
    tensor = model.encode(
        texts,
        batch_size=batch_size,
        convert_to_tensor=True,
        device=device,
        normalize_embeddings=NORMALIZE_EMBEDDINGS,
        show_progress_bar=True,
    )
    tensor = tensor.to(dtype=output_dtype)
    return tensor.detach().cpu().tolist()


def embed_chunks(chunks, model, device, output_dtype, selected_indices):
    """Populate embedding fields on the chunk dicts. Returns counters dict."""
    counters = {"embedded": 0, "skipped_unselected": 0, "skipped_flagged": 0}

    if not ENABLE_EMBEDDING:
        for chunk in chunks:
            chunk['embedding'] = None
        logger.info("ENABLE_EMBEDDING is False; skipping all embeddings.")
        return counters

    candidates = []
    for idx, chunk in enumerate(chunks):
        if selected_indices is not None and idx not in selected_indices:
            counters["skipped_unselected"] += 1
            continue
        if _should_skip_chunk(chunk):
            # Normalize representation to None for downstream consumers
            chunk['embedding'] = None
            counters["skipped_flagged"] += 1
            logger.info(f"Embedding skipped for {chunk.get('id')} (flagged non-embeddable).")
            continue
        candidates.append(chunk)

    if not candidates:
        logger.warning("No chunks eligible for embedding.")
        return counters

    # --- content embeddings (batched) ---
    # Build enriched text: align with training by including heading + friendly names
    content_texts = []
    for chunk in candidates:
        heading = (chunk.get("concat_header_path") or "").strip()
        friendly = (chunk.get("code_friendly_name") or "").strip()
        text = f"{heading}: {chunk['content']}" if heading else chunk['content']
        if friendly:
            text = f"{text}\n{friendly}"
        content_texts.append(text)

    logger.info("Encoding %d content texts (batched)...", len(content_texts))
    content_vectors = _encode_batch(model, content_texts, device, output_dtype)
    for chunk, vector in zip(candidates, content_vectors):
        chunk['embedding'] = vector
    counters["embedded"] = len(candidates)

    # --- summary embeddings (deduplicated + batched) ---
    # Page summaries repeat across every chunk of an H1 group; embed each
    # distinct text once and fan the vector out.
    unique_texts = []
    text_to_index = {}
    for chunk in candidates:
        for summary_key, _embed_key in SUMMARY_EMBED_FIELDS:
            text = (chunk.get(summary_key) or "").strip()
            if text and text.lower() != "false" and text not in text_to_index:
                text_to_index[text] = len(unique_texts)
                unique_texts.append(text)

    summary_vectors = []
    if unique_texts:
        logger.info("Encoding %d unique summary texts (deduplicated, batched)...", len(unique_texts))
        summary_vectors = _encode_batch(model, unique_texts, device, output_dtype)

    for chunk in candidates:
        for summary_key, embed_key in SUMMARY_EMBED_FIELDS:
            text = (chunk.get(summary_key) or "").strip()
            if text and text.lower() != "false":
                chunk[embed_key] = summary_vectors[text_to_index[text]]
            else:
                chunk[embed_key] = None  # no summary -> no summary embedding

    logger.info("Embedding complete: %d chunks embedded.", counters["embedded"])
    return counters


# ---------------- persistence ----------------
def save_embeddings_to_parquet(chunks_list, parquet_path: Path, emb_dim: int, compression: str = "zstd", row_group_size: int | None = None):
    """
    Write embeddings to a Parquet sidecar file with schema: [id: string, embedding: fixed_size_list<float32, emb_dim>].

    Parquet stores binary floats with compression, dramatically reducing size
    and speeding I/O versus JSON, while remaining lossless (exact float32).
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception as e:
        raise RuntimeError(
            "pyarrow is required to write Parquet embeddings. Install it with: pip install pyarrow"
        ) from e

    ids = []
    vectors = []
    summary_vectors = []
    page_vectors = []
    for ch in chunks_list:
        emb = ch.get('embedding')
        emb_sum = ch.get('embedding_summary_chunk')
        emb_page = ch.get('embedding_summary_page')
        cid = ch.get('id')
        if cid is None:
            continue
        if emb is None:
            continue
        # Only accept list/tuple/ndarray; skip strings/bools or malformed entries
        if not isinstance(emb, (list, tuple, np.ndarray)):
            logger.warning(f"Skipping embedding for id={cid}: non-numeric type {type(emb).__name__}")
            continue
        try:
            vec = np.asarray(emb, dtype=np.float32)
        except Exception as conv_e:
            logger.warning(f"Skipping embedding for id={cid}: cannot convert to float32 ({conv_e})")
            continue
        if vec.ndim != 1 or (emb_dim is not None and vec.shape[0] != emb_dim):
            logger.warning(f"Skipping embedding for id={cid}: wrong shape {vec.shape}, expected ({emb_dim},)")
            continue
        ids.append(str(cid))
        vectors.append(vec)

        # Keep summaries aligned by id; if missing or invalid, store None
        def _coerce_or_none(val, label):
            if val is None:
                return None
            if not isinstance(val, (list, tuple, np.ndarray)):
                logger.warning(f"Skipping {label} for id={cid}: non-numeric type {type(val).__name__}")
                return None
            try:
                v = np.asarray(val, dtype=np.float32)
            except Exception as conv_e:
                logger.warning(f"Skipping {label} for id={cid}: cannot convert to float32 ({conv_e})")
                return None
            if v.ndim != 1 or (emb_dim is not None and v.shape[0] != emb_dim):
                logger.warning(f"Skipping {label} for id={cid}: wrong shape {v.shape}, expected ({emb_dim},)")
                return None
            return v

        summary_vectors.append(_coerce_or_none(emb_sum, "embedding_summary_chunk"))
        page_vectors.append(_coerce_or_none(emb_page, "embedding_summary_page"))

    if not vectors:
        logger.warning("No embeddings available to write to Parquet; skipping.")
        return 0

    # Flatten vectors to a single values array, then form a FixedSizeListArray
    flat = np.concatenate(vectors).astype(np.float32, copy=False)
    values = pa.array(flat, type=pa.float32())

    # Prefer FixedSizeList if available, else fall back to list_ with list_size
    try:
        emb_array = pa.FixedSizeListArray.from_arrays(values, emb_dim)
    except AttributeError:
        # Older Arrow versions: build offsets for a regular ListArray
        offsets = pa.array([i * emb_dim for i in range(len(vectors) + 1)], type=pa.int32())
        emb_array = pa.ListArray.from_arrays(offsets, values)

    arrays = [pa.array(ids, type=pa.string()), emb_array]
    names = ["id", "embedding"]

    # Optional summary embeddings (preserve row alignment; nulls allowed)
    def _to_list_array(vecs):
        if not vecs or all(v is None for v in vecs):
            return None
        vals = []
        offsets = [0]
        mask = []
        for v in vecs:
            if v is None:
                offsets.append(offsets[-1])
                mask.append(True)
                continue
            vals.append(v)
            offsets.append(offsets[-1] + emb_dim)
            mask.append(False)
        flat_local = np.concatenate(vals).astype(np.float32, copy=False)
        values_local = pa.array(flat_local, type=pa.float32())
        offsets_arr = pa.array(offsets, type=pa.int32())
        return pa.ListArray.from_arrays(offsets_arr, values_local)

    sum_array = _to_list_array(summary_vectors)
    if sum_array is not None:
        arrays.append(sum_array)
        names.append("embedding_summary_chunk")

    page_array = _to_list_array(page_vectors)
    if page_array is not None:
        arrays.append(page_array)
        names.append("embedding_summary_page")

    table = pa.Table.from_arrays(arrays, names=names)

    parquet_path = Path(parquet_path)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, parquet_path.as_posix(), compression=compression, row_group_size=row_group_size)
    logger.info(f"Wrote {len(ids)} embeddings to Parquet: {parquet_path} (dim={emb_dim}, codec={compression})")
    return len(ids)


def persist_outputs(chunks, model, embedded_count) -> None:
    """Write embeddings + metadata to disk and verify vectors were persisted.

    - USE_PARQUET=True: vectors go to the Parquet sidecar; the chunks JSON
      stays lightweight (embedding=None) for the upsert step to merge.
    - USE_PARQUET=False: vectors are written INTO a_chunks.json, which is what
      upsert_to_vectordb.py reads when there is no sidecar. (A previous
      version dropped the vectors entirely in this mode.)
    """
    strip_json = bool(globals().get('STRIP_EMBEDDINGS_IN_JSON', True)) and USE_PARQUET

    # Always save a vector-free copy for human inspection/diffing.
    stripped_chunks = copy.deepcopy(chunks)
    for ch in stripped_chunks:
        for key in ('embedding', 'embedding_summary_chunk', 'embedding_summary_page'):
            if ch.get(key) is not None:
                ch[key] = None
    with open(post_embed_file, 'w', encoding='utf-8') as f:
        json.dump(stripped_chunks, f, indent=JSON_INDENT, ensure_ascii=False)
    logger.info(f"Saved stripped post-embedding JSON (no vectors) to {post_embed_file}")

    persisted_vectors = 0

    if USE_PARQUET:
        parquet_file = DATASET_ROOT / PARQUET_FILENAME
        emb_dim = None
        try:
            emb_dim = model.get_sentence_embedding_dimension()
        except Exception:
            for ch in chunks:
                if isinstance(ch.get('embedding'), list):
                    emb_dim = len(ch['embedding'])
                    break
        if not emb_dim:
            logger.error("Could not determine embedding dimension for Parquet output.")
            sys.exit(1)
        persisted_vectors = save_embeddings_to_parquet(
            chunks, parquet_file, emb_dim, PARQUET_COMPRESSION, PARQUET_ROW_GROUP_SIZE
        )
        try:
            logger.info("Parquet size: %s", _fmt_size(parquet_file.stat().st_size))
        except Exception:
            pass

    if not strip_json:
        # Persist vectors inline so the upsert step can read them from JSON.
        with open(embedfile, 'w', encoding='utf-8') as f:
            json.dump(chunks, f, indent=JSON_INDENT, ensure_ascii=False)
        persisted_vectors = max(
            persisted_vectors,
            sum(1 for ch in chunks if isinstance(ch.get('embedding'), list)),
        )
        logger.info(f"Saved chunks WITH embeddings to {embedfile}")

    if embedded_count and not persisted_vectors:
        logger.error(
            "Computed %s embeddings but persisted none (check USE_PARQUET/pyarrow). Aborting.",
            embedded_count,
        )
        sys.exit(1)


def main() -> None:
    with open(embedfile, 'r', encoding='utf-8') as f:
        chunks = json.load(f)
    logger.info("Loaded chunks from %s; count=%d", embedfile, len(chunks) if hasattr(chunks, '__len__') else 0)

    selected_indices = _select_indices(chunks)
    device = _setup_device()

    try:
        compute_dtype = _dtype_from_string(EMBED_COMPUTE_PRECISION)
    except Exception as e:
        logger.error(f"Invalid EMBED_COMPUTE_PRECISION '{EMBED_COMPUTE_PRECISION}': {e}")
        sys.exit(1)
    try:
        output_dtype = _dtype_from_string(EMBED_OUTPUT_PRECISION)
    except Exception as e:
        logger.error(f"Invalid EMBED_OUTPUT_PRECISION '{EMBED_OUTPUT_PRECISION}': {e}")
        sys.exit(1)

    model = _load_model(device, compute_dtype)
    _check_vector_dim(model)
    logger.info(f"Loaded {EMBED_MODEL} embedding model on {device}")

    try:
        counters = embed_chunks(chunks, model, device, output_dtype, selected_indices)
    except Exception as e:
        logger.error(f"Embedding failed: {e}")
        sys.exit(1)

    logger.info(
        "Embedding summary -> total: %s | embedded: %s | skipped-unselected: %s | skipped-flagged: %s",
        len(chunks), counters["embedded"], counters["skipped_unselected"], counters["skipped_flagged"],
    )

    persist_outputs(chunks, model, counters["embedded"])

    # Finally, update provenance with the run's embedding settings
    try:
        update_provenance_with_embedding()
    except SystemExit:
        raise
    except Exception as e:
        logger.error(f"Failed to update provenance: {e}")


if __name__ == "__main__":
    main()
