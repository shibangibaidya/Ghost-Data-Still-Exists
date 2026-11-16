# Ghost Data Still Exists — Deletion Audit Agent

A deletion-audit agent. The full task description is in [problem_statement.md](problem_statement.md).

A deletion workflow can report **Complete** while a person's data still sits in replicas, logs,
support attachments, exports, search indexes, analytics records or backups, and while sync jobs
and restores can bring it back. For every deletion request, this agent:

1. decides whether the request text actually asks for deletion (in any language, including negations);
2. resolves the person's current and historical identifiers (emails, phones, email hashes);
3. finds every linked record across SQL snapshots, JSON, logs, text records, screenshots and PDFs;
4. reconstructs each record's state at the case's audit time, treating workflow statuses as claims, not proof;
5. applies the deletion policy (delete, redact, anonymize, retain, or keep an account tombstone);
6. returns `COMPLETE`, `INCOMPLETE` or `NOT_APPLICABLE`, with evidence-backed findings, an ordered
   remediation plan, and the verification checks that must pass afterwards.

The result is [output.csv](output.csv): one row per request (`case_id,deletion_status,steps_json`).

## Highlights

- **Deterministic core.** Identity resolution, evidence linking, state reconstruction, policy,
  planning and serialization are plain Python with no model calls, so every step traces back to
  an evidence ID and a rule. Two runs produce byte-identical output.
- **The model is used only where rules fall short:** classifying free-form, multilingual request
  text, and transcribing screenshots and PDFs. Every visual transcription was checked by hand
  against the source file.
- **Cached model answers.** Every model answer is cached as soon as it arrives, so reruns and
  interrupted runs replay without new calls.
- **Precision first.** When the evidence can't tie a record to one person, the agent abstains
  instead of guessing. On the public samples, every resource link it makes is correct.
- **Independent output checker.** `verify_output.py` re-implements the output contract without
  importing the pipeline and checks every row.

## Results

Measured on the 32 public sample cases (answers for the 518 evaluation cases are not public):

| Metric | Score |
|---|---|
| Deletion-status accuracy | 0.969 |
| Step precision / recall / F1 | 1.000 / 0.922 / 0.960 |
| Resource-link precision / recall | 1.000 / 0.865 |
| Output rows fully correct (exact step sequence) | 0.594 |

On the 518 evaluation cases the agent returns 152 `COMPLETE`, 356 `INCOMPLETE` and 10
`NOT_APPLICABLE`, with no failed cases and zero contract errors from the independent checker.
Six extra hand-built test cases (a historical email, upper-case email, a Spanish negated
request, an audit time moved before an evidence capture, a near-miss decoy email, a duplicate
claimant) all match exactly.

Full metrics, the custom cases and the failures found along the way are in
[code/evaluation/evaluation_report.md](code/evaluation/evaluation_report.md).

## How it works

```text
load dataset ─┬─> identity directory (current + historical emails, phones, hashes)
              ├─> intent classification ── LLM, cached ──> only deletion requests continue
              ├─> evidence index (SQL, JSON, text, logs; PNG/PDF via vision model, cached + verified)
              └─> linking: identifier match, reference match, lineage, burst attribution
                     │
   per case: state at audit time -> policy -> findings / remediation / verification -> contract check
                     │
              output.csv
```

| Module | Role |
|---|---|
| `deletion_audit/identity.py` | Resolves a request email (current or historical) to a person and the identifiers valid at audit time |
| `deletion_audit/intent.py` | Decides whether the request text authorizes deletion |
| `deletion_audit/evidence/` | Turns every evidence file into time-stamped observations; nothing captured after the audit time is used |
| `deletion_audit/linking/` | Ties records to the person; a name alone is never enough, and ambiguous activity is left unattributed |
| `deletion_audit/assessment/` | State at audit time, policy per data category, and the ordered remediation plan |
| `deletion_audit/submission.py` | Enforces every output-contract rule |
| `deletion_audit/llm/` | The only external dependency: prompts, cache, Gemini transport, timeouts |
| `evaluation/` | Scoring, custom cases, report generation, and the independent output checker |

A detailed technical write-up, covering design decisions, what the iterations taught, known
limitations and the visual-evidence verification log, is in [code/README.md](code/README.md).

## Getting started

### Requirements

- Python 3.13
- The dataset described in [problem_statement.md](problem_statement.md), placed in a `dataset/`
  folder at the repository root. It is not included in this repository.

```text
<repo root>/
├── README.md
├── problem_statement.md
├── output.csv          written by code/main.py
├── dataset/            input data (add it yourself)
└── code/
    ├── main.py
    ├── deletion_audit/
    ├── evaluation/
    ├── prompts/
    ├── tests/
    └── tools/
```

### Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/shibangibaidya/Ghost-Data-Still-Exists.git
   cd Ghost-Data-Still-Exists
   ```

2. Copy the `dataset/` folder into the repository root, so that
   `dataset/deletion_requests.csv` exists.

3. Create a virtual environment and install the dependencies.

   Windows PowerShell:

   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install -r code\requirements-dev.txt
   ```

   macOS / Linux:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r code/requirements-dev.txt
   ```

4. Copy `code/.env.example` to `.env` at the repository root and set `GEMINI_API_KEY`.
   Never commit `.env`.

The commands below use Windows paths. On macOS/Linux, replace `.venv\Scripts\python` with
`.venv/bin/python` and `\` with `/`.

### Run

The model-answer cache is not included in this repository, so the first run calls Gemini for
every distinct request text and every screenshot/PDF (about 400 calls), and caches each answer
under `code/cache/`. The hand-checked corrections to the visual transcriptions are not included
either, so pass `--allow-unverified` to accept the model's transcriptions as they are. Because
of that, a fresh run can differ slightly from the committed `output.csv`.

```powershell
# Audit all requests -> .\output.csv and code\evaluation\usage_report.md
.venv\Scripts\python -B code\main.py --allow-unverified

# Rerun from the local cache only, with no model calls
.venv\Scripts\python -B code\main.py --offline --allow-unverified

# Check output.csv against the output contract
.venv\Scripts\python -B code\evaluation\verify_output.py

# Score the sample and custom cases -> code\evaluation\evaluation_report.md
.venv\Scripts\python -B code\evaluation\main.py --allow-unverified

# Tests (229; need dataset/, but no network and no API key)
.venv\Scripts\python -B -m pytest code\tests -q
```

`main.py` also accepts `--requests` and `--output` (for example to run the sample file),
`--cache-dir` and `--usage-report`.

## Limitations

- **Link recall is 0.865** on the samples. Of the 15 missed links, 3 appear in no evidence file,
  5 are account deletions where more than one person fits the evidence, and 7 are anonymized
  records with no identifier left. Attributing them would mean guessing, so the agent abstains.
- Every missed sequence on the samples comes from a missed link. Wherever all links are found,
  the steps and their order match exactly.
- The official grading formulas are not public, so all scores above are measured against the
  public samples and the hand-built cases.

## Tech stack

Python 3.13 (standard library at run time), Google Gemini (`gemini-3.5-flash-lite` for intent,
`gemini-3.5-flash` / `gemini-3.7-flash` for visual evidence), pytest.

## Author

[@shibangibaidya](https://github.com/shibangibaidya)
