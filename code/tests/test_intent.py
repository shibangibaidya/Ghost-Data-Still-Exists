from pathlib import Path

from deletion_audit.intent import classify_intents, normalize_text
from deletion_audit.llm import LlmClient, TransportReply, Usage


class ScriptedTransport:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, request):
        self.texts.append(request.text)
        intent = "NOT_APPLICABLE" if "not asking" in request.text else "DELETION"
        return TransportReply({"intent": intent, "request_type": "ACCOUNT_DELETION", "language": "en"},
                              Usage(50, 10, 20), "fake-model", "STOP")


def test_each_distinct_text_is_classified_once(workdir: Path) -> None:
    transport = ScriptedTransport()
    client = LlmClient(workdir, transport)
    texts = ["Please delete my account.", "Please   delete my account.", "I am not asking for deletion."]

    decisions = classify_intents(texts, client, model="fake-model")

    assert len(transport.texts) == 2
    assert decisions["Please delete my account."].has_deletion_intent is True
    assert decisions["Please   delete my account."].has_deletion_intent is True
    assert decisions["I am not asking for deletion."].has_deletion_intent is False


def test_normalization_keeps_meaning_but_not_spacing() -> None:
    assert normalize_text("  Supprimez mon   compte \n") == "Supprimez mon compte"


def test_prompt_and_answers_are_replayed_offline(workdir: Path) -> None:
    classify_intents(["Please delete my account."], LlmClient(workdir, ScriptedTransport()), model="fake-model")

    offline = classify_intents(["Please delete my account."], LlmClient(workdir, None), model="fake-model")

    assert offline["Please delete my account."].has_deletion_intent is True
