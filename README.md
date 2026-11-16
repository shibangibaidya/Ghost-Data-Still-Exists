# Deletion Audit

A deletion-audit agent for HackerRank Orchestrate (October 2026), "Ghost Data Still Exists". For
every request in `dataset/deletion_requests.csv` it decides whether the person asked for
deletion, resolves their current and historical identifiers, reconstructs the state of every
linked record at the case's `audit_at`, applies `dataset/deletion_rules.csv`, and writes one row
per case to `output.csv` (`case_id,deletion_status,steps_json`).

Python 3.13, standard library only at run time. The Gemini SDK (`google-genai`) is needed only to
make new model calls; every model answer the run needs ships in `cache/llm/`, so the full run
reproduces offline, with no API key and no network.

## Layout

`code.zip` has the layout of the challenge's own packaging command (`README.md` plus `code/`):
the repository's `README.md` at the zip root and every other file under `code/`, for example
`code/main.py`, `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md`.
Unzip it at the repository root, next to `dataset/`, because paths are resolved from the
repository root (the parent of `code/`):

```powershell
Expand-Archive code.zip -DestinationPath <repo root>      # macOS/Linux: unzip code.zip -d <repo root>
```

```text
<repo root>/
├── README.md           from the zip: challenge text plus setup and run steps
├── dataset/            the challenge data (not shipped)
├── code/               from the zip: this folder (README.md, main.py, deletion_audit/, ...)
├── .env                optional, never committed or shipped
└── output.csv          written by code/main.py
```

## Setup

From the repository root (Windows PowerShell; on macOS/Linux use `python3` and
`.venv/bin/python`):

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r code\requirements-dev.txt
```

`requirements.txt` pins `google-genai` (live calls only) and `requirements-dev.txt` adds `pytest`.
For a replay-only run, no package is needed beyond Python itself.

Optional, for live model calls only: copy `code\.env.example` to `.env` at the repository root and
set `GEMINI_API_KEY`. Without a `.env`, the provider defaults to Gemini with the models that built
the shipped cache, and no call is ever attempted without a key.

## Run

```powershell
.venv\Scripts\python -B code\main.py --offline      # official run from the shipped cache -> .\output.csv
.venv\Scripts\python -B code\main.py                # same, but cache misses go to the provider (needs the key)
.venv\Scripts\python -B code\evaluation\main.py --offline   # sample + custom-case metrics -> evaluation_report.md
.venv\Scripts\python -B code\evaluation\verify_output.py   # independent contract check of .\output.csv
.venv\Scripts\python -B -m pytest code\tests -q -p no:cacheprovider   # 229 tests, no network
```

`verify_output.py` re-states the output contract from the challenge text and reads the dataset
itself, importing nothing from the pipeline. It checks the header, one row per request in
`case_id` order, allowed statuses and (phase, operation, state) triples, step numbering and phase
order, `[]` for NOT_APPLICABLE, which steps must or must not cite evidence, that every
`evidence_id` exists, that no step cites an item captured after the case's `audit_at`, the
remediation order and verification coverage. Exit code 1 means at least one error.

`main.py` writes `output.csv` (518 rows, sorted by the numeric part of `case_id`), one JSON trace
per case in `.audit_traces/`, and `evaluation/usage_report.md`. It exits non-zero if any case
fell back to the safe default row. Two runs produce byte-identical output. Other flags:
`--requests` and `--output` (for example the sample file), `--cache-dir`, `--usage-report`.

Filling the cache (only needed after a prompt or dataset change; it makes live calls):
`code\tools\warm_cache.py intent` and `code\tools\warm_cache.py visual`. Every answer is cached
as soon as it arrives, so an interrupted run resumes.

## Architecture

```text
load_dataset ─┬─> IdentityDirectory (current + historical emails, phones, hashes)
              ├─> classify_intents ── LLM, cached ──> only Deletion Intent continues
              ├─> build_evidence_index (SQL, JSON, TXT, 26 logs; PNG/PDF via VLM, cached + verified)
              └─> link_cases: identifier match, reference match, lineage, burst attribution
                     │
   per case: state at audit_at -> policy -> findings / remediation / verification -> contract check
                     │
              output.csv, .audit_traces/, evaluation/usage_report.md
```

| Module | Role |
|---|---|
| `deletion_audit/dataset.py`, `model.py` | Loads the CSVs and the evidence manifest (UTF-8, CRLF-safe); domain types |
| `deletion_audit/identity.py` | Resolves a request email (current, historical, any case) to a subject and the identifiers valid at `audit_at` |
| `deletion_audit/intent.py` | Deletion Intent from `request_text`, one model call per distinct normalized text |
| `deletion_audit/evidence/` | Turns every evidence file into time-stamped observations; a record is usable only once known (captured at or before `audit_at`) and is cited only when citable |
| `deletion_audit/linking/` | Ties records to the subject. A name is never enough. Burst attribution assigns unlabelled deletion activity to at most one case and abstains when ambiguous |
| `deletion_audit/assessment/` | State at `audit_at` (an explicit observation beats an omission; claims never prove state), policy per data category, and the ordered plan |
| `deletion_audit/submission.py` | Every output-contract rule; a case that breaks one gets the fallback row `INCOMPLETE,[]` |
| `deletion_audit/llm/` | The only external seam: request, cache, transport (Gemini), timeouts, quota handling |
| `deletion_audit/usage.py` | The usage report |
| `evaluation/` | Scoring, custom cases, the evaluation report, and `verify_output.py`, the independent output checker |

Key decisions are recorded as ADRs in the repository (`docs/adr/`, not shipped): bursts are
exclusive and abstain; claims never establish state; step order follows one sort rule; the LLM is
confined to intent and visual evidence; and each observation has two clocks (known versus citable).

### Where the model is used, and why only there

1. **Intent** (`prompts/intent_classification.md`, `gemini-3.5-flash-lite`, low thinking): request
   texts are free-form, multilingual and sometimes negated ("No quiero que eliminen mi cuenta"), which keyword
   rules handle badly. 398 distinct texts, each answered once and cached.
2. **Visual evidence** (`prompts/evidence_extraction.md`, `gemini-3.5-flash`, 5 files answered by
   `gemini-3.7-flash`): 15 PNG/PDF files are transcribed into records and fields. Every
   transcription was then checked by hand (log below), and corrections live in
   `cache/visual_verification.json`. The official run refuses unverified extractions.

Everything else (identity, linking, state, policy, planning, ordering, serialization) is
deterministic code, so the answer for a case can be traced to evidence IDs and rules. Model
answers are replayed from the cache, which keeps runs reproducible; responses are cached only
after they finish normally and pass the JSON schema.

## Evaluation summary

Full numbers, the custom cases and the failures found are in `evaluation/evaluation_report.md`.
On the 32 public samples: status accuracy 0.969, row accuracy 0.922 of expected step rows and
0.594 of output rows fully correct, step F1 0.960 (precision 1.000, recall 0.922), exact step
sequence 0.594, resource-link precision 1.000 (recall 0.865). The 6 custom cases
(a historical email, an upper-case email, a Spanish negated non-deletion, an audit time moved
before a capture, a near-miss decoy email, a duplicate claimant) all match exactly. The official run gives COMPLETE 152,
INCOMPLETE 356, NOT_APPLICABLE 10, with 0 failed cases, and `verify_output.py` finds 0 errors.

Adversarial tests (`tests/test_adversarial.py`) cover prompt injection in the request text,
conflicting evidence, missing files and decoy identities:
- *Prompt injection*: the text reaches the model only as data under a fixed prompt and schema.
  An off-schema answer is rejected and never cached. Injected instructions change no step,
  citation or subject. The worst a fooled intent answer can do is an empty NOT_APPLICABLE row.
- *Conflicting evidence*: the latest observation per copy wins, and a replica that still holds a
  record outweighs an absent primary. Evidence outranks check claims, and only a record's latest
  content decides exposure.
- *Missing files*: a missing evidence file or table stops the run with the file named, and nothing
  is written.
- *Decoy identities*: lookalike emails resolve to nobody, and two real users who share a name share
  no linking identifier.

Token usage and estimated cost of the run are in `evaluation/usage_report.md` (413 model answers,
about 227k tokens, about USD 0.30 at paid-tier prices; built on the free tier).

## What the iterations taught us

Most bugs below were found by a number that did not add up, then traced in the evidence. The
contract validator counts two suspicious shapes: INCOMPLETE without a finding, and COMPLETE
without a verification. On the official run their total went from 17 to 4, then 2, then 1, and
each drop was a real bug. The others came from a sample mismatch (case_09) and a custom case
(custom_4). Every fix is a general rule with a test, kept only if sample precision stayed
1.000.

- **A cache miss hid the record it was looking up.** Log lines of one trace are merged into one
  observation. A `cache_lookup ... result=miss` line overrode the `rows=1` / `http_status=200`
  read that followed it, so records that were present looked deleted. INCOMPLETE rows without a
  finding fell from 13 to 3, and 24 official rows changed: retained audit records stopped being
  reported missing, and remaining support and analytics records surfaced. Lesson: when sources
  are merged, decide which line is allowed to speak for the resource.
- **SQL flags stored as 0/1 were read as false.** `restore_allowed INTEGER` with value `1` meant
  "not restorable", so restore risks and active flows in SQL snapshots were missed (sample
  case_09). Sample step F1 went from 0.957 to 0.960 and exact sequences from 0.562 to 0.594.
  Lesson: a flag column is a convention, so parse `1`, `true` and `yes`, and test each format.
- **Catalog aliases were specified but never implemented.** The data catalog ties business
  references to resources ("export_1842 (NST-2026-0718-1842)",
  "object_19 (delivery_confirmation_19.pdf)"). Screens and PDFs show only the business
  reference. With exact alias matching, 2 official cases moved from an empty COMPLETE to a
  reported remaining record. Lesson: a rule in the spec without a test is not implemented.
- Smaller ones, same pattern:
  - Burst attribution first attributed every worker action and was right on 12 of 17 links. Now
    it takes account deletions only and is right on 12 of 12.
  - Visual records took their time from printed dates, so case_07 lost an attachment. Status
    accuracy went from 0.938 to 0.969.
  - Uncitable violations lost their remediation (found by custom_4).
  - A backup with no stated restore access was certified as safe (official case_55, below).
  - A deleted account lost its verification when its absence was stated only in a file
    captured after the audit time, although an inventory taken minutes before the audit, with the
    account service in scope, omits it. That omission is now the citation, as it already was when
    no explicit observation exists. 11 official COMPLETE rows gained their `VERIFY_RECORD_ABSENT`.
  - Support attachments were redacted as messages. A consistency audit found that the deletion
    worker's policy action on a record (delete, redact, anonymize, retain) agrees with the
    category on all 58 sample records and all 173 official records whose category is stated,
    and disagreed only on 14 support records inferred as messages. A log's `file=` now marks an
    attachment, and an unstated category follows the worker's action within the request window
    (never its result). 13 official rows changed from redact to delete.
- **What did not work, and was dropped:** attributing worker actions in a lone off-the-hour slot
  to its claimant. Its only sample target (`analytics_901`, case_04) also sits in the retry
  window of the 07:00 slot of cases 83 to 85, so it linked nothing new, on the samples or on the
  official run.

## Known limitations

Measured on the 32 public samples (the official answers are not public):

- **Link recall 0.865** (96 of 111 answer resources; precision 1.000, all 96 links made are
  correct). The 15 misses, traced one by one:
  - 3 appear in no participant file at all (`account_004`, `audit_record_6645`,
    `audit_record_5569`).
  - 5 are account deletions in shared report slots where more than one claim-consistent deletion
    remains. For cases 31 and 32, the file that would settle it was captured after the audit
    time.
  - 7 are anonymized analytics records with no identifier left, whose only link is an
    `ANONYMIZE_RECORD` activity shared by 2 or more claimants.

  Attributing any of these means guessing, which would cost the precision the plan relies on.
- **Exact step sequence 0.594** (19 of 32). All 13 inexact cases are cases with a missed link;
  wherever every link is found, the steps and their order match exactly. Status accuracy is
  0.969: case_04 is predicted INCOMPLETE with no steps, because both of its resources are
  among the misses.
- **case_55** (official): the subject's backup `backup_9287` appears with their email in a
  recovery console (`evidence_32`), but no evidence at the audit time says whether its restore
  access is blocked. It used to come out COMPLETE with 0 steps. It is now INCOMPLETE with a
  restore-risk finding, `BLOCK_RESTORE` and a verification, under the rule that a backup can be
  restored until something blocks it. This rests on that default: no sample backup lacks a
  restore flag, so the samples cannot confirm it. It is the only official row the rule changes.
- **case_126** (official): the remaining account `account_8819` is known only from
  `evidence_12`, a snapshot section captured on 08-11 inside a file captured on 08-19, after the
  08-17 audit time. A finding must cite evidence, so the row is INCOMPLETE with remediation and
  verification but no finding. It is the one warning left.
- **case_46** (official) is COMPLETE but omits the verification of `tombstone_17`. The only
  evidence of it, a reference-registry screenshot (`evidence_42`), shows the tombstone but not
  whether its identifiers were removed. A screen that displays no personal fields does not prove
  that none are held, and no sample uses a screenshot as redaction proof. A verification without
  a citation is not allowed in a COMPLETE row, so the step is left out (D7).
- The hidden grader's formulas are not public; every number above is a proxy against the samples
  and 6 hand-derived custom cases.

## Packaging

```powershell
.venv\Scripts\python -B code\tools\package.py       # -> .\code.zip
```

The tool runs the official, sample and custom-case audits offline to find the cache entries they
use, packs the repository's `README.md` at the zip root and `code/` under `code/` (an allow-list
of file types plus only those cache entries), then reopens the zip and audits it: every entry is
`README.md` or under `code/`; no `.env`, `__pycache__`, `.venv`, `dataset/` or nested archives;
no key prefixes or the configured key; and the required files present, including
`code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md`. It writes nothing
if the audit fails. `tests/test_package.py` covers the layout and each audit rule.

## VLM verification log

The 15 PNG/PDF evidence files were transcribed once by a vision model (cached in `cache/llm/`),
then each file was opened and checked by hand against its extraction: every visible identifier
and record ID was listed first, then compared. Corrections are transcription-only, never informed
by the sample answers, and live in `cache/visual_verification.json` (keyed by the file's sha256),
on top of the raw model output, which is never edited.

Verified 2026-10-25 by Claude Code (claude-opus-5-5).

| File | Model | Records extracted | Correction | Reason |
|---|---|---|---|---|
| evidence_04 account_admin_console_20260926.png | gemini-3.5-flash | account_5762 | add record `support_9538` (fields Support, Activity) | shown as "Support: CS-8538 · support_9538" and "28 Aug support_9538 created" but had no record entry |
| evidence_09 search_index_inspector_20260901.png | gemini-3.7-flash | search_428, search_1874 | none | |
| evidence_14 connector_management_console_20260807.png | gemini-3.7-flash | data_flow_1121, data_flow_9846 | none | |
| evidence_18 account_history_report_20260718.pdf | gemini-3.7-flash | user_18 (a reference, not a resource) | none | statement `NST-2026-0718-1842` is the catalog alias of `export_1842` (activity_4009), matched exactly |
| evidence_19 analytics_profile_view_20260713.png | gemini-3.7-flash | analytics_7438 | none | |
| evidence_23 customer_export_console_20260618.png | gemini-3.7-flash | export_1258 to export_1261 | none | export_1261 shows Created 20 Jun on a screen captured 18 Jun; transcribed as shown |
| evidence_27 legal_retention_register_20260529.pdf | gemini-3.5-flash | none | none | order numbers and user references only (D3) |
| evidence_28 support_ticket_console_20260524.png | gemini-3.5-flash | support_1034 | none | |
| evidence_32 backup_restore_console_20260429.png | gemini-3.5-flash | backup_9286 to backup_9291 (4) | none | backup_9281 appears only as an Activity restore event; not added, since the screen does not show that archive existing |
| evidence_37 security_audit_explorer_20260905.png | gemini-3.5-flash | audit_record_001 to _006 | none | |
| evidence_42 deleted_account_reference_20260310.png | gemini-3.5-flash | user_17 (a reference, not a resource) | none | `user_17` is the catalog alias of `tombstone_17` (activity_4011), matched exactly |
| evidence_46 object_storage_browser_20260213.png | gemini-3.5-flash | none | add 8 records, one per listed object, keyed by file name (T16) | the object listing was not transcribed; no record IDs are shown and none are inferred. `delivery_confirmation_19.pdf` is the catalog alias of `object_19` (activity_4007) |
| evidence_55 security_retention_extract.pdf | gemini-3.5-flash | audit_record_003 | none | |
| evidence_58 delivery_correction_attachment.pdf | gemini-3.5-flash | support_001 | 7 fields added to `support_001` from page 2: file, size, ticket, region, uploader, captured, checksum prefix | page 2 "Attachment properties and upload history" was not transcribed |
| evidence_60 marketing_profile_console.png | gemini-3.5-flash | marketing_001 | none | |

Two pipeline rules came out of the verification (general rules, not corrections):
- A visual record is Known from the file's capture time. Dates printed in a file describe the
  record's history ("received 18 Jun") and no longer move it out of the Episode it was captured in.
- Printed labels map to the vocabulary: `file`/`filename` to `file_name`, `uploader` to
  `uploaded_by` (`VISUAL_FIELD_ALIASES`).
