"""One timed Gemini call with SDK retries off and the run's timeout, to see latency or the real
error. Never prints the key. Run from the repository root:
    .venv\\Scripts\\python -B code\\tools\\probe_gemini.py           (intent model)
    .venv\\Scripts\\python -B code\\tools\\probe_gemini.py visual    (visual model)"""

import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "code"))

import httpx  # noqa: E402
from google import genai  # noqa: E402
from google.genai import errors, types  # noqa: E402

from deletion_audit.settings import Settings  # noqa: E402

settings = Settings.from_environment(REPO_ROOT)
visual = sys.argv[1:] == ["visual"]
model = settings.visual_model if visual else settings.intent_model
timeout = settings.visual_timeout if visual else settings.intent_timeout
print(f"provider={settings.provider} key_present={settings.has_api_key} model={model} timeout={timeout:.0f}s")
client = genai.Client(api_key=settings.api_key,
                      http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1),
                                                     timeout=int(timeout * 1000)))
started = time.time()
try:
    reply = client.models.generate_content(
        model=model, contents=['Reply with the JSON {"ok": true}'],
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           thinking_config=types.ThinkingConfig(thinking_level="LOW")))
    print(f"OK in {time.time() - started:.1f}s: {reply.text[:80]}")
except errors.APIError as error:
    print(f"APIError {error.code} after {time.time() - started:.1f}s: {str(error.message)[:500]}")
except httpx.TimeoutException:
    print(f"TIMEOUT: no answer within {timeout:.0f}s")
