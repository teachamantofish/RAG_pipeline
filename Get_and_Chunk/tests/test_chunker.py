import pytest


@pytest.fixture
def chunker(chunker_module):
    module = chunker_module
    module.ENABLE_CODE_EXTRACTION = True
    module.MAX_TOKENS_FOR_NODE = 10
    return module


def test_build_candidates_heading_tree(chunker):
    files = [("doc.md", "# Title\nBody text\n")]

    candidates, front_matter = chunker.build_candidates_from_files(files)

    assert list(front_matter.keys()) == ["doc.md"]
    assert len(candidates) == 1
    chunk = candidates[0]
    assert chunk.heading == "Title"
    assert chunk.header_level == 1
    assert chunk.concat_header_path == "Title"
    assert chunk.parent_id is None
    assert chunk.content == "Body text"
    assert chunk.token_count == len(chunker.TOKENIZER.encode("Body text"))


def test_build_candidates_nested_headings(chunker):
    files = [(
        "doc.md",
        "# Top\nIntro\n## Child\nChild body\n\nParagraph still under child\n",
    )]

    candidates, _ = chunker.build_candidates_from_files(files)

    assert [c.heading for c in candidates] == ["Top", "Child"]
    top, child = candidates

    assert top.parent_id is None
    assert top.concat_header_path == "Top"
    assert top.content == "Intro"

    assert child.parent_id == top.id
    assert child.concat_header_path == "Top > Child"
    assert "Paragraph still under child" in child.content


def test_build_candidates_parses_front_matter_and_empty_headings(chunker):
    files = [(
        "doc.md",
        "---\nTitle: My Doc\n---\n# Root\n## Empty Parent\n### Leaf\nLeaf body\n",
    )]

    candidates, front_matter = chunker.build_candidates_from_files(files)

    assert front_matter["doc.md"].get("Title") == "My Doc"
    empty_parent = next(c for c in candidates if c.heading == "Empty Parent")
    assert empty_parent.content == ""
    assert empty_parent.token_count == 0
    leaf = next(c for c in candidates if c.heading == "Leaf")
    assert leaf.parent_id == empty_parent.id


def test_enforce_chunk_size_peels_code_block(chunker):
    chunker.MAX_TOKENS_FOR_NODE = 5
    body = "Intro text " * 2
    code = "```python\nprint('hello')\nprint('again')\n```"
    chunk = chunker.LeafChunk(
        id="h1",
        filename="doc.md",
        parent_id=None,
        heading="Title",
        header_level=1,
        concat_header_path="Title",
        content=f"{body}\n{code}\n",
        token_count=0,
    )
    chunk.token_count = chunker._tok(chunk.content)

    final_chunks = chunker.enforce_chunk_size([chunk])

    assert len(final_chunks) == 2
    heading = final_chunks[0]
    example = final_chunks[1]

    assert heading.chunk_type == "heading"
    assert example.chunk_type == "example"
    assert example.language == "python"
    assert example.content.startswith("```python")
    assert example.id in heading.examples
    # Peeled components are children of the heading they were peeled from.
    assert example.parent_id == heading.id
    assert heading.token_count <= chunker.MAX_TOKENS_FOR_NODE


def test_enforce_chunk_size_peels_table_without_code(chunker):
    chunker.MAX_TOKENS_FOR_NODE = 5
    table = "\n".join([
        "| a | b |",
        "|---|---|",
        "| cell1 | cell2 |",
        "| cell3 | cell4 |",
    ])
    chunk = chunker.LeafChunk(
        id="h2",
        filename="doc.md",
        parent_id=None,
        heading="Has Table",
        header_level=2,
        concat_header_path="Top > Has Table",
        content=f"intro sentence before table.\n\n{table}\n",
        token_count=0,
    )
    chunk.token_count = chunker._tok(chunk.content)

    final_chunks = chunker.enforce_chunk_size([chunk])

    assert len(final_chunks) == 2
    heading = final_chunks[0]
    table_chunk = final_chunks[1]

    assert table_chunk.chunk_type == "table"
    assert table_chunk.content.startswith("Table: Has Table")
    assert "| a | b |" in table_chunk.content
    assert heading.token_count <= chunker.MAX_TOKENS_FOR_NODE


def test_link_prev_next_assigns_neighbors(chunker_module):
    chunks = [
        chunker_module.LeafChunk(id="a", filename="f", parent_id=None),
        chunker_module.LeafChunk(id="b", filename="f", parent_id=None),
        chunker_module.LeafChunk(id="c", filename="f", parent_id=None),
    ]

    chunker_module.link_prev_next(chunks)

    assert chunks[0].id_prev is None and chunks[0].id_next == "b"
    assert chunks[1].id_prev == "a" and chunks[1].id_next == "c"
    assert chunks[2].id_prev == "b" and chunks[2].id_next is None


def test_link_prev_next_does_not_cross_files(chunker_module):
    chunks = [
        chunker_module.LeafChunk(id="a1", filename="a.md", parent_id=None),
        chunker_module.LeafChunk(id="a2", filename="a.md", parent_id=None),
        chunker_module.LeafChunk(id="b1", filename="b.md", parent_id=None),
    ]

    chunker_module.link_prev_next(chunks)

    # Last chunk of a.md must not point into b.md, and vice versa.
    assert chunks[1].id_next is None
    assert chunks[2].id_prev is None and chunks[2].id_next is None


def test_chunks_to_dicts_round_trip(chunker_module):
    chunk = chunker_module.LeafChunk(
        id="n1",
        filename="doc.md",
        parent_id="p0",
        id_prev="n0",
        id_next="n2",
        heading="Heading",
        header_level=2,
        concat_header_path="Top > Heading",
        content="Body",
        examples=["ex1"],
        chunk_summary="sum",
        page_summary="page",
        language="en",
        token_count=3,
        embedding=[0.1, 0.2],
    )

    out = chunker_module.chunks_to_dicts([chunk])

    assert out == [
        {
            "id": "n1",
            "filename": "doc.md",
            "parent_id": "p0",
            "id_prev": "n0",
            "id_next": "n2",
            "heading": "Heading",
            "header_level": 2,
            "concat_header_path": "Top > Heading",
            "chunk_type": "heading",
            "content": "Body",
            "examples": ["ex1"],
            "chunk_summary": "sum",
            "page_summary": "page",
            "language": "en",
            "token_count": 3,
            "embed": True,
            "embedding": [0.1, 0.2],
        }
    ]


def test_chunks_to_dicts_flags_empty_chunks_non_embeddable(chunker_module):
    chunk = chunker_module.LeafChunk(
        id="n1", filename="doc.md", parent_id=None, heading="Empty", token_count=0
    )

    out = chunker_module.chunks_to_dicts([chunk])

    assert out[0]["embed"] is False
    assert out[0]["embedding"] is None  # list|None, never the string "false"
