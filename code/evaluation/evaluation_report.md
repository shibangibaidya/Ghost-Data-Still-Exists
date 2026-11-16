# Evaluation report

The hidden grader's formulas and the answers for the 518 evaluation requests are not public. Every
number here is a proxy measured against answers we know: the 32 public samples
(`dataset/sample_output.csv`) and 6 custom cases whose answers were derived by hand
(`code/evaluation/custom_cases/`). Sample answers are read only by the evaluator and the tests,
never by the prediction path.

Regenerate the metrics block from the repository root (offline replays the cached model answers):

```text
.venv\Scripts\python -B code\evaluation\main.py --offline
```

Only the text between the `BEGIN GENERATED` and `END GENERATED` markers is rewritten.

## How the metrics are computed

The four accuracy levels the challenge asks for map to the table rows as follows: **case**
accuracy is the deletion-status row, **row** accuracy is the two "Row accuracy" rows, **field**
accuracy is the "Field accuracy" rows, and **sequence** accuracy is the last row.

- **Case-level deletion-status accuracy**: share of cases whose `deletion_status` matches.
- **Row accuracy, step rows**: a step row is one object of `steps_json`, compared as the full
  tuple of `phase`, `system_id`, `resource_id`, `operation`, `state` and `evidence_id`. Step-row
  accuracy = expected step rows matched by a predicted step row / expected step rows, summed over
  all cases (multiset overlap per case, so each expected row is matched at most once). It is the
  full-step recall below; extra predicted rows are measured by the precision.
- **Row accuracy, output rows**: an output row is one line of `output.csv`. Output-row accuracy =
  output rows whose `deletion_status` and whole `steps_json` (every step, in order) both equal the
  expected row / expected output rows. A row with the right steps but the wrong status counts as
  wrong, so this is never higher than case or sequence accuracy.
- **Step precision, recall and F1 (full step)**: steps are compared as tuples of `phase`,
  `system_id`, `resource_id`, `operation`, `state` and `evidence_id` (multiset overlap per case).
  "Ignoring evidence_id" drops that field from the tuple.
- **Field accuracy, aligned steps**: predicted and expected steps are paired when phase, system,
  resource and operation agree; the table reports how often `state` and `evidence_id` then match.
- **Field accuracy, by position**: step *n* is compared with expected step *n*, field by field,
  over the number of expected steps. This also measures ordering.
- **Sequence accuracy**: share of cases whose whole step list is identical, in order.
- **Resource links**: whether the resources the agent tied to the subject are the ones the answer
  mentions, by the basis of the link (identifier match, reference, lineage, burst attribution).

## Metrics

<!-- BEGIN GENERATED -->
### Sample requests (32 cases, `dataset/sample_output.csv`)

| Metric | Value |
|---|---|
| Case-level deletion-status accuracy | 0.969 |
| Row accuracy, step rows (matched / expected step rows) | 0.922 |
| Row accuracy, output rows (status and steps_json fully correct) | 0.594 |
| Step precision (full step) | 1.000 |
| Step recall (full step) | 0.922 |
| Step F1 (full step) | 0.960 |
| Step F1 ignoring evidence_id | 0.960 |
| Field accuracy, aligned steps: state | 1.000 |
| Field accuracy, aligned steps: evidence_id | 1.000 |
| Field accuracy, by position: phase | 0.922 |
| Field accuracy, by position: system_id | 0.798 |
| Field accuracy, by position: resource_id | 0.798 |
| Field accuracy, by position: operation | 0.813 |
| Field accuracy, by position: state | 0.813 |
| Field accuracy, by position: evidence_id | 0.922 |
| Sequence accuracy (exact step list) | 0.594 |

Status confusion (expected -> predicted): COMPLETE -> COMPLETE: 9, COMPLETE -> INCOMPLETE: 1, INCOMPLETE -> INCOMPLETE: 22

Resource links: precision 1.000, recall 0.865.

| Link basis | Links made | In the answer |
|---|---|---|
| BURST | 12 | 12 |
| IDENTIFIER | 51 | 51 |
| LINEAGE | 1 | 1 |
| REFERENCE | 32 | 32 |

### Custom cases (6 cases, `code/evaluation/custom_cases/`)

| Metric | Value |
|---|---|
| Case-level deletion-status accuracy | 1.000 |
| Row accuracy, step rows (matched / expected step rows) | 1.000 |
| Row accuracy, output rows (status and steps_json fully correct) | 1.000 |
| Step precision (full step) | 1.000 |
| Step recall (full step) | 1.000 |
| Step F1 (full step) | 1.000 |
| Step F1 ignoring evidence_id | 1.000 |
| Field accuracy, aligned steps: state | 1.000 |
| Field accuracy, aligned steps: evidence_id | 1.000 |
| Field accuracy, by position: phase | 1.000 |
| Field accuracy, by position: system_id | 1.000 |
| Field accuracy, by position: resource_id | 1.000 |
| Field accuracy, by position: operation | 1.000 |
| Field accuracy, by position: state | 1.000 |
| Field accuracy, by position: evidence_id | 1.000 |
| Sequence accuracy (exact step list) | 1.000 |

| Case | Expected | Predicted | Steps expected | Steps predicted | Exact match |
|---|---|---|---|---|---|
| custom_1 | INCOMPLETE | INCOMPLETE | 5 | 5 | yes |
| custom_2 | INCOMPLETE | INCOMPLETE | 14 | 14 | yes |
| custom_3 | NOT_APPLICABLE | NOT_APPLICABLE | 0 | 0 | yes |
| custom_4 | INCOMPLETE | INCOMPLETE | 4 | 4 | yes |
| custom_5 | INCOMPLETE | INCOMPLETE | 0 | 0 | yes |
| custom_6 | COMPLETE | COMPLETE | 1 | 1 | yes |
<!-- END GENERATED -->

## Custom cases

Each case mutates one sample request, so its answer is known or can be derived by hand from the
participant-visible data. Custom IDs use the `custom_` prefix and never reach `output.csv`. The
official and sample requests stay in the linking context, as in the official run.

### custom_1: historical email (base: sample case_12)

- **Change**: `email` is `minjun.kim90@gmail.com`, the address user_12 used before the change
  recorded in `audit_282` (2026-05-18), instead of the current `minjun.kim@gmail.com`.
- **Expected**: identical to sample case_12, `INCOMPLETE` with 5 steps. The request email may be
  current or historical, so both must resolve to the same subject and the same records.

### custom_2: upper-case current email (base: sample case_30)

- **Change**: `email` is `CAMILLE.BERNARD@OUTLOOK.COM`, the current address of user_30 in upper case
  (the sample request used the historical `camille.bernard86@outlook.com`).
- **Expected**: identical to sample case_30, `INCOMPLETE` with 14 steps. Email matching ignores case.

### custom_3: Spanish request with a negation, not a deletion (base: sample case_01)

- **Change**: `request_text` is "No quiero que eliminen mi cuenta ni mis datos. Solo necesito
  corregir la dirección de envío de mi último pedido, porque el número de la calle está mal.
  Gracias." ("I do not want my account or data deleted. I only need to correct the shipping
  address of my last order.")
- **Expected**: `NOT_APPLICABLE` with `[]`. The text mentions deletion only to refuse it; the request
  is a correction. This is the only custom text that needed a new model call (cached once).

### custom_4: audit time before the evidence capture (base: sample case_12)

- **Change**: `audit_at` is `2026-09-15T11:59:00Z` instead of `12:00:00Z`.
- **Derivation**: every line about the subject's records in `evidence_01` is stamped 11:55:40 to
  11:58:26, so the agent may know them, but the file was captured at 11:59:39, after the audit
  time, so it cannot be cited. No other file in the request window covers these resources.
  - `analytics_9914` is still present and keyed by the old email hash, so policy requires deletion.
    The violation is known, but a `FINDING` must cite evidence, so no finding can be reported.
    Remediation and verification describe future work and need no evidence, so the plan remains.
  - `account_9797` is absent (lookup returned 404). `audit_record_2945` is a retained security
    record and present.
- **Expected**: `INCOMPLETE` with 4 steps, all with an empty `evidence_id`:
  `DELETE_RECORD analytics_9914`, then `VERIFY_RECORD_ABSENT account_9797`,
  `VERIFY_RECORD_ABSENT analytics_9914` and `VERIFY_RECORD_PRESENT audit_record_2945`.
  `evidence_01` must not be cited.

### custom_5: near-miss decoy email (base: sample case_28)

- **Change**: `email` is `smartinez6461.jr@gmail.com`. It differs from user_28's
  `smartinez6461@gmail.com` only by the `.jr` suffix and appears in exactly one record
  (`search_8168` in `evidence_02`), but it belongs to no user in `user_identities.csv` or
  `user_audit_log.csv`.
- **Expected**: `INCOMPLETE` with `[]`. No subject can be resolved, so nothing can be verified as
  deleted, and none of user_28's records may be attributed to this requester by name or by a
  similar address.

### custom_6: duplicate claimant (base: sample case_01)

- **Change**: none except the ID. The same request arrives twice, so two claimants compete for the
  same burst of deletion activity (`activity_3342`, `account_001`).
- **Expected**: identical to sample case_01, `COMPLETE` with 1 step. Identical claims share the
  burst rather than cancelling each other.

## Official run

The final offline run wrote 518 rows (one per request, sorted by `case_id`) with 0 case
failures: COMPLETE 152, INCOMPLETE 356, NOT_APPLICABLE 10. Two runs are byte-identical. The
pipeline's contract validator and the independent checker `code/evaluation/verify_output.py`
(which re-states the contract and imports nothing from the pipeline) both find 0 errors and one
warning, case_126, explained below.

## Failures found and improvements made

Sample metrics after each change (status accuracy, full-step F1 with precision/recall, sequence
accuracy, link recall at link precision 1.000):

| Stage | Status | Step F1 (P / R) | Sequence | Link recall |
|---|---|---|---|---|
| Thin slice: evidence index, direct links, state, policy, planning | 0.906 | 0.910 (1.000 / 0.834) | 0.344 | 0.748 |
| Burst attribution for account deletions | 0.938 | 0.940 (0.994 / 0.891) | 0.531 | 0.856 |
| Verified visual evidence (screenshots, PDFs) | 0.969 | 0.954 (1.000 / 0.912) | 0.562 | 0.865 |
| Remediation for uncitable violations (from custom_4) | 0.969 | 0.957 (1.000 / 0.917) | 0.562 | 0.865 |
| SQL 0/1 flags read as booleans | 0.969 | 0.960 (1.000 / 0.922) | 0.594 | 0.865 |
| A backup with no stated restore access is a restore risk | 0.969 | 0.960 (1.000 / 0.922) | 0.594 | 0.865 |
| An uncitable absence is cited by an omitting audit-time snapshot | 0.969 | 0.960 (1.000 / 0.922) | 0.594 | 0.865 |
| Support attachments named in logs; the worker's policy action picks the category | 0.969 | 0.960 (1.000 / 0.922) | 0.594 | 0.865 |

Each fix is a general rule, covered by a test, and checked to leave the other samples unchanged.

1. **Burst attribution was too greedy.** The first version attributed every activity in a burst to
   a lone claimant and scored 12 of 17 burst links correct. It now attributes account deletions
   only, and only on a unique claim-consistent match: 12 of 12.
2. **Visual records took their time from printed dates.** A PDF that says "received 18 Jun" was
   treated as known on 18 June and fell outside the request window (sample case_07). A screenshot
   or PDF now shows its records as of its capture time. Printed labels such as "file" and
   "uploader" map to the evidence vocabulary.
3. **Visual transcriptions missed rows.** Each of the 15 files was viewed and compared with its
   extraction. Transcription-only corrections are kept in `code/cache/visual_verification.json`:
   a missing support record (`evidence_04`), page-2 attachment fields (`evidence_58`) and the
   eight-object listing in `evidence_46`.
4. **A cache miss hid the record it was looking up.** Log lines of one trace are merged, and a
   `cache_lookup` line's `result=miss` overrode the following `rows=1` / `http_status=200`, so
   present records looked deleted. A cache line's result no longer counts as the resource's
   result. In the official run, rows with an INCOMPLETE status and no finding dropped from 13 to
   3, and 24 rows changed. Retained audit records were no longer reported missing, and remaining
   support and analytics records surfaced.
5. **Catalog aliases were specified but not implemented.** The data catalog ties business
   references to resources ("export_1842 (NST-2026-0718-1842)",
   "object_19 (delivery_confirmation_19.pdf)"). A visual record that carries such an alias
   exactly is now an observation of that resource, known from the catalog activity on. Two
   official cases moved from an empty COMPLETE to a reported remaining record.
6. **A screen label crashed the policy lookup.** A visual record type such as "Customer key" was
   taken as a policy category. Only categories present in `deletion_rules.csv` count as stated.
7. **A healthy connector was not an active flow.** A connector shown as "Healthy" in continuous
   sync is now an enabled data flow.
8. **Uncitable violations lost their remediation** (found by custom_4). When a violation is known
   only from an item captured after the audit time, the finding is still left out, but the
   remediation step is kept, because remediation needs no evidence.
9. **SQL flags stored as 0/1 were read as false.** `restore_allowed INTEGER (0,1)` with value `1`
   meant "not restorable", so restore risks and active flows in SQL snapshots were missed (sample
   case_09). Flags now accept `1`, `true` and `yes`.
10. **A backup with no stated restore access was certified as safe** (official case_55). The
    subject's archive `backup_9287` is listed with their email in a recovery console
    (`evidence_32`), and no evidence says its restore access was blocked. The case came out
    COMPLETE with no steps, because the unresolved verification was omitted. A backup can be
    restored until something blocks it, so a present archive with no restore flag is now a
    restore risk, cited by the evidence that shows the archive. Official run: 1 row changed
    (case_55 COMPLETE with 0 steps to INCOMPLETE with finding, `BLOCK_RESTORE` and
    verification), COMPLETE 153 to 152. No sample has such a backup, so sample metrics are
    unchanged; the other 58 backups in the official run have a stated restore flag and are
    unaffected.
11. **A deleted account lost its verification when a later file said it was gone** (11 official
    COMPLETE rows). The account's absence was stated only by log lines inside a file captured
    after the audit time (for example `evidence_21`, captured 07-05 for a 07-03 audit), so it
    could not be cited, and the verification was left out. Yet an inventory snapshot captured
    17-23 minutes before the audit time (`evidence_81`, `evidence_93`, `evidence_72`) has the
    account service in scope and omits the account. That omission was used only when no explicit
    observation existed (the D10 rule, sample case_01). It now also cites an explicit absence that
    is not citable, if the snapshot was taken after the account was last seen present. Official
    run: 11 rows gain `VERIFY_RECORD_ABSENT` (cases 64, 69, 70, 213, 215, 218, 219, 221, 335, 336,
    339); no verdict changes. Samples: no output change. Sample case_09's account takes the same
    proof (`evidence_93`), but its row is INCOMPLETE, where verifications are uncited. Its gold
    cites `evidence_93` for the backup finding.
12. **Support attachments were redacted as messages** (13 official INCOMPLETE rows). A consistency
    audit compared each record's category with the deletion worker's policy action on it
    (`DELETE_RECORD`, `REDACT_RECORD`, `ANONYMIZE_RECORD`, `RETAIN_RECORD`). They agree on all 58
    sample records (the gold treatment) and on all 173 official records whose category the
    evidence states. The only disagreements were 14 support records that we inferred to be
    messages (REDACT) and that the worker deletes. Two general rules:
    - A log line's `file=` names the scanned file (`event=attachment_pii_scan ...
      file="delivery_confirmation_93.pdf"`), as the `file` label does on a screen, so the record
      is an attachment: 6 rows (cases 71, 119, 263, 371, 419, 515).
    - When no category is stated, the one policy action the worker applied to the record within
      the case's request-to-audit window picks among the categories its System can hold. Only the
      action is used, never its result: 7 rows (cases 47, 95, 143, 251, 287, 347, 443). case_34's
      action predates its request, so the case is unchanged.
    Each row's finding, remediation and verification change from REPORT_EXPOSED_PII / REDACT /
    REDACTED to REPORT_REMAINING_RECORD / DELETE / ABSENT, with the same cited evidence. Every
    official case that holds two support records now has one attachment and one message, as the
    three that were already explicit did. Samples: no output change.

### Remaining failures

- **Sample links not found (15 of 111 answer resources; all 96 links made are correct).** Each
  was traced to its cause (2026-10-25); none has a general fix that keeps precision at 1.000:
  - *In no participant file* (3): `account_004` (case_04), `audit_record_6645` (case_18) and
    `audit_record_5569` (case_20) appear in no evidence file, activity or visual extraction.
  - *Ambiguous account deletions* (5): the subject's account appears only as a deletion-worker
    activity in a shared report slot, and more than one claim-consistent deletion is left once
    the other claimants' known accounts are removed. Case_23 has 3 FAILED deletions for 4
    NOT_STARTED claimants. Cases 28 and 29 have 2 FAILED deletions for 3. For cases 31 and 32,
    the file that ties the rival claimants to their accounts (`evidence_03`) was captured on
    2026-10-01, after their 2026-09-15 audit time, so it may not be used.
  - *Identifier-less anonymized analytics* (7): cases 03, 04, 21, 25, 26, 27 and 28. These records
    carry no identifier at all, so the only link is an `ANONYMIZE_RECORD` activity in a report
    slot. Slots hold 2 such activities for 2 or more claimants (cases 03, 25/26, 27/28). The
    result does not follow the claim, and `analytics_6077` (SUCCESS) belongs to NOT_STARTED
    case_28. In case_21 there is one activity for 6 claimants, and not every subject has an
    analytics record. Case_04's `analytics_901` also falls inside another slot's retry window.
  - Every one of the 13 sample cases without an exact step list is one of these cases; where all
    links are found, the steps and their order match exactly.
- **Sample case_04** is COMPLETE in the answer but predicted INCOMPLETE with no steps: both of
  its resources are in the lists above.
- **Official case_126**: the remaining account is known only from a file captured after the audit
  time, so it is INCOMPLETE with remediation and verification but no finding. This follows the
  two-clock rule that the sample answers confirm (sample case_09 verifies `audit_record_4902`,
  known from a later-captured file, without a citation).
