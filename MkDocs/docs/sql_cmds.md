---
id: C:/GIT/topomorph/apps/rag-master/MkDocs/docs/sql_cmds.md
type: reference
title: sql_cmds
name: sql_cmds
description: 'I don''t know SQL or db mgmt., so I''m storing examples here:'
resource: /topomorph/apps/rag-master/MkDocs/docs/sql_cmds.md
resource_root: C:/GIT
author: brogers
tags: [topomorph, apps, rag-master, mkdocs, docs, "postgresql", "pgvector"]
domain: tools
tokens: 408
generated: {by: 'human:brogers', at: '2026-09-10T14:43:17-04:00'}
okf_version: '0.2'
---
# List of useful queries

I don't know SQL or db mgmt., so I'm storing examples here: 

## Find specific data by string
```sql
SELECT * FROM chunks WHERE content LIKE '%mkdocs%';
```
## Create a database
```sql
CREATE DATABASE "BensDocServer";
```
## Grant access
```sql
GRANT ALL PRIVILEGES ON TABLE public.chunks TO postgres;
```

## Create schema/columns
```sql
CREATE TABLE chunks (
    id TEXT PRIMARY KEY,
    filename TEXT,
    heading TEXT,
    header_level INTEGER,
    concat_header_path TEXT,
    content TEXT,
    parent_id TEXT,
    examples TEXT,
    token_count INTEGER,
    chunk_summary TEXT,
    page_summary TEXT,
    id_prev TEXT,
    id_next TEXT,
    domain TEXT,
    custom_title TEXT,
    source TEXT,
    date TEXT,
    embedding vector(1536),
    ranking REAL DEFAULT NULL,
    type TEXT,
    language TEXT
);
```

## List All Tables in Public Schema
```sql
SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema = 'public';
```

## List All Tables Named "chunks"
```sql
SELECT table_schema, table_name FROM information_schema.tables WHERE table_name = 'chunks';
```

## Show Table Structure (Columns and Types)
```sql
SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'chunks';
```

## View All Rows in Chunks Table
```sql
SELECT * FROM chunks;
```

## View First 10 Rows in Chunks Table
```sql
SELECT * FROM chunks LIMIT 10;
```

## Count Rows in Chunks Table
```sql
SELECT COUNT(*) FROM chunks;
```

## Delete a Row by ID
```sql
DELETE FROM chunks WHERE id = 'the_accidental_id';
```

## Filter Data by String in a Column
```sql
SELECT * FROM chunks WHERE content LIKE '%xxx%';
```




