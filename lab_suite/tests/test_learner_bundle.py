import ast

from lab_suite.publish import remove_authoring_keys


def test_learner_bundle_keeps_probe_and_removes_teacher_solution(tmp_path):
    target = tmp_path / "lab_suite" / "cases"
    target.mkdir(parents=True)
    path = target / "advanced_example.py"
    path.write_text(
        "def replacements(case):\n    return [('app.py', 'answer', 'fault')]\n\n"
        "def probe(case, urls, evidence):\n    return {'healthy': True}\n",
        encoding="utf-8",
    )
    ordinary = target / "other.py"
    ordinary.write_text("VALUE = 1\n", encoding="utf-8")
    removed = remove_authoring_keys(tmp_path)
    assert removed == ["lab_suite/cases/advanced_example.py"]
    source = path.read_text(encoding="utf-8")
    assert "answer" not in source and "replacements" not in source
    functions = {node.name for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}
    assert functions == {"probe"}
    assert ordinary.read_text() == "VALUE = 1\n"
    assert remove_authoring_keys(tmp_path) == []


def test_teacher_source_without_keys_is_unchanged(tmp_path):
    target = tmp_path / "lab_suite" / "cases"
    target.mkdir(parents=True)
    path = target / "advanced_example.py"
    original = "def probe(case, urls, evidence):\n    return {'healthy': True}\n"
    path.write_text(original, encoding="utf-8")
    assert remove_authoring_keys(tmp_path) == []
    assert path.read_text(encoding="utf-8") == original

