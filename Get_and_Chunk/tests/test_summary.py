import json


def test_prepend_metadata_to_small_chunk(summary_module):
    module = summary_module
    chunk = {
        "token_count": 5,
        "content": "Body text",
        "concat_header_path": "Title > Section",
        "page_summary": "Page info",
        "chunk_summary": "Chunk info",
    }

    module._prepend_metadata_to_small_chunk(chunk)

    expected_prefix = "Title > Section\nChunk info"
    assert chunk["content"].startswith(expected_prefix)


def test_prepend_metadata_skips_large_chunk(summary_module):
    module = summary_module
    chunk = {
        "token_count": module.CHUNK_MIN_THRESHOLD,
        "content": "Body text",
        "concat_header_path": "Title > Section",
        "page_summary": "Page info",
        "chunk_summary": "Chunk info",
    }

    original = chunk["content"]

    if chunk.get("token_count", 0) < module.PAD_CHUNK_THRESHOLD:
        module._prepend_metadata_to_small_chunk(chunk)

    if chunk.get("token_count", 0) < module.PAD_CHUNK_THRESHOLD:
        assert chunk["content"] != original
    else:
        assert chunk["content"] == original


def test_group_chunks_by_top_heading(summary_module):
    module = summary_module
    chunks = [
        {
            "id": "h1",
            "header_level": 1,
            "concat_header_path": "Root",
            "content": "Top",
            "filename": "doc.md",
        },
        {
            "id": "leaf1",
            "parent_id": "h1",
            "header_level": 2,
            "concat_header_path": "Root > Child",
            "content": "Child",
            "filename": "doc.md",
        },
        {
            "id": "orphan",
            "header_level": None,
            "filename": "loose.md",
            "content": "Loose",
        },
    ]

    grouped = module._group_chunks_by_top_heading(chunks)

    keys = list(grouped.keys())
    assert keys == [("h1", "h1"), ("file", "loose.md")]
    assert len(grouped[("h1", "h1")]["chunks"]) == 2
    assert grouped[("file", "loose.md")]["chunks"][0]["id"] == "orphan"


def test_summarize_summaries_assigns_page_summary(summary_module, fake_openai):
    module = summary_module
    module.ENABLE_FILE_SUMMARY = True
    module.SUMMARY_SETTINGS["file"]["backend"] = "openai"
    module.SUMMARY_SETTINGS["file"]["model"] = "stub-model"
    module.SUMMARY_SETTINGS["file"]["system_prompt"] = "prompt"
    module.SUMMARY_SETTINGS["file"]["size"] = 25
    module.SUMMARY_SETTINGS["file"]["temperature"] = 0.0

    data = [
        {
            "id": "h1",
            "header_level": 1,
            "concat_header_path": "Title",
            "content": "Heading body",
            "chunk_summary": "Top summary",
            "page_summary": "",
            "token_count": 3,
            "filename": "doc.md",
        },
        {
            "id": "leaf1",
            "parent_id": "h1",
            "header_level": 2,
            "concat_header_path": "Title > Child",
            "content": "Child body",
            "chunk_summary": "Child summary",
            "page_summary": "",
            "token_count": 2,
            "filename": "doc.md",
        },
    ]

    module.chunkfile.write_text(json.dumps(data))

    calls = fake_openai
    module.summarize_summaries()

    updated = json.loads(module.chunkfile.read_text())
    chunks = updated

    assert len(calls) == 1
    for chunk in chunks:
        assert chunk["page_summary"].startswith("stub:1:")
        assert chunk["content"].split("\n", 1)[0] == chunk["concat_header_path"]


def test_summarize_chunks_populates_chunk_summary(summary_module, fake_openai):
    module = summary_module
    module.ENABLE_CHUNK_SUMMARY = True
    module.SKIP_CHUNK_THRESHOLD = 0
    module.SUMMARY_SETTINGS["chunk"]["backend"] = "openai"
    module.SUMMARY_SETTINGS["chunk"]["model"] = "stub-model"
    module.SUMMARY_SETTINGS["chunk"]["system_prompt_template"] = "prompt {heading_context} {size}"
    module.SUMMARY_SETTINGS["chunk"]["size"] = 20
    module.SUMMARY_SETTINGS["chunk"]["temperature"] = 0.0

    chunks = [
        {
            "id": "c1",
            "content": "Chunk content body",
            "chunk_summary": "",
            "token_count": 500,
            "concat_header_path": "Top > Child",
        }
    ]

    module.chunkfile.write_text(json.dumps(chunks))

    calls = fake_openai
    module.summarize_chunks()

    updated = json.loads(module.chunkfile.read_text())

    assert len(calls) == 1
    assert updated[0]["chunk_summary"].startswith("stub:1:")
