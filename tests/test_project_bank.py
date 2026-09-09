from pathlib import Path

from src.common.project_bank import (
    delete_project,
    get_project,
    list_stacks,
    load_project_bank,
    save_project,
    suggest_projects,
)

SAMPLE_BANK = [
    {"id": "a", "stack": "wordpress", "stack_label": "WordPress", "tools": ["WordPress", "PHP"], "title": "A"},
    {"id": "b", "stack": "wordpress", "stack_label": "WordPress", "tools": ["WordPress", "WooCommerce", "PHP"], "title": "B"},
    {"id": "c", "stack": "react", "stack_label": "React", "tools": ["React", "TypeScript"], "title": "C"},
    {"id": "d", "stack": "django", "stack_label": "Django", "tools": ["Python", "Django"], "title": "D"},
]


def test_real_project_bank_json_loads_and_has_expected_shape():
    bank = load_project_bank()
    assert len(bank) >= 200
    for record in bank[:5]:
        assert record["id"]
        assert record["tools"]
        assert record["title"]
        assert record["description"]
        assert record["features"]


def test_real_project_bank_has_at_least_15_per_stack():
    bank = load_project_bank()
    counts: dict[str, int] = {}
    for r in bank:
        counts[r["stack"]] = counts.get(r["stack"], 0) + 1
    assert len(counts) >= 15  # at least 15 distinct stacks
    for stack, count in counts.items():
        assert count >= 15, f"stack {stack!r} only has {count} projects"


def test_real_project_bank_ids_and_titles_are_unique():
    bank = load_project_bank()
    ids = [r["id"] for r in bank]
    titles = [r["title"] for r in bank]
    assert len(ids) == len(set(ids))
    assert len(titles) == len(set(titles))


def test_list_stacks_groups_and_sorts():
    groups = list_stacks(bank=SAMPLE_BANK)
    labels = [g["stack_label"] for g in groups]
    assert labels == sorted(labels)
    wp_group = next(g for g in groups if g["stack"] == "wordpress")
    assert len(wp_group["projects"]) == 2
    assert [p["title"] for p in wp_group["projects"]] == ["A", "B"]  # sorted by title


def test_save_project_local_appends_and_generates_id(tmp_path: Path, monkeypatch):
    bank_file = tmp_path / "bank.json"
    bank_file.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("STORAGE_BACKEND", "local")

    record = save_project(
        {
            "stack": "custom", "stack_label": "Custom", "category": "general",
            "tools": ["Foo"], "title": "My Test Project",
            "description": "A test.", "features": ["Does a thing"],
            "estimated_effort": "1 week",
        },
        path=bank_file,
    )

    assert record["id"].startswith("custom-my-test-project-")
    saved = load_project_bank(path=bank_file)
    assert len(saved) == 1
    assert saved[0]["title"] == "My Test Project"


def test_save_project_local_upserts_by_id(tmp_path: Path, monkeypatch):
    bank_file = tmp_path / "bank.json"
    bank_file.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("STORAGE_BACKEND", "local")

    save_project(
        {"id": "fixed-id", "stack": "x", "stack_label": "X", "tools": [], "title": "V1", "description": "d", "features": ["f"]},
        path=bank_file,
    )
    save_project(
        {"id": "fixed-id", "stack": "x", "stack_label": "X", "tools": [], "title": "V2", "description": "d", "features": ["f"]},
        path=bank_file,
    )

    saved = load_project_bank(path=bank_file)
    assert len(saved) == 1
    assert saved[0]["title"] == "V2"


def test_suggest_projects_ranks_by_overlap():
    results = suggest_projects(["WordPress", "WooCommerce", "PHP"], bank=SAMPLE_BANK, seed=1)
    assert results[0]["id"] == "b"  # 3-way overlap beats "a"'s 2-way


def test_suggest_projects_respects_count():
    results = suggest_projects(["WordPress", "PHP", "React", "Python"], count=2, bank=SAMPLE_BANK, seed=1)
    assert len(results) == 2


def test_suggest_projects_no_match_returns_empty():
    assert suggest_projects(["Rust", "Go"], bank=SAMPLE_BANK) == []


def test_suggest_projects_empty_skills_returns_empty():
    assert suggest_projects([], bank=SAMPLE_BANK) == []


def test_suggest_projects_is_case_insensitive():
    results = suggest_projects(["wordpress", "php"], bank=SAMPLE_BANK, seed=1)
    assert any(r["id"] in ("a", "b") for r in results)


def test_suggest_projects_real_bank_matches_common_stack():
    results = suggest_projects(["React", "Node.js"], count=3, seed=1)
    assert len(results) > 0
    assert all("tools" in r for r in results)


def test_get_project_found_and_not_found():
    assert get_project("a", bank=SAMPLE_BANK)["title"] == "A"
    assert get_project("does-not-exist", bank=SAMPLE_BANK) is None


def test_delete_project_removes_and_returns_true(tmp_path: Path, monkeypatch):
    bank_file = tmp_path / "bank.json"
    bank_file.write_text('[{"id": "x", "title": "X"}, {"id": "y", "title": "Y"}]', encoding="utf-8")
    monkeypatch.setenv("STORAGE_BACKEND", "local")

    result = delete_project("x", path=bank_file)

    assert result is True
    remaining = load_project_bank(path=bank_file)
    assert len(remaining) == 1
    assert remaining[0]["id"] == "y"


def test_delete_project_unknown_id_returns_false(tmp_path: Path, monkeypatch):
    bank_file = tmp_path / "bank.json"
    bank_file.write_text('[{"id": "x", "title": "X"}]', encoding="utf-8")
    monkeypatch.setenv("STORAGE_BACKEND", "local")

    assert delete_project("does-not-exist", path=bank_file) is False
    assert len(load_project_bank(path=bank_file)) == 1


def test_edit_then_save_project_preserves_id_and_overwrites_fields(tmp_path: Path, monkeypatch):
    bank_file = tmp_path / "bank.json"
    bank_file.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("STORAGE_BACKEND", "local")

    save_project({"id": "p1", "stack": "x", "stack_label": "X", "tools": ["A"], "title": "Old", "description": "d", "features": ["f"]}, path=bank_file)
    save_project({"id": "p1", "stack": "x", "stack_label": "X", "tools": ["B"], "title": "New", "description": "d2", "features": ["f2"]}, path=bank_file)

    bank = load_project_bank(path=bank_file)
    assert len(bank) == 1
    assert bank[0]["title"] == "New"
    assert bank[0]["tools"] == ["B"]
