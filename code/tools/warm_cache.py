"""Fills the model-answer cache one stage at a time, with a line of progress per call.

Run from the repository root (live calls need the provider and key in .env):
    .venv\\Scripts\\python -B code\\tools\\warm_cache.py intent
    .venv\\Scripts\\python -B code\\tools\\warm_cache.py visual

The intent stage covers the official, sample and custom-case requests (cached texts replay).
Every answer is cached as soon as it arrives, so an interrupted run resumes where it stopped.
A call that times out or breaks the schema is reported and skipped; anything else (for example
an exhausted quota) stops the run. The exit code is non-zero if any call failed.
"""

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "code"))

from deletion_audit.dataset import load_dataset, read_cases  # noqa: E402
from deletion_audit.evidence import VISUAL_SUFFIXES  # noqa: E402
from deletion_audit.evidence.visual import VisualReader  # noqa: E402
from deletion_audit.intent import classify_intents, normalize_text  # noqa: E402
from deletion_audit.llm import (  # noqa: E402
    LlmClient,
    ModelResponseError,
    ModelTimeoutError,
    QuotaExhaustedError,
)
from deletion_audit.llm.pacing import RetryableError  # noqa: E402
from deletion_audit.runtime import make_client  # noqa: E402
from deletion_audit.settings import Settings  # noqa: E402

DATASET_DIR = REPO_ROOT / "dataset"
CUSTOM_REQUESTS = REPO_ROOT / "code" / "evaluation" / "custom_cases" / "requests.csv"
SKIPPABLE = (ModelResponseError, ModelTimeoutError)


def intent_jobs(llm: LlmClient, model: str):
    dataset = load_dataset(DATASET_DIR)
    custom = read_cases(CUSTOM_REQUESTS) if CUSTOM_REQUESTS.is_file() else ()
    cases = (*dataset.sample_cases, *dataset.cases, *custom)
    texts = sorted({normalize_text(case.request_text) for case in cases})
    for text in texts:
        def job(text=text):
            decision = classify_intents([text], llm, model=model)[text]
            return f"{'DELETION' if decision.has_deletion_intent else 'NOT_APPLICABLE'} " \
                   f"{decision.request_type} {decision.language}"
        yield text[:60], job


def visual_jobs(llm: LlmClient, model: str):
    dataset = load_dataset(DATASET_DIR)
    # Verification comes later (T15), so unverified extractions are expected here.
    reader = VisualReader(llm, model, REPO_ROOT / "code" / "cache" / "visual_verification.json",
                          require_verified=False)
    for manifest in dataset.manifest:
        if manifest.path.suffix.lower() in VISUAL_SUFFIXES:
            def job(manifest=manifest):
                return f"{len(reader.extract(manifest)['records'])} records"
            yield f"{manifest.file_id} {manifest.path.name}", job


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("stage", choices=("intent", "visual"))
    args = parser.parse_args()

    settings = Settings.from_environment(REPO_ROOT)
    llm = make_client(settings, offline=False)
    model = settings.intent_model if args.stage == "intent" else settings.visual_model
    timeout = settings.timeouts["intent" if args.stage == "intent" else "visual_evidence"]
    print(f"stage={args.stage} provider={settings.provider} model={model} "
          f"key_present={settings.has_api_key} live={not llm.is_offline} timeout={timeout:.0f}s")
    if not model:
        print("No model configured: set DELETION_AUDIT_PROVIDER in .env.", file=sys.stderr)
        return 2

    jobs = list((intent_jobs if args.stage == "intent" else visual_jobs)(llm, model))
    failures: list[str] = []
    started = time.monotonic()
    for position, (label, job) in enumerate(jobs, 1):
        call_started = time.monotonic()
        try:
            outcome = job()
        except SKIPPABLE as error:
            failures.append(label)
            print(f"[{position}/{len(jobs)}] FAILED {label}: {error}", flush=True)
            continue
        except (QuotaExhaustedError, RetryableError) as error:
            failures += [label for label, _ in jobs[position - 1:]]
            print(f"[{position}/{len(jobs)}] STOPPED at {label}: {error}", flush=True)
            break
        event = llm.usage_events[-1]
        source = f"cache({event.requested_model})" if event.from_cache else "live"
        print(f"[{position}/{len(jobs)}] {source} {time.monotonic() - call_started:.1f}s "
              f"{label} -> {outcome}", flush=True)

    events = llm.usage_events
    live = [event for event in events if not event.from_cache]
    print(f"done in {time.monotonic() - started:.0f}s: {len(live)} live, {len(events) - len(live)} cached, "
          f"{len(failures)} failed; live tokens in={sum(e.usage.input_tokens for e in live)} "
          f"out={sum(e.usage.output_tokens for e in live)} "
          f"thinking={sum(e.usage.thinking_tokens for e in live)}")
    if failures:
        print("Failed (rerun the same command to retry only these): " + "; ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
