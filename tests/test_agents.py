import json
import subprocess
import sys
from pathlib import Path

import pytest

from photolab import agents
from photolab.agents import AgentFailed, AgentUnavailable, CliResult, Clis, CodexRun
from tests.conftest import THREAD, FakeCli, codex_events, codex_failure

SCHEMA = {"type": "object", "properties": {"caption": {"type": "string"}}}


def flag_values(command: list[str], flag: str) -> list[str]:
    return [command[i + 1] for i, part in enumerate(command) if part == flag]


def test_run_cli_passes_the_prompt_on_stdin_and_reports_the_exit(tmp_path):
    script = "import sys; print(sys.stdin.read().upper()); sys.exit(3)"

    result = agents.run_cli([sys.executable, "-c", script], "hello", tmp_path)

    assert result.returncode == 3
    assert result.stdout.strip() == "HELLO"


def test_run_cli_hides_api_keys_so_a_subscription_is_what_gets_used(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    script = (
        "import os; print(os.environ.get('ANTHROPIC_API_KEY'), os.environ.get('OPENAI_API_KEY'))"
    )

    result = agents.run_cli([sys.executable, "-c", script], "", tmp_path)

    assert result.stdout.strip() == "None None"


def test_run_cli_reports_a_timeout_as_one_failed_request(tmp_path, monkeypatch):
    def too_slow(*_, **__):
        raise subprocess.TimeoutExpired("codex", 1)

    monkeypatch.setattr(subprocess, "run", too_slow)

    with pytest.raises(AgentFailed, match="took over"):
        agents.run_cli(["codex"], "", tmp_path)


def test_run_cli_reports_a_missing_command_as_a_stopped_run(tmp_path):
    with pytest.raises(AgentUnavailable, match="could not start"):
        agents.run_cli(["no-such-command-here"], "", tmp_path)


def test_default_codex_home_honours_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert agents.default_codex_home() == tmp_path
    monkeypatch.delenv("CODEX_HOME")
    assert agents.default_codex_home() == Path.home() / ".codex"


def test_can_make_images_needs_codex(tmp_path):
    assert FakeCli(tmp_path, ("codex",)).clis().can_make_images
    assert not FakeCli(tmp_path, ("claude",)).clis().can_make_images


def test_available_backends_lists_what_is_here(settings, tmp_path):
    assert agents.available_backends(settings, FakeCli(tmp_path).clis()) == [
        "codex",
        "claude",
        "api",
    ]
    settings.anthropic_api_key = ""
    assert agents.available_backends(settings, FakeCli(tmp_path, ()).clis()) == []


def test_default_backend_keeps_the_configured_one_when_it_is_here(settings, tmp_path):
    settings.annotation_backend = "claude"

    assert agents.default_backend(settings, FakeCli(tmp_path).clis()) == "claude"


def test_default_backend_falls_through_to_what_is_here(settings, tmp_path):
    settings.annotation_backend = "codex"

    assert agents.default_backend(settings, FakeCli(tmp_path, ("claude",)).clis()) == "claude"


def test_default_backend_stays_put_when_nothing_is_here(settings, tmp_path):
    settings.annotation_backend = "codex"
    settings.anthropic_api_key = ""

    assert agents.default_backend(settings, FakeCli(tmp_path, ()).clis()) == "codex"


def test_resolve_model_prefers_the_request_then_the_setting_then_the_default(settings):
    settings.annotation_backend, settings.annotation_model = "codex", "gpt-custom"

    assert agents.resolve_model(settings, "codex", "gpt-other") == "gpt-other"
    assert agents.resolve_model(settings, "codex") == "gpt-custom"


def test_resolve_model_does_not_hand_one_backends_model_to_another(settings):
    settings.annotation_backend, settings.annotation_model = "codex", "gpt-custom"

    assert agents.resolve_model(settings, "claude") == "haiku"


@pytest.mark.parametrize(
    "message",
    [
        "You've hit your usage limit",
        "Rate limit reached",
        "Not logged in",
        "The 'x' model is not supported when using Codex with a ChatGPT account.",
    ],
)
def test_classify_failure_stops_a_run_for_what_every_call_would_hit(message):
    assert isinstance(agents.classify_failure(message), AgentUnavailable)


def test_classify_failure_keeps_anything_else_to_one_request():
    assert isinstance(agents.classify_failure("the stream was cut"), AgentFailed)


def test_codex_command_inherits_nothing_of_the_owners_setup(tmp_path):
    command = agents.codex_command("gpt-x", tmp_path)

    # The owner's own config runs with danger-full-access, so these are not optional.
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert flag_values(command, "--sandbox") == ["read-only"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


def test_codex_command_removes_the_tools_a_photo_could_try_to_use(tmp_path):
    disabled = flag_values(agents.codex_command("gpt-x", tmp_path), "--disable")

    assert {"shell_tool", "apps", "plugins", "browser_use", "computer_use"} <= set(disabled)


def test_codex_command_reads_its_prompt_from_stdin(tmp_path):
    command = agents.codex_command("gpt-x", tmp_path, ["--image", "a.jpg"])

    assert command[-1] == "-"
    assert flag_values(command, "--model") == ["gpt-x"]
    assert flag_values(command, "--image") == ["a.jpg"]


def test_parse_codex_events_keeps_the_last_message():
    run = agents.parse_codex_events(codex_events("final"))

    assert (run.thread_id, run.message, run.error) == (THREAD, "final", "")


def test_parse_codex_events_prefers_the_turns_own_failure():
    run = agents.parse_codex_events(codex_failure("no such model"))

    assert run.error == "no such model"


def test_parse_codex_events_survives_lines_that_are_not_json():
    assert agents.parse_codex_events("warning: something\n" + codex_events("ok")).message == "ok"


def test_parse_codex_events_treats_a_warning_item_as_no_answer():
    # Observed for real: an `error` *item* is only a warning, and the run carries on.
    line = json.dumps({"type": "item.completed", "item": {"type": "error", "message": "hm"}})

    assert agents.parse_codex_events(line) == CodexRun()


def test_codex_json_sends_the_photo_and_schema_and_no_image_tool(fake_cli):
    fake_cli.result = CliResult(0, codex_events('{"caption": "a"}'))

    answer = agents.codex_json("system", "prompt", SCHEMA, "gpt-x", b"jpeg", fake_cli.clis())

    assert answer == '{"caption": "a"}'
    assert fake_cli.files == [["photo.jpg", "schema.json"]]
    assert fake_cli.stdin == ["system\n\nprompt"]
    assert "image_generation" in flag_values(fake_cli.calls[0], "--disable")


def test_codex_json_without_a_photo_attaches_none(fake_cli):
    agents.codex_json("system", "prompt", SCHEMA, "gpt-x", None, fake_cli.clis())

    assert fake_cli.files == [["schema.json"]]
    assert "--image" not in fake_cli.calls[0]


def test_codex_json_refuses_an_empty_answer(fake_cli):
    fake_cli.result = CliResult(0, "")

    with pytest.raises(AgentFailed, match="no answer"):
        agents.codex_json("s", "p", SCHEMA, "m", None, fake_cli.clis())


def test_codex_json_raises_what_the_turn_reported(fake_cli):
    fake_cli.result = CliResult(1, codex_failure("You've hit your usage limit"))

    with pytest.raises(AgentUnavailable, match="usage limit"):
        agents.codex_json("s", "p", SCHEMA, "m", None, fake_cli.clis())


def test_codex_json_falls_back_to_stderr_for_a_crash(fake_cli):
    fake_cli.result = CliResult(101, "", "panicked")

    with pytest.raises(AgentFailed, match="panicked"):
        agents.codex_json("s", "p", SCHEMA, "m", None, fake_cli.clis())


def test_codex_image_returns_the_image_and_leaves_no_copy_behind(fake_cli):
    fake_cli.make_image = True

    image = agents.codex_image("draw", [b"one", b"two"], "gpt-x", fake_cli.clis())

    assert image.startswith(b"\x89PNG")
    assert fake_cli.files == [["image-1.jpg", "image-2.jpg"]]
    assert not (fake_cli.home / "generated_images" / THREAD).exists()


def test_codex_image_attaches_references_in_order(fake_cli):
    fake_cli.make_image = True

    agents.codex_image("draw", [b"one", b"two"], "gpt-x", fake_cli.clis())

    assert [Path(item).name for item in flag_values(fake_cli.calls[0], "--image")] == [
        "image-1.jpg",
        "image-2.jpg",
    ]


def test_a_run_that_makes_no_image_reports_what_the_model_said(fake_cli):
    fake_cli.result = CliResult(0, codex_events("I can't help with that."))

    with pytest.raises(AgentFailed, match="can't help"):
        agents.codex_image("draw", [], "gpt-x", fake_cli.clis())


def test_take_generated_image_never_trusts_a_session_id_as_a_path(tmp_path):
    victim = tmp_path / "keep"
    victim.mkdir()

    with pytest.raises(AgentFailed, match="session"):
        agents.take_generated_image(CodexRun(thread_id="../../keep"), tmp_path / "home")

    assert victim.exists()


def test_claude_command_with_a_photo_allows_only_reading(tmp_path):
    command = agents.claude_command("system", SCHEMA, "haiku", read=True)

    assert flag_values(command, "--tools") == ["Read"]
    assert flag_values(command, "--allowedTools") == ["Read"]
    assert "--strict-mcp-config" in command
    assert "--no-session-persistence" in command


def test_claude_command_without_a_photo_allows_nothing():
    command = agents.claude_command("system", SCHEMA, "haiku", read=False)

    assert flag_values(command, "--tools") == [""]
    assert "--allowedTools" not in command


def claude_answer(**fields) -> str:
    return json.dumps({"is_error": False, "subtype": "success", **fields})


def test_claude_json_returns_the_structured_output(fake_cli):
    fake_cli.result = CliResult(0, claude_answer(structured_output={"caption": "a"}))

    answer = agents.claude_json("s", "annotate", SCHEMA, "haiku", b"jpeg", fake_cli.clis())

    assert json.loads(answer) == {"caption": "a"}
    assert fake_cli.files == [["photo.jpg"]]
    assert "./photo.jpg" in fake_cli.stdin[0]


def test_claude_json_without_a_photo_names_no_file(fake_cli):
    fake_cli.result = CliResult(0, claude_answer(structured_output={}))

    agents.claude_json("s", "choose", SCHEMA, "haiku", None, fake_cli.clis())

    assert fake_cli.stdin == ["choose"]


def test_claude_json_reports_an_error_result(fake_cli):
    fake_cli.result = CliResult(1, claude_answer(is_error=True, result="Rate limit reached"))

    with pytest.raises(AgentUnavailable, match="Rate limit"):
        agents.claude_json("s", "p", SCHEMA, "haiku", None, fake_cli.clis())


def test_claude_json_refuses_an_unstructured_answer(fake_cli):
    fake_cli.result = CliResult(0, claude_answer(result="just text"))

    with pytest.raises(AgentFailed, match="no structured answer"):
        agents.claude_json("s", "p", SCHEMA, "haiku", None, fake_cli.clis())


def test_claude_json_reports_output_it_cannot_read(fake_cli):
    fake_cli.result = CliResult(1, "", "Not logged in")

    with pytest.raises(AgentUnavailable, match="Not logged in"):
        agents.claude_json("s", "p", SCHEMA, "haiku", None, fake_cli.clis())


def test_clis_defaults_to_the_real_thing():
    assert Clis().run is agents.run_cli
