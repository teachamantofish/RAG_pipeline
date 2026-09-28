---
id: C:/GIT/topomorph/apps/rag-master/MkDocs/docs/vector.md
type: reference
title: vector
name: vector
description: pip install pgvector
resource: /topomorph/apps/rag-master/MkDocs/docs/vector.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, mkdocs, docs]
domain: tools
tokens: 682
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
# Vector upsert





pip install pgvector

Step 0: Enable the pgvector extension (run this first in pgAdmin → Query Tool).
CREATE EXTENSION IF NOT EXISTS vector;
Step 1: provenance table (must exist before chunks because of the FK)
Step 2 — Create the chunks table (run this next in pgAdmin → Query Tool).
CREATE TABLE IF NOT EXISTS public.chunks (
  id TEXT PRIMARY KEY,
  filename TEXT,
  etc...


## How to upsert

Load the data into the vector db by upserting the the following in different tables: 

- a_chunk.json: Contains the chunks and metadata
- a_embeddings.parquet: Contains only embeddings with matching ids that map to the chunks
- a_provenance.json: Provenance data stored in a separate table. An ID maps the file to upserted data


1. Start the postgres server if not already running: find the postgresql-x64-XX service and start it.
#. Start PGAdmin. 
#. Log in.
#. Check if the tables exist: 

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('provenance','chunks','chunk_embeddings');
```

#. If they don't exist,  create them: 

```sql
CREATE TABLE public.chunks (
  id TEXT PRIMARY KEY,
  filename TEXT,
  parent_id TEXT,
  id_prev TEXT,
  id_next TEXT,
  heading TEXT,
  header_level INT,
  concat_header_path TEXT,
  content TEXT,
  examples JSONB,
  chunk_summary TEXT,
  page_summary TEXT,
  title TEXT,
  author TEXT,
  category TEXT,
  description TEXT,
  language TEXT,
  token_count INT,
  prov_id TEXT REFERENCES public.provenance(prov_id)
);

CREATE TABLE public.chunk_embeddings (
  id TEXT PRIMARY KEY REFERENCES public.chunks(id),
  embedding vector(2560)
);
```

#. Verify the connection details are correct. Run ``SELECT current_database(), current_user, inet_server_addr(), inet_server_port();``
#. Update vectorconfig.py as needed. Note that ::1 is the same as ``127.0.0.1``.
#. Run the upsert script. 
#. Verify success: 

```sql
SELECT
    c.id,
    c.heading,
    pe.chunk_model,
    (embedding::float4[])[1:5] AS embed_preview
FROM chunks c
JOIN provenance pe ON c.prov_id = pe.prov_id
JOIN chunk_embeddings e ON c.id = e.id
LIMIT 3;
```

  That check produces an embedding preview by pulling and joining rows from all three tables:

    - hunks c supplies id and heading.
    - provenance pe joins on c.prov_id = pe.prov_id to grab metadata like chunk_model.
    - chunk_embeddings e joins on c.id = e.id, and the (embedding::float4[])[1:5] slice comes from that table.

    Because each row in the result set only appears if it exists in all three tables, that single vector preview confirms the relationships and data in chunks, provenance, and chunk_embeddings. To double-check counts, you can still run the individual SELECT COUNT(*) ... queries, but the join itself already exercised every table.