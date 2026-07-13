"""Golden-corpus integration test: run the chunker end-to-end on real files
and assert the invariants downstream stages depend on."""

import json


GOLDEN_A = """---
Title: Guide A
---
# Guide A
Introductory text for guide A.

## Install
Run the installer and follow the prompts with plenty of words here.

```python
def install():
    print("installing")

def verify():
    print("verifying")
```

## Configure
Short section.
"""

GOLDEN_B = """# Guide B
Guide B intro.

## Empty Parent

### Deep Leaf
Deep content lives here.
"""


def _run_chunker(chunker_module):
    module = chunker_module
    module.MAX_TOKENS_FOR_NODE = 12  # force the code block in A to peel
    (module.CWD / "a.md").write_text(GOLDEN_A, encoding="utf-8")
    (module.CWD / "b.md").write_text(GOLDEN_B, encoding="utf-8")

    module.process_directory()

    with open(module.CHUNK_OUTPUT, "r", encoding="utf-8") as f:
        return json.load(f)


def test_chunker_end_to_end_invariants(chunker_module):
    chunks = _run_chunker(chunker_module)
    assert chunks, "chunker produced no output"

    ids = [c["id"] for c in chunks]
    assert len(ids) == len(set(ids)), "chunk ids must be unique"

    by_id = {c["id"]: c for c in chunks}

    for chunk in chunks:
        # No stringly-typed sentinels anywhere.
        assert chunk["embedding"] is None or isinstance(chunk["embedding"], list)
        assert chunk["embed"] == (chunk["token_count"] > 0)

        # Parent links resolve and stay within the same file.
        if chunk["parent_id"]:
            parent = by_id[chunk["parent_id"]]
            assert parent["filename"] == chunk["filename"]

        # prev/next stay within the same file.
        for key in ("id_prev", "id_next"):
            if chunk[key]:
                assert by_id[chunk[key]]["filename"] == chunk["filename"]

    # The oversized code block was peeled into an example chunk parented to
    # its heading, and referenced from the heading's examples list.
    examples = [c for c in chunks if c["chunk_type"] == "example"]
    assert examples, "expected at least one peeled code example"
    for example in examples:
        parent = by_id[example["parent_id"]]
        assert example["id"] in parent["examples"]

    # Empty headings are kept for structure but flagged non-embeddable.
    empty_parent = next(c for c in chunks if c["heading"] == "Empty Parent")
    assert empty_parent["embed"] is False
    deep_leaf = next(c for c in chunks if c["heading"] == "Deep Leaf")
    assert deep_leaf["parent_id"] == empty_parent["id"]
    assert deep_leaf["concat_header_path"] == "Guide B > Empty Parent > Deep Leaf"


def test_chunker_writes_provenance_and_report(chunker_module):
    _run_chunker(chunker_module)

    prov_path = chunker_module.CWD / "a_provenance.json"
    assert prov_path.exists()
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    assert prov["prov_id"].startswith("prov_")
    assert "chunk" in prov and "summary" in prov and "embed" in prov

    report = chunker_module.CWD / "chunk_token_counts_report.csv"
    assert report.exists()
