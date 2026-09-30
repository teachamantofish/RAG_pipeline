---
id: C:/GIT/topomorph/apps/rag-master/MkDocs/docs/embedding.md
type: reference
title: embedding
name: embedding
description: 'models:'
resource: /topomorph/apps/rag-master/MkDocs/docs/embedding.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, mkdocs, docs, "embeddings", "pgvector", "parquet", "performance-analysis"]
domain: tools
tokens: 3768
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
# Embeddings


## Considerations

models: 

best embedding model with high accuracy for SDK docs

Mistral-Embed	77.8%	Not specified	Best overall accuracy, ideal for precise retrieval	Moderate cost
Nomic Embed Code	81.7	2048 tokens	Supports multiple languages, trained on CoRNStack dataset, open-source	Available on HuggingFace https://www.nomic.ai/blog/posts/introducing-state-of-the-art-nomic-embed-code
text-embedding-3-large is OpenAI’s latest embedding model, showing strong performance across both text and code tasks.
VoyageCode3 large is specifically designed for code understanding tasks.: https://blog.voyageai.com/2024/12/04/voyage-code-3/ 200M tokens free
qwen embedding 8B


Hugging Face MTEB leaderboard — you can see model rankings, scores across embedding tasks. 
Hugging Face
https://huggingface.co/spaces/mteb/leaderboard

Benchmark papers / GitHub repos of embedding models (often they publish evaluation tables on code / retrieval tasks)

Milvus embedding API benchmarking — for example, they ran comparisons across embedding APIs measuring recall, latency, etc. 
Milvus

“Making benchmark of different embedding models” (AutoRAG medium article) — shows practical benchmarking setup for embeddings. 
Medium

Community / blog comparisons — e.g. “6 Best Code Embedding Models Compared” or “Best Open-Source Embedding Models Benchmarked” posts often include metric tables. 
Modal
+1

PapersWithCode — for code retrieval tasks (e.g. CodeSearchNet) you can find metrics and model rankings. 
Papers with Code


Variant	Formula	Size per vector	Relative to float-512
float-512	512 × 4 bytes	2,048 bytes (≈ 2 KB)	1×
int8-512	512 × 1 byte	512 bytes (0.5 KB)	0.25×
int8-1024	1024 × 1 byte	1,024 bytes (1 KB)	0.5×
int8-2048	2048 × 1 byte	2,048 bytes (2 KB)	1×


⚙️ Quick background: how pgvector handles embeddings

Each embedding is stored as a fixed-length vector of float4 (32-bit float) values in a vector column type.

As of pgvector ≥ 0.5.0, you can also store 8-bit quantized vectors using vector(…) + IVFFlat / HNSW indexes with optional compression.

Performance depends roughly linearly on:

vector dimension

element precision (float32 vs int8)

index type (IVFFlat or HNSW)

number of probes / ef_search, etc.

📊 VoyageCode3 variants compared (for pgvector)
Variant	Dim	Type	Storage per vector	Storage per 10 M	Relative RAM footprint	Search latency (approx)	Comments
float-512	512	float32	2 KB	20 GB	1.0×	1.0× baseline	Standard; full precision, accurate but slower
int8-512	512	8-bit quantized	0.5 KB	5 GB	0.25×	~0.6×	4× smaller I/O; usually 1.5–2× faster search in pgvector
int8-1024	1024	8-bit quantized	1 KB	10 GB	0.5×	~0.8×	Slightly slower due to 2× more dimensions
int8-2048	2048	8-bit quantized	2 KB	20 GB	1.0×	~1.0×


⚡ Rough latency behavior (IVFFlat index, CPU-only)

Let’s say you have:

10 M vectors, indexed with IVFFlat (1000 lists, 10 probes)

Queries of 512–2048 dims, cosine distance

A modern 8-core CPU (e.g. Ryzen 9 8845HS)

Approximate average per-query latency (single probe):

Variant	Latency (ms/query)
float-512	~28–35 ms
int8-512	~15–20 ms
int8-1024	~22–28 ms
int8-2048	~30–35 ms

So you can expect:

~40–50 % faster searches with int8-512

half the RAM, ¼ the disk, same accuracy

little to no quality loss if the model provides dedicated quantized embeddings (as VoyageCode does)

💡 Recommendation (for pgvector)

✅ Use int8-512 unless you empirically measure a loss in recall/precision for your API/code retrieval tasks.
It’s the best trade-off for pgvector.

⚙️ Index: USING ivfflat (embedding vector_cosine_ops) WITH (lists = 1000)
Then tune probes:

SET ivfflat.probes = 10;  -- higher = more accurate, slower


💾 Expect 4× lower storage, ~2× faster query times, similar recall.

XXXXXXXXXXXXXXXXXXXXXX >>>>>>>>>>>>>>> Would you like me to give you the exact pgvector table and index creation DDL for storing and querying those int8-512 VoyageCode3 embeddings efficiently (including cosine distance and metadata)?


## Choosing and enforcing embedding precision

Great call—I've wired your precision settings into config and the embedding script so you can control both compute precision (speed/memory) and output precision (what you store), and I added clear logging.

What I changed
Config enhancements in embedconfig.py:

EMBED_COMPUTE_PRECISION: controls model math precision on GPU.
Allowed: "float32", "float16", "bfloat16", "tf32"
"tf32" keeps tensors as float32 but enables TF32 matmul for speed on supported GPUs.
EMBED_OUTPUT_PRECISION: controls the dtype you save (cast before serialization).
Allowed: "float32", "float16", "bfloat16"
ENABLE_TF32: toggles TF32 acceleration for float32/tf32 paths.
Left the old VECTOR_SIZE for compatibility, but marked it deprecated.
Script updates in VectorDB/embedding.py:

Enforce CUDA-only still.
Map your precision strings to torch dtypes; validate and log errors if invalid.
Enable TF32 when requested and supported.
Move the SentenceTransformer model to the configured compute dtype.
Encode to a torch tensor on CUDA, cast to EMBED_OUTPUT_PRECISION, then serialize to JSON.
Log the model-reported embedding dimension: “Embedding dimension reported by model: …”
Log when TF32 is enabled: “TF32 acceleration enabled for float32 compute paths”
Log which compute precision is applied: “Embedding compute precision set to …”
How it behaves
Faster compute:

Set EMBED_COMPUTE_PRECISION = "float16" (default I set for you) or "bfloat16".
For float32 speed-ups without changing dtype, use "tf32" with ENABLE_TF32 = True.
Stable output:

EMBED_OUTPUT_PRECISION controls what we serialize.
Even though JSON stores numbers as plain floats, values are cast to that dtype first (useful when you later switch to binary formats like .npy/.npz).
Logging:

You’ll see the chosen precision and the embedding dimension in the logs right after model load.

Notes and recommendations
If you want smaller files and exact dtype preservation, consider switching to a binary format for embeddings (e.g., .npy/.npz or Parquet). Then EMBED_OUTPUT_PRECISION will directly control stored dtype and you’ll get much better size and load speed.
Your DB schema (pgvector dimension) must match the model dimension. The Qwen3-Embedding-4B model commonly uses 2560 dimensions. If your table is VECTOR(1536), update it:
ALTER TABLE public.chunks ALTER COLUMN embedding TYPE vector(2560);

## If you need smaller size

Short version: your current model’s embedding dimension isn’t configurable. Most local/HF/SentenceTransformers models have a fixed output size baked into the model. You can’t change it via a flag at encode-time.

What’s generally true vs exceptions:

Typical local models (HF + SentenceTransformers): fixed dim. To change it you’d:
Apply a post-step like PCA/TruncatedSVD to reduce to a target dim (e.g., 1024).
Add a projection layer (dense head) and fine-tune or export a new model.
Some hosted APIs are exceptions: a few providers let you request a smaller output dimension server-side (they apply a projection before returning the vector). That’s model/provider-specific, not universal.
Different models often come in different fixed sizes (e.g., 768, 1024, 1536, 2560, 4096), so picking a model with your target dim is another option.
Your options if you need 1024:

Keep your current model and add a PCA/TSVD step to 1024 before saving/ingesting (recommended for easy drop-in).
Fine-tune a projection head to 1024 (more work, can preserve task-specific quality).
Switch to a model that natively outputs 1024.
Regardless of pathway, pgvector expects a fixed dimension; make sure your table and ingestion match whatever dimension you decide to use.

Yes. Since you trained the model, you can add a learned projection head (a small Dense layer) that maps the model’s native embedding to your target dimension (e.g., 1024), and train/fine‑tune it with your usual contrastive/ranking loss. After that, inference will directly output 1024‑dim vectors—no PCA step required.

Here’s the practical pattern with SentenceTransformers

Architecture

Transformer backbone (frozen or trainable)
Pooling (e.g., mean pooling + L2 normalize)
Dense projection to 1024 (linear layer, typically without activation, bias=False)
Optional Normalize module after the Dense to re‑normalize embeddings
Training approach

Fast path: freeze the backbone; train only the Dense projection (much quicker and stable).
Full path: fine‑tune both backbone and Dense if you have enough data/compute.
Loss: MultipleNegativesRankingLoss or CosineSimilarityLoss (whatever you used originally for embeddings).

Example (SentenceTransformers)

```python
from sentence_transformers import SentenceTransformer, models, losses, InputExample
from torch.utils.data import DataLoader

base = "Qwen/Qwen3-Embedding-4B"  # your pretrained embedding model

# 1) Modules: Transformer -> Pooling -> Dense(-> Normalize)
word = models.Transformer(base)  # device='cuda' is handled by SentenceTransformer at load/train
pool = models.Pooling(
    word.get_word_embedding_dimension(),
    pooling_mode_mean_tokens=True,
    pooling_mode_cls_token=False,
    pooling_mode_max_tokens=False,
)
dense = models.Dense(
    in_features=pool.get_sentence_embedding_dimension(),
    out_features=1024,  # target dimension
    bias=False,         # linear projection tends to work best
    activation_function=None
)
norm = models.Normalize()  # keep cosine geometry stable after projection

model = SentenceTransformer(modules=[word, pool, dense, norm])

# 2) (Optional) Freeze backbone, train only projection+norm
for p in word.auto_model.parameters():
    p.requires_grad = False

# 3) Data + loss
train_examples = [
    # Fill with your pairs; for MNRLoss, (anchor, positive)
    InputExample(texts=["query text", "relevant passage"]),
    # ...
]
train_loader = DataLoader(train_examples, shuffle=True, batch_size=64, drop_last=True)
train_loss = losses.MultipleNegativesRankingLoss(model)

# 4) Train (fp16 mixed precision recommended)
model.fit(
    train_objectives=[(train_loader, train_loss)],
    epochs=1,                            # tune for your data
    warmup_steps=int(0.1 * len(train_loader)),
    optimizer_params={'lr': 5e-4},       # slightly higher lr if only head is trainable
    use_amp=True,                        # mixed precision for speed
    output_path="qwen-embed-1024-head",  # export path
)

# 5) Inference uses the new dimension
deployed = SentenceTransformer("qwen-embed-1024-head", device="cuda")
print("dim:", deployed.get_sentence_embedding_dimension())  # -> 1024
```

## Use Parquet

Storing the data in a binary file format (like Parquet, Feather, NPZ) just changes how it’s serialized on disk and is lossless for retrieval quality as long as you keep the same numeric dtype.
So yes: using Parquet to store embeddings is a storage choice and won’t degrade retrieval quality, provided you:

Keep the dtype consistent (e.g., float32 end to end for pgvector).
Don’t accidentally upcast to float64 on load.
Don’t apply lossy quantization.
Practical tips for Parquet

Use a fixed-size list of float32 per row with the embedding dimension:
Arrow type: fixed_size_list(float32, dim) or list_(float32) with validation of length.
Compression (snappy/zstd) is lossless and safe.
On load, ensure you read them as float32 (Arrow/Polars preserves dtype; pandas may upcast to float64 unless you specify/convert).
For pgvector: it stores float32 internally. So compute in fp16 for speed if you like, cast to float32 before writing Parquet and before inserting into Postgres.
Minimal mapping for your pipeline

Keep metadata in JSON (or a lean JSON).
Store embeddings in a Parquet sidecar keyed by chunk id with a float32 fixed-size list column.
In VectorDB/upsert_to_vectorydb.py, load the Parquet, join on id, and insert float32 vectors into pgvector.
This gives:

Much smaller on-disk size vs JSON.
Much faster I/O.
No loss in retrieval quality in Postgres, because the values are identical float32s to what you’d have stored from JSON.

### How it works end-to-end

During embedding:

You compute embeddings on CUDA (with your chosen compute precision).
If USE_PARQUET is True, embeddings are written to a_embeddings.parquet (float32), and the JSON metadata drops the heavy vectors by setting them to None.
During ingestion:

VectorDB/upsert_to_vectorydb.py detects the sidecar and merges vectors back via id, so pgvector inserts get the float32 arrays they expect.
Retrieval quality:
Identical to storing embeddings in JSON; we don’t quantize or binarize, we just store float32 in a better container.

Notes
Dependencies: this path requires pyarrow. If it’s missing, you’ll see a clear install prompt in the logs.
Schema: fixed-size list ensures consistent vector length; ingestion converts to Python lists for pgvector’s psycopg2 adapter.
Storage: still float32 so pgvector accepts it. You can compute in fp16 on GPU (fast) and upcast to float32 at save/insert time.


## Creating embeddings

After running the RAG pipeline so that the chunks are in order and training the embedding model create the embeddings: 

1. Set the base model: The base model is pulled from Hugging Face by ID in `VectorDB/embedding.py` via `SentenceTransformer(EMBED_MODEL, device=device)` and downloads/loads from the HF cache (`~/.cache/huggingface` unless `TRANSFORMERS_CACHE` is set).
#. **Check whether a reusable .venv Python environment with SentenceTransformers already exists.**
#. Activate the environment: ``<path>.venv\Scripts\Activate.ps1``
#. Install dependencies as needed; for example: torch, numpy, sentence-transformers, and (when USE_PARQUET=True) pyarrow in addition to local modules.
#. Ensure the fine-tuned adapter in qwen3-embedding-4b-finetuned-epoch3 is accessible from the machine running embeddings.
#. Run the embedding writer script to batch-encode each chunk’s content or summary and normalize the vectors.
#. Persist the resulting 2,560-dim arrays back into mifref/a_chunks.json under the `embedding` field, then spot-check cosine sims for a few queries.
#. Run in test mode: 
    - MAX_EMBED_CHUNKS = 20  # For test runs
    - CHUNK_SAMPLE_MODE = "head" # faster
#. Verify the postembed.json is the same size as the original json. The data should not have changed. 
#. Verify the existence of the .parquet file.
#. Set MAX_EMBED_CHUNKS = None for the full run. 
#. Run the script. 
#. Verification: The log and provenance file shows if the adapter is applied. If in doubt, compare a test run with no adapter to a run with an adapter. 

## Keeping embeddings separate from chunks

While both the chunk and embedding data wind up in the same Db table, keeping them separate during creation provides the following benefits: 

1) Re-embedding without touching text

You can regenerate embeddings (new model, better model, different dimensions) without rewriting chunk data. Common in RAG pipelines.

2) Multiple embedding models

You may want:

- BGE-large for retrieval
- Qwen for code
- OpenAI for cross-encoder rerank
- Separate storage avoids duplicating chunk text.

3) Storage efficiency

Parquet compresses dense float vectors very well. Postgres JSONB + vector is larger and slower for bulk ingest.

4) Pipeline modularity

Chunking and embedding are independent steps. Failures in embedding don’t corrupt chunk corpus.

5) Batch ML tooling compatibility

PyTorch / HF / NumPy / DuckDB pipelines prefer Parquet. Easier to move embeddings between machines or GPUs. Why you still merge into Postgres later

Parquet = offline artifact
Postgres + pgvector = online retrieval system

They serve different roles.