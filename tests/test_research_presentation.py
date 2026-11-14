from services.research_presentation import compact_research, requested_sources
from memory import local_store


def test_links_hidden_and_retrievable_only_by_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    raw = "Useful discovery. [S1]\n\nSources:\n• Publisher: https://example.org/very-long-url"
    text = compact_research("alice", raw)
    assert "https" not in text
    assert "[S1]" not in text
    assert "Useful discovery" in text
    assert "https://example.org/very-long-url" in requested_sources(
        "alice", "show me the sources"
    )
    assert "https" not in requested_sources("bob", "sources?")
    assert requested_sources("alice", "I like open source software") is None


def test_markdown_sources_and_multiple_bubbles(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    first = "New release. [Release notes](https://example.org/release)"
    second = "Another release. [Publisher](https://example.net/news)"
    compact_research("alice", first + "\n\n" + second)
    assert "https" not in compact_research("alice", first, remember=False)
    sources = requested_sources("alice", "sources")
    assert "example.org" in sources and "example.net" in sources
