"""code.zip layout and audit (code/tools/package.py). Builds zips in memory; never calls a model."""

import io
import zipfile
from pathlib import Path

from conftest import REPO_ROOT
from tools.package import REQUIRED, audit_zip, build_zip, select_files


def write(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def fake_repo(root: Path) -> tuple[Path, Path]:
    """A repository root README.md and a code/ folder holding every required file plus junk."""
    readme = write(root / "README.md", "# challenge")
    code = root / "code"
    for name in REQUIRED:
        if name.startswith("code/"):
            write(code / name.removeprefix("code/"))
    write(code / "cache" / "llm" / "used.json")
    write(code / "cache" / "llm" / "unused.json")
    write(code / "__pycache__" / "main.cpython-313.pyc")
    write(code / ".env", "DELETION_AUDIT_PROVIDER=gemini")
    write(code / "old.zip")
    write(code / ".audit_traces" / "case_1.json")
    return code, readme


def names_in(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.namelist()


def test_entries_are_the_root_readme_plus_everything_under_code(workdir: Path) -> None:
    code, readme = fake_repo(workdir)

    names = [name for name, _ in select_files({"used"}, code_dir=code, root_readme=readme)]

    assert names[0] == "README.md"
    assert all(name.startswith("code/") for name in names[1:])
    assert "code/README.md" in names
    assert "code/evaluation/evaluation_report.md" in names
    assert "code/evaluation/usage_report.md" in names
    assert "code/.env.example" in names
    assert "code/cache/llm/used.json" in names
    for left_out in ("code/cache/llm/unused.json", "code/.env", "code/old.zip",
                     "code/__pycache__/main.cpython-313.pyc", "code/.audit_traces/case_1.json"):
        assert left_out not in names


def test_a_well_formed_zip_passes_the_audit(workdir: Path) -> None:
    code, readme = fake_repo(workdir)

    data = build_zip(select_files({"used"}, code_dir=code, root_readme=readme))

    assert audit_zip(data, {"used"}, secret=None) == []
    assert build_zip(select_files({"used"}, code_dir=code, root_readme=readme)) == data  # reproducible


def test_the_audit_rejects_the_old_flat_layout_and_forbidden_entries(workdir: Path) -> None:
    code, readme = fake_repo(workdir)
    files = select_files({"used"}, code_dir=code, root_readme=readme)
    flat = [("evaluation/evaluation_report.md", code / "evaluation" / "evaluation_report.md")]
    forbidden = [("code/.env", code / ".env"), ("code/old.zip", code / "old.zip"),
                 ("code/__pycache__/main.cpython-313.pyc", code / "__pycache__" / "main.cpython-313.pyc"),
                 ("dataset/deletion_requests.csv", readme)]

    problems = audit_zip(build_zip(files + flat + forbidden), {"used"}, secret=None)

    assert "outside the code/ layout: evaluation/evaluation_report.md" in problems
    assert "forbidden path: code/.env" in problems
    assert "forbidden file type: code/old.zip" in problems
    assert "forbidden path: code/__pycache__/main.cpython-313.pyc" in problems
    assert "forbidden path: dataset/deletion_requests.csv" in problems


def test_the_audit_requires_the_reports_under_code_evaluation_and_the_root_readme(workdir: Path) -> None:
    code, readme = fake_repo(workdir)
    files = [(name, path) for name, path in select_files({"used"}, code_dir=code, root_readme=readme)
             if name not in ("README.md", "code/evaluation/usage_report.md")]

    problems = audit_zip(build_zip(files), {"used", "missing"}, secret=None)

    assert "missing required file: README.md" in problems
    assert "missing required file: code/evaluation/usage_report.md" in problems
    assert "missing cache entry: missing" in problems


def test_the_audit_finds_the_configured_key_without_printing_it(workdir: Path) -> None:
    code, readme = fake_repo(workdir)
    write(code / "notes.md", "token: s3cr3t-value")

    problems = audit_zip(build_zip(select_files({"used"}, code_dir=code, root_readme=readme)), {"used"},
                         secret="s3cr3t-value")

    assert problems == ["possible secret in code/notes.md (marker 3)"]


def test_the_real_repository_packs_into_the_expected_layout() -> None:
    files = select_files(set())
    names = [name for name, _ in files]

    assert names[0] == "README.md" and files[0][1] == REPO_ROOT / "README.md"
    assert all(name.startswith("code/") for name in names[1:])
    assert audit_zip(build_zip(files), set(), secret=None) == []
