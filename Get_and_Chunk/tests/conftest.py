import importlib.util
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ensure_stub_modules() -> None:
    """Register lightweight stand-ins for optional dependencies."""
    if "tiktoken" not in sys.modules:
        # Stub tiktoken so tests run offline: the real package downloads its
        # encoding files from the network on first use.
        tiktoken = types.ModuleType("tiktoken")

        class _StubEncoding:
            def encode(self, text: str):
                return text.split() if text else []

        tiktoken.get_encoding = lambda _name: _StubEncoding()
        sys.modules["tiktoken"] = tiktoken

    if "llama_index" not in sys.modules:
        llama_index = types.ModuleType("llama_index")
        core = types.ModuleType("llama_index.core")
        node_parser = types.ModuleType("llama_index.core.node_parser")

        class _DummyDocument:
            def __init__(self, text: str = "", metadata=None):
                self.text = text
                self.metadata = metadata or {}

        class _DummySimpleDirectoryReader:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def load_data(self):
                return []

        class _DummyMarkdownNodeParser:
            def get_nodes_from_documents(self, docs):
                return []

        core.Document = _DummyDocument
        core.SimpleDirectoryReader = _DummySimpleDirectoryReader
        node_parser.MarkdownNodeParser = _DummyMarkdownNodeParser

        llama_index.core = core
        sys.modules["llama_index"] = llama_index
        sys.modules["llama_index.core"] = core
        sys.modules["llama_index.core.node_parser"] = node_parser

    if "pygments" not in sys.modules:
        pygments = types.ModuleType("pygments")
        lexers = types.ModuleType("pygments.lexers")

        class _ClassNotFound(Exception):
            pass

        def _guess_lexer(_code: str):
            raise _ClassNotFound()

        lexers.ClassNotFound = _ClassNotFound
        lexers.guess_lexer = _guess_lexer
        pygments.lexers = lexers
        sys.modules["pygments"] = pygments
        sys.modules["pygments.lexers"] = lexers

    if "openai" not in sys.modules:
        chat_module = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=lambda *args, **kwargs: None)
        )
        openai = types.SimpleNamespace(chat=chat_module)
        sys.modules["openai"] = openai


# Register stubs at import time: test modules import pipeline modules during
# collection (before any fixture runs), and the real deps would hit the network.
_ensure_stub_modules()


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_external_stubs():
    _ensure_stub_modules()


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


class DummyTokenizer:
    def encode(self, text: str):
        if not text:
            return []
        return text.split()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def chunker_module(monkeypatch, tmp_path, project_root):
    import common.run_context as run_context

    fake_cwd = tmp_path / "workspace"
    fake_cwd.mkdir()

    metadata_row = {"ID": "test", "BASE_DIR": fake_cwd.name, "SOURCE": "test"}

    def _fake_get_run_context(output_name=None):
        return {
            "cwd": fake_cwd,
            "metadata": metadata_row,
            "md_to_chunk": fake_cwd / f"{fake_cwd.name}.md",
            "output_path": (fake_cwd / output_name) if output_name else None,
        }

    monkeypatch.setattr(run_context, "get_run_context", _fake_get_run_context)

    module = _load_module("chunker_under_test", project_root / "3.00chunker.py")
    module.TOKENIZER = DummyTokenizer()
    module.MAX_TOKENS_FOR_NODE = 20
    return module


@pytest.fixture
def summary_module(monkeypatch, tmp_path, project_root):
    import common.run_context as run_context

    fake_cwd = tmp_path / "workspace"
    fake_cwd.mkdir()

    metadata_row = {"ID": "test", "BASE_DIR": fake_cwd.name, "SOURCE": "test"}

    def _fake_get_run_context(output_name=None):
        return {
            "cwd": fake_cwd,
            "metadata": metadata_row,
            "md_to_chunk": fake_cwd / f"{fake_cwd.name}.md",
            "output_path": (fake_cwd / output_name) if output_name else None,
        }

    monkeypatch.setattr(run_context, "get_run_context", _fake_get_run_context)

    module = _load_module("summary_under_test", project_root / "4.01summary.py")
    module.TOKENIZER = DummyTokenizer()
    module.CHUNK_MIN_THRESHOLD = 10
    return module


@pytest.fixture
def fake_openai():
    import summary_under_test as summary_module

    calls = []

    def _fake_run_summary_backend(_backend, text, _params, **_kwargs):
        calls.append(
            {
                "backend": _backend,
                "text": text,
            }
        )
        return f"stub:{len(calls)}:{str(text)[:20]}"

    summary_module.run_summary_backend = _fake_run_summary_backend
    return calls


@pytest.fixture
def markdown_cleanup_module(monkeypatch, tmp_path, project_root):
    import common.run_context as run_context

    fake_cwd = tmp_path / "workspace"
    fake_cwd.mkdir()

    metadata_row = {"ID": "test", "BASE_DIR": fake_cwd.name, "SOURCE": "test"}

    def _fake_get_run_context(output_name=None):
        return {
            "cwd": fake_cwd,
            "metadata": metadata_row,
            "md_to_chunk": fake_cwd / f"{fake_cwd.name}.md",
            "output_path": (fake_cwd / output_name) if output_name else None,
        }

    monkeypatch.setattr(run_context, "get_run_context", _fake_get_run_context)

    module = _load_module("markdown_cleanup_under_test", project_root / "2markdown_cleanup.py")
    return module