"""Tests for the best-of-N Cursor Cloud Agents runner.

Every test is offline: the Cursor API, git and Telegram are all replaced with
``unittest.mock`` doubles, and the two network entry points used by
``scripts/queue_manager.py`` (``subprocess.run`` and ``urllib.request.urlopen``)
are patched to raise so an accidental real call fails loudly.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from scripts import best_of_n as bon
from scripts import queue_manager as qm


# --------------------------------------------------------------------------- #
# helpers


@pytest.fixture(autouse=True)
def no_network():
    """Fail loudly if anything tries to reach the network or shell out."""

    def _boom(*args, **kwargs):  # pragma: no cover - only hit on regressions
        raise AssertionError(f"unexpected network/subprocess call: {args} {kwargs}")

    with mock.patch("urllib.request.urlopen", side_effect=_boom), mock.patch(
        "scripts.queue_manager.subprocess.run", side_effect=_boom
    ):
        yield


def make_stats(**kwargs) -> bon.DiffStats:
    defaults = dict(
        files_changed=1,
        insertions=10,
        deletions=0,
        test_files=0,
        new_src_files=0,
        files=["scripts/thing.py"],
    )
    defaults.update(kwargs)
    return bon.DiffStats(**defaults)


def make_variant_result(index: int, **kwargs) -> bon.VariantResult:
    payload = dict(
        index=index,
        model=f"model-{index}",
        branch=bon.variant_branch_name("demo-task", index),
        agent_id=f"agent-{index}",
        pr_url=f"https://github.com/acme/repo/pull/{index}",
        score=0.5,
        verdict="ok",
        latency_s=10.0,
        state=bon.STATE_COMPLETED,
    )
    payload.update(kwargs)
    return bon.VariantResult(**payload)


def make_result(n: int, **kwargs) -> bon.BestOfNResult:
    variants = [make_variant_result(i + 1) for i in range(n)]
    result = bon.BestOfNResult(
        task_slug="demo-task",
        variants=variants,
        winner=variants[0],
        judge_model="MiniMax-M3.1-Flash",
        created_at="2026-10-21T06:00:00+00:00",
        judge_mode=bon.JUDGE_MODE_LLM,
    )
    for key, value in kwargs.items():
        setattr(result, key, value)
    return result


# --------------------------------------------------------------------------- #
# 1-4. judge response parsing


def test_parse_judge_response_plain_json():
    verdict = bon.parse_judge_response(
        '{"score": 0.82, "verdict": "solid", "reasons": ["tests added"]}'
    )
    assert verdict.score == pytest.approx(0.82)
    assert verdict.verdict == "solid"
    assert verdict.reasons == ["tests added"]
    assert verdict.error is None


def test_parse_judge_response_fenced_json_block():
    reply = (
        "Here is my grading.\n"
        "```json\n"
        '{"score": 0.4, "verdict": "partial", "reasons": ["no tests", "narrow"]}\n'
        "```\n"
    )
    verdict = bon.parse_judge_response(reply)
    assert verdict.score == pytest.approx(0.4)
    assert verdict.verdict == "partial"
    assert verdict.reasons == ["no tests", "narrow"]
    assert verdict.error is None


def test_parse_judge_response_json_embedded_in_prose():
    reply = (
        "After reviewing the diff I would say "
        '{"score": 0.95, "verdict": "meets every criterion", "reasons": ["all good"]} '
        "and that is my final answer."
    )
    verdict = bon.parse_judge_response(reply)
    assert verdict.score == pytest.approx(0.95)
    assert verdict.verdict == "meets every criterion"
    assert verdict.error is None


@pytest.mark.parametrize(
    "garbage",
    [
        "I refuse to answer.",
        "",
        "{not even json",
        '{"verdict": "no score field"}',
        '{"score": "not-a-number"}',
    ],
)
def test_parse_judge_response_garbage_yields_none_score_without_raising(garbage):
    verdict = bon.parse_judge_response(garbage)
    assert verdict.score is None
    assert verdict.error
    assert verdict.raw == garbage


def test_judge_transport_failure_does_not_propagate():
    judge = bon.LLMJudge(
        "some-model",
        transport=mock.Mock(side_effect=RuntimeError("socket closed")),
    )
    verdict = judge.evaluate(
        bon.TaskSpec(slug="demo"), make_variant_result(1), make_stats()
    )
    assert verdict.score is None
    assert "judge call failed" in (verdict.error or "")


def test_judge_unavailable_when_no_transport():
    judge = bon.LLMJudge("some-model", transport=None, auto_discover=False)
    assert judge.available is False
    verdict = judge.evaluate(bon.TaskSpec(slug="demo"), make_variant_result(1), None)
    assert verdict.score is None
    assert verdict.error == "judge transport unavailable"


# --------------------------------------------------------------------------- #
# 5. deterministic fallback scoring


def test_fallback_weights_are_documented_and_normalised():
    total = (
        bon.FALLBACK_WEIGHT_TESTS
        + bon.FALLBACK_WEIGHT_NEW_SRC
        + bon.FALLBACK_WEIGHT_FILES_CHANGED
        + bon.FALLBACK_WEIGHT_LINE_VOLUME
    )
    assert total == pytest.approx(1.0)

    verdict = bon.score_variant_fallback(make_stats(test_files=1))
    # every component reports the weight it was scored with
    assert len(verdict.reasons) == 4
    assert all("weight" in reason for reason in verdict.reasons)


def test_fallback_scores_variant_with_tests_above_variant_without():
    with_tests = bon.score_variant_fallback(
        make_stats(
            files_changed=2,
            insertions=40,
            test_files=1,
            files=["scripts/thing.py", "tests/test_thing.py"],
        )
    )
    without_tests = bon.score_variant_fallback(
        make_stats(files_changed=2, insertions=40, test_files=0)
    )
    assert with_tests.score > without_tests.score
    assert with_tests.score - without_tests.score == pytest.approx(
        bon.FALLBACK_WEIGHT_TESTS
    )
    assert with_tests.mode == bon.JUDGE_MODE_FALLBACK


def test_fallback_score_is_zero_when_diff_unavailable():
    verdict = bon.score_variant_fallback(
        bon.DiffStats(available=False, error="branch missing")
    )
    assert verdict.score == 0.0
    assert verdict.mode == bon.JUDGE_MODE_FALLBACK


def test_parse_numstat_detects_tests_and_new_src_files():
    numstat = (
        "12\t3\tsrc/pkg/new_mod.py\n"
        "40\t0\ttests/test_new_mod.py\n"
        "5\t5\tscripts/queue_manager.py\n"
    )
    stats = bon.parse_numstat(
        numstat, added_files=["src/pkg/new_mod.py", "tests/test_new_mod.py"]
    )
    assert stats.files_changed == 3
    assert stats.insertions == 57
    assert stats.deletions == 8
    assert stats.test_files == 1
    assert stats.new_src_files == 1
    assert stats.touches_tests is True


# --------------------------------------------------------------------------- #
# 6. branch naming


def test_variant_branch_names_are_slugged_and_unique():
    variants = bon.build_variants(["m1", "m2", "m3"], n=3)
    branches = [bon.variant_branch_name("Task 14: Best of N!", v.index) for v in variants]
    assert branches == [
        "cursor/task-14-best-of-n-variant-1",
        "cursor/task-14-best-of-n-variant-2",
        "cursor/task-14-best-of-n-variant-3",
    ]
    assert len(set(branches)) == 3
    assert [v.index for v in variants] == [1, 2, 3]


def test_build_variants_cycles_models_when_n_exceeds_model_list():
    variants = bon.build_variants(["a", "b"], n=3)
    assert [v.model for v in variants] == ["a", "b", "a"]


# --------------------------------------------------------------------------- #
# 7. winner selection


def test_winner_tie_breaks_on_lower_latency_then_index():
    variants = [
        make_variant_result(1, score=0.7, latency_s=300.0),
        make_variant_result(2, score=0.7, latency_s=120.0),
        make_variant_result(3, score=0.5, latency_s=10.0),
    ]
    winner = bon.select_winner(variants)
    assert winner is not None
    assert winner.index == 2

    # fully tied variants fall back to the lower index, never randomness
    tied = [
        make_variant_result(2, score=0.7, latency_s=120.0),
        make_variant_result(1, score=0.7, latency_s=120.0),
    ]
    assert bon.select_winner(tied).index == 1


def test_no_winner_when_every_score_is_none():
    variants = [make_variant_result(i, score=None) for i in (1, 2)]
    assert bon.select_winner(variants) is None


# --------------------------------------------------------------------------- #
# 8. report


def test_write_report_matches_schema(tmp_path: Path):
    result = make_result(3)
    json_path, md_path = bon.write_report(
        result, report_dir=tmp_path, timestamp="20261021T060000Z"
    )

    assert json_path.name == "demo-task_20261021T060000Z.json"
    assert md_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["task_slug"] == "demo-task"
    assert payload["n"] == 3
    assert payload["judge_model"] == "MiniMax-M3.1-Flash"
    assert payload["judge_mode"] in ("llm", "fallback")
    assert payload["created_at"]
    assert payload["winner"] == {
        "index": 1,
        "model": "model-1",
        "pr_url": "https://github.com/acme/repo/pull/1",
    }
    assert [v["index"] for v in payload["variants"]] == [1, 2, 3]
    for variant in payload["variants"]:
        assert set(
            [
                "index",
                "model",
                "branch",
                "agent_id",
                "pr_url",
                "score",
                "verdict",
                "latency_s",
                "state",
                "error",
            ]
        ).issubset(variant)

    markdown = md_path.read_text(encoding="utf-8")
    assert "# Best-of-3: demo-task" in markdown
    for variant in result.variants:
        assert variant.branch in markdown


# --------------------------------------------------------------------------- #
# 9. telegram digest


def test_digest_is_silent_for_single_variant():
    sender = mock.Mock()
    result = make_result(1)
    assert bon.send_telegram_digest(result, sender=sender) is False
    sender.send_message.assert_not_called()


def test_digest_sends_exactly_one_message_for_two_variants():
    sender = mock.Mock()
    result = make_result(2)
    assert bon.send_telegram_digest(result, sender=sender) is True
    sender.send_message.assert_called_once()

    text = sender.send_message.call_args.args[0]
    lines = text.splitlines()
    assert "winner" in lines[0].lower()
    assert "variant 1" in lines[0]
    for variant in result.variants:
        assert any(line.startswith(f"{variant.index}. ") for line in lines)


def test_digest_not_sent_without_winner():
    sender = mock.Mock()
    result = make_result(2, winner=None)
    assert bon.send_telegram_digest(result, sender=sender) is False
    sender.send_message.assert_not_called()


def test_digest_skipped_when_telegram_unconfigured():
    result = make_result(2)
    assert (
        bon.send_telegram_digest(result, sender_factory=lambda: None) is False
    )


# --------------------------------------------------------------------------- #
# 10. prompt content + end-to-end run with mocked network


def test_variant_prompt_carries_marker_model_and_acceptance_criteria():
    spec = bon.TaskSpec(
        slug="demo-task",
        prompt="Add a widget.",
        acceptance_criteria=["pytest green", "no new deps"],
    )
    runner = bon.BestOfNRunner(
        queue_manager=SimpleNamespace(),
        worktree_orchestrator=mock.Mock(),
        judge=bon.LLMJudge("judge", transport=None, auto_discover=False),
        notify=False,
    )
    prompt = runner.build_variant_prompt(
        spec, bon.VariantSpec(index=2, model="gpt-5.6-sol-high"), 3, "cursor/demo-task-variant-2"
    )
    assert "variant: 2/3" in prompt
    assert "model: gpt-5.6-sol-high" in prompt
    assert "Acceptance criteria:" in prompt
    assert "- pytest green" in prompt
    assert "cursor/demo-task-variant-2" in prompt


def test_run_launches_one_agent_per_variant_and_reports(tmp_path: Path):
    spec = bon.TaskSpec(
        slug="demo-task",
        prompt="Add a widget.",
        acceptance_criteria=["pytest green"],
    )
    variants = bon.build_variants(
        ["claude-opus-5-thinking-high", "gpt-5.6-sol-high", "gemini-3.7-flash-high"]
    )

    launched: dict[str, str] = {}

    def fake_launch(branch, prompt, model=None):
        agent_id = f"agent-{len(launched) + 1}"
        launched[agent_id] = branch
        return {"agent_id": agent_id, "branch": branch, "status": "LAUNCHED"}

    def fake_poll(running, tracker):
        return {
            agent_id: qm.PollResult(
                status="FINISHED",
                pr_urls=[f"https://github.com/acme/repo/pull/{agent_id[-1]}"],
            )
            for agent_id in running
        }

    judge_replies = iter(
        [
            '{"score": 0.4, "verdict": "thin", "reasons": ["no tests"]}',
            "```json\n{\"score\": 0.9, \"verdict\": \"great\", \"reasons\": [\"tests\"]}\n```",
            "nonsense, I will not grade this",
        ]
    )
    transport = mock.Mock(side_effect=lambda **kwargs: next(judge_replies))
    sender = mock.Mock()
    worktrees = mock.Mock()
    worktrees.create_worktree.side_effect = lambda branch, base_ref=None: (
        tmp_path / branch.replace("/", "_")
    )

    with mock.patch.object(qm, "launch_agent_prompt", side_effect=fake_launch), \
            mock.patch.object(qm, "poll_running_agents_batch", side_effect=fake_poll):
        runner = bon.BestOfNRunner(
            queue_manager=qm,
            worktree_orchestrator=worktrees,
            judge=bon.LLMJudge("MiniMax-M3.1-Flash", transport=transport),
            diff_stats_fn=lambda branch, base_ref=None: make_stats(
                test_files=1 if branch.endswith("2") else 0
            ),
            report_dir=tmp_path / "best_of_N",
            telegram_sender=sender,
            sleeper=lambda _s: None,
        )
        result = runner.run(spec, variants)

    assert len(launched) == 3
    assert sorted(launched.values()) == [
        "cursor/demo-task-variant-1",
        "cursor/demo-task-variant-2",
        "cursor/demo-task-variant-3",
    ]
    assert [v.state for v in result.variants] == [bon.STATE_COMPLETED] * 3
    assert [v.index for v in result.variants] == [1, 2, 3]

    # variants 1 and 2 were graded by the judge, variant 3 fell back
    assert result.variants[0].judge_mode == bon.JUDGE_MODE_LLM
    assert result.variants[1].judge_mode == bon.JUDGE_MODE_LLM
    assert result.variants[2].judge_mode == bon.JUDGE_MODE_FALLBACK
    assert result.variants[2].error
    assert result.judge_mode == bon.JUDGE_MODE_LLM

    assert result.winner is not None and result.winner.index == 2
    assert result.report_path and Path(result.report_path).exists()
    sender.send_message.assert_called_once()


def test_run_marks_launch_failures_and_still_reports(tmp_path: Path):
    spec = bon.TaskSpec(slug="demo-task", prompt="Add a widget.")
    variants = bon.build_variants(["m1", "m2"])

    def fake_launch(branch, prompt, model=None):
        if branch.endswith("1"):
            return {"agent_id": None, "branch": branch, "status": "LIMIT_REACHED"}
        return {"agent_id": "agent-2", "branch": branch, "status": "LAUNCHED"}

    def fake_poll(running, tracker):
        return {
            agent_id: qm.PollResult(status="FAILED", error_message="agent crashed")
            for agent_id in running
        }

    with mock.patch.object(qm, "launch_agent_prompt", side_effect=fake_launch), \
            mock.patch.object(qm, "poll_running_agents_batch", side_effect=fake_poll):
        runner = bon.BestOfNRunner(
            queue_manager=qm,
            worktree_orchestrator=mock.Mock(),
            judge=bon.LLMJudge("judge", transport=None, auto_discover=False),
            diff_stats_fn=lambda branch, base_ref=None: bon.DiffStats(
                available=False, error="no such branch"
            ),
            report_dir=tmp_path / "best_of_N",
            notify=False,
            sleeper=lambda _s: None,
        )
        result = runner.run(spec, variants)

    assert result.variants[0].state == bon.STATE_LAUNCH_FAILED
    assert result.variants[1].state == bon.STATE_FAILED
    assert result.judge_mode == bon.JUDGE_MODE_FALLBACK
    assert all(v.score == 0.0 for v in result.variants)
    assert Path(result.report_path).exists()


def test_run_rejects_duplicate_variant_indexes():
    runner = bon.BestOfNRunner(
        queue_manager=SimpleNamespace(),
        worktree_orchestrator=mock.Mock(),
        judge=bon.LLMJudge("judge", transport=None, auto_discover=False),
        notify=False,
    )
    with pytest.raises(ValueError):
        runner.run(
            bon.TaskSpec(slug="demo"),
            [bon.VariantSpec(index=1, model="a"), bon.VariantSpec(index=1, model="b")],
        )


# --------------------------------------------------------------------------- #
# CLI surface


def test_queue_manager_run_help_exposes_best_of_flag(capsys):
    with mock.patch("sys.argv", ["queue_manager.py", "run", "--help"]):
        with pytest.raises(SystemExit):
            qm.main()
    out = capsys.readouterr().out
    assert "--best-of" in out
    assert "--best-of-models" in out
    assert "--best-of-judge-model" in out


def test_queue_manager_single_agent_mode_is_unchanged():
    with mock.patch("sys.argv", ["queue_manager.py", "run", "--branches", "foo"]), \
            mock.patch.object(qm, "run_queue", return_value=[]) as run_queue:
        qm.main()
    run_queue.assert_called_once()
    assert run_queue.call_args.args[0] == ["foo"]


def test_queue_manager_best_of_two_dispatches_to_best_of_runner():
    argv = [
        "queue_manager.py",
        "run",
        "--best-of",
        "2",
        "--task-slug",
        "demo-task",
        "--task-prompt",
        "Add a widget.",
    ]
    with mock.patch("sys.argv", argv), mock.patch.object(
        bon, "run_best_of_n", return_value=None
    ) as runner:
        with pytest.raises(SystemExit) as excinfo:
            qm.main()
    assert excinfo.value.code == 0
    runner.assert_called_once()
    assert runner.call_args.kwargs["n"] == 2
    assert runner.call_args.kwargs["task_slug"] == "demo-task"


# --------------------------------------------------------------------------- #
# task spec loading


def test_task_spec_from_yaml_reads_acceptance_criteria(tmp_path: Path):
    spec_file = tmp_path / "widget_task.yaml"
    spec_file.write_text(
        "name: Widget Task\n"
        "type: script\n"
        "description: Build the widget\n"
        "prompt: Implement scripts/widget.py\n"
        "acceptance_criteria:\n"
        "  - pytest green\n"
        "  - no new dependencies\n",
        encoding="utf-8",
    )
    spec = bon.TaskSpec.from_yaml(spec_file)
    assert spec.slug == "widget-task"
    assert spec.description == "Build the widget"
    assert spec.acceptance_criteria == ["pytest green", "no new dependencies"]
    assert spec.prompt == "Implement scripts/widget.py"


def test_default_models_prefers_env_override(monkeypatch):
    monkeypatch.setenv("OPENCLAW_BEST_OF_MODELS", "a, b c")
    assert bon.default_models() == ["a", "b", "c"]
    monkeypatch.delenv("OPENCLAW_BEST_OF_MODELS")
    assert bon.default_models({}) == list(bon.DEFAULT_VARIANT_MODELS)
