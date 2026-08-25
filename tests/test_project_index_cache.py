from utils import project_indexer


def test_project_index_cache_is_owner_scoped_and_invalidates_on_change(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.py"
    source.write_text("print('one')\n", encoding="utf-8")

    first = project_indexer.index_project_dir(project, owner_id="u1")
    second = project_indexer.index_project_dir(project, owner_id="u1")
    project_indexer.index_project_dir(project, owner_id="u2")
    stats = project_indexer._PROJECT_INDEX_CACHE.stats()

    assert first == second
    assert stats["hits"] == 1
    assert stats["misses"] == 2

    source.write_text("print('changed and longer')\n", encoding="utf-8")
    changed = project_indexer.index_project_dir(project, owner_id="u1")
    assert changed != first
    assert project_indexer._PROJECT_INDEX_CACHE.stats()["misses"] == 3


def test_project_index_never_caches_or_previews_known_secret_files(tmp_path):
    (tmp_path / ".env").write_text("API_TOKEN=secret", encoding="utf-8")
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    result = project_indexer.index_project_dir(tmp_path, owner_id="owner")
    names = [item["name"] for files in result.values() for item in files]
    assert ".env" not in names
    assert "README.md" in names
