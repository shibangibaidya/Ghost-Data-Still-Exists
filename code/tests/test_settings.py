from pathlib import Path

from deletion_audit.settings import Settings


def write_env(root: Path, text: str) -> None:
    (root / ".env").write_text(text, encoding="utf-8")


def test_paths_resolve_from_repo_root(workdir: Path) -> None:
    settings = Settings.from_environment(workdir, environ={})

    assert settings.dataset_dir == workdir / "dataset"
    assert settings.output_path == workdir / "output.csv"
    assert settings.cache_dir == workdir / "code" / "cache" / "llm"
    assert settings.trace_dir == workdir / ".audit_traces"


def test_anthropic_defaults_when_provider_is_anthropic(workdir: Path) -> None:
    settings = Settings.from_environment(workdir, environ={"DELETION_AUDIT_PROVIDER": "anthropic"})

    assert settings.provider == "anthropic"
    assert settings.intent_model == "claude-haiku-5-5"
    assert settings.visual_model == "claude-opus-5-5"
    assert settings.has_api_key is False


def test_provider_defaults_to_gemini_which_built_the_shipped_cache(workdir: Path) -> None:
    settings = Settings.from_environment(workdir, environ={})

    assert settings.provider == "gemini"
    assert settings.intent_model == "gemini-3.5-flash-lite"
    assert settings.has_api_key is False


def test_dotenv_is_read_and_process_environment_wins(workdir: Path) -> None:
    write_env(
        workdir,
        "# comment\nDELETION_AUDIT_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-from-file\n"
        "DELETION_AUDIT_INTENT_MODEL=model-from-file\n",
    )

    settings = Settings.from_environment(
        workdir, environ={"DELETION_AUDIT_INTENT_MODEL": "model-from-env"}
    )

    assert settings.has_api_key is True
    assert settings.intent_model == "model-from-env"


def test_secret_never_appears_in_repr(workdir: Path) -> None:
    write_env(workdir, "DELETION_AUDIT_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-secret-value\n")

    settings = Settings.from_environment(workdir, environ={})

    assert "sk-secret-value" not in repr(settings)


def test_unknown_provider_is_rejected(workdir: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="DELETION_AUDIT_PROVIDER"):
        Settings.from_environment(workdir, environ={"DELETION_AUDIT_PROVIDER": "other"})


def test_gemini_defaults_to_flash_lite_for_intent_and_3_5_flash_for_visuals(workdir: Path) -> None:
    settings = Settings.from_environment(workdir, environ={"DELETION_AUDIT_PROVIDER": "gemini"})

    assert (settings.intent_model, settings.visual_model) == ("gemini-3.5-flash-lite", "gemini-3.5-flash")
    assert settings.min_interval == 4.0
    assert settings.timeouts == {"intent": 60.0, "visual_evidence": 180.0}


def test_timeouts_can_be_overridden(workdir: Path) -> None:
    settings = Settings.from_environment(workdir, environ={"DELETION_AUDIT_VISUAL_TIMEOUT": "240"})

    assert settings.timeouts == {"intent": 60.0, "visual_evidence": 240.0}
