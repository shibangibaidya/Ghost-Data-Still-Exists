"""Builds code.zip from code/ and audits it (FR-17). Never calls a model.

Run from the repository root:
    .venv\\Scripts\\python -B code\\tools\\package.py              (writes .\\code.zip)
    .venv\\Scripts\\python -B code\\tools\\package.py --output X  (writes X)

The layout matches the challenge's own packaging command (`zip -r code.zip README.md code`):
the repository's README.md at the zip root and every other entry under code/, so the reports
are at code/evaluation/evaluation_report.md and code/evaluation/usage_report.md. Unzipping at
the repository root, next to dataset/, gives a runnable tree. Only an allow-list of file types
is packed. Of the model-answer cache, only the entries that the official, sample and custom-case
runs replay are packed; finding them means running those audits offline, which also proves the
packed cache reproduces them. The audit then reopens the zip and fails on secrets, forbidden
paths, nested archives, an entry outside that layout or a missing required file. The exit code
is non-zero on any failure, and the zip is then left unwritten.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import io  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path, PurePosixPath  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CODE_DIR = REPO_ROOT / "code"
sys.path.insert(0, str(CODE_DIR))

from deletion_audit.audit import run_audit  # noqa: E402
from deletion_audit.llm import LlmClient  # noqa: E402
from deletion_audit.settings import Settings  # noqa: E402

DATASET_DIR = REPO_ROOT / "dataset"
ROOT_README = REPO_ROOT / "README.md"
ZIP_CODE_DIR = "code"
CACHE_DIR = CODE_DIR / "cache" / "llm"
REQUEST_FILES = (
    DATASET_DIR / "deletion_requests.csv",
    DATASET_DIR / "sample_deletion_requests.csv",
    CODE_DIR / "evaluation" / "custom_cases" / "requests.csv",
)
ALLOWED_SUFFIXES = {".py", ".md", ".txt", ".toml", ".csv", ".json", ".sql", ".log", ".jsonl"}
ALLOWED_NAMES = {".env.example"}
SKIPPED_DIRS = {"__pycache__", ".pytest_cache", ".audit_traces", ".test_tmp", ".venv", "dataset"}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".whl"}
REQUIRED = ("README.md", *(f"{ZIP_CODE_DIR}/{name}" for name in (
    "README.md", "main.py", "requirements.txt", ".env.example", "evaluation/evaluation_report.md",
    "evaluation/usage_report.md", "cache/visual_verification.json", "prompts/intent_classification.md",
    "prompts/evidence_extraction.md")))
# Key prefixes for Anthropic and Google, split so this file does not match itself. The
# configured key itself is also searched for.
SECRET_MARKERS = (b"sk-" + b"ant", b"AI" + b"za")
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def used_cache_keys(settings: Settings) -> set[str]:
    keys: set[str] = set()
    for requests in REQUEST_FILES:
        llm = LlmClient(CACHE_DIR, transport=None)  # no transport: a cache miss raises
        run = run_audit(DATASET_DIR, requests, llm, intent_model=settings.intent_model or "",
                        visual_model=settings.visual_model or "")
        if run.failures:
            raise RuntimeError(f"{requests.name}: {len(run.failures)} cases failed, e.g. {run.failures[0]}")
        keys |= {event.key for event in llm.usage_events}
        print(f"{requests.name}: {len(run.results)} cases, {len({e.key for e in llm.usage_events})} cached answers")
    return keys


def select_files(keys: set[str], code_dir: Path = CODE_DIR,
                 root_readme: Path = ROOT_README) -> list[tuple[str, Path]]:
    """(name in the zip, source file), sorted by name: the root README.md, then code/..."""
    files = [("README.md", root_readme)] if root_readme.is_file() else []
    for path in sorted(code_dir.rglob("*")):
        relative = path.relative_to(code_dir)
        if not path.is_file() or SKIPPED_DIRS & set(relative.parts[:-1]):
            continue
        if path.name not in ALLOWED_NAMES and (path.name.startswith(".") or path.suffix not in ALLOWED_SUFFIXES):
            continue
        if relative.parts[:2] == ("cache", "llm") and path.stem not in keys:
            continue
        files.append((f"{ZIP_CODE_DIR}/{relative.as_posix()}", path))
    return sorted(files)


def build_zip(files: list[tuple[str, Path]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in files:
            info = zipfile.ZipInfo(name, FIXED_TIME)  # fixed times: the same tree gives the same zip
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def audit_zip(data: bytes, keys: set[str], secret: str | None) -> list[str]:
    problems = []
    markers = SECRET_MARKERS + ((secret.encode("utf-8"),) if secret else ())
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        for name in names:
            parts = PurePosixPath(name).parts
            if SKIPPED_DIRS & set(parts) or any(part.startswith(".env") and part != ".env.example" for part in parts):
                problems.append(f"forbidden path: {name}")
            if name.endswith(".pyc") or PurePosixPath(name).suffix in ARCHIVE_SUFFIXES:
                problems.append(f"forbidden file type: {name}")
            if name != "README.md" and parts[0] != ZIP_CODE_DIR:
                problems.append(f"outside the code/ layout: {name}")
            content = archive.read(name)
            for marker in markers:
                if marker in content:
                    # Never print the matched text: it may be the key.
                    problems.append(f"possible secret in {name} (marker {markers.index(marker) + 1})")
        problems += [f"missing required file: {name}" for name in REQUIRED if name not in names]
        packed = {PurePosixPath(n).stem for n in names if n.startswith(f"{ZIP_CODE_DIR}/cache/llm/")}
        problems += [f"missing cache entry: {key}" for key in sorted(keys - packed)]
    return problems


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "code.zip")
    args = parser.parse_args()

    settings = Settings.from_environment(REPO_ROOT)
    keys = used_cache_keys(settings)
    files = select_files(keys)
    data = build_zip(files)
    problems = audit_zip(data, keys, settings.api_key)
    cache_total = len(list(CACHE_DIR.glob("*.json")))
    print(f"{len(files)} files, {len(data):,} bytes; cache entries packed {len(keys)} of {cache_total}")
    if problems:
        print("Audit FAILED, zip not written:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f"Audit passed: no secrets, no __pycache__/.env/dataset/archives, README.md + code/ layout, "
          f"required files present. Wrote {args.output}")
    for name in REQUIRED:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
