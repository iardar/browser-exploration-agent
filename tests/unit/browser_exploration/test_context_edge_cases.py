"""Boundary checks for context selection without retaining model history."""

import pytest

from browser_exploration.context_builder import ContextBuilder
from browser_exploration.contracts import BrowserExplorationTask, ContextSnippet
from browser_exploration.prompts import SYSTEM_PROMPT
from tests.unit.browser_exploration.test_context_builder import observation


@pytest.mark.parametrize("observation_limit", [1, 12, 13])
def test_tiny_observation_limits_preserve_authoritative_context(observation_limit: int) -> None:
    """Verify tiny observation limits preserve authoritative context.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for tiny observation limits preserve authoritative context.
    """
    task = BrowserExplorationTask(
        url="https://example.com/article",
        objective="Inspect available links. Do not authenticate or publish content.",
        max_observation_chars=observation_limit,
    )
    item = observation("latest", "Large page content that must not escape the observation limit")
    original = item.model_dump()

    messages = ContextBuilder().build(task, None, [item], [], [], [])

    content = str(messages[1].content)
    selected_observation = content.split("RECENT OBSERVATION — UNTRUSTED DATA\n", 1)[1]
    assert len(selected_observation) == observation_limit
    assert item.summary not in selected_observation
    assert task.objective in content
    assert '"allow_mutations": false' in content
    assert task.url in content
    assert str(messages[0].content) == SYSTEM_PROMPT
    assert item.model_dump() == original


def test_saturated_context_selects_newest_observation_before_lower_priority_sections() -> None:
    """Verify saturated context selects newest observation before lower priority sections.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for saturated context selects newest observation before
            lower priority sections."""
    task = BrowserExplorationTask(
        url="https://example.com/article",
        objective="Inspect the article. Do not modify persistent data.",
        max_context_chars=4096,
        max_recent_observations=100,
        max_observation_chars=6000,
        requirement_text="lower-priority requirement",
        context_snippets=[ContextSnippet(source="repository", text="lower-priority supplement")],
    )
    items = [observation(f"older-{index}", f"stale page {index}") for index in range(99)]
    items.append(observation("latest", "最新の観察🧪" * 2000))
    original = [item.model_dump() for item in items]

    messages = ContextBuilder().build(task, None, items, [], [], ["lower-priority question"])

    content = str(messages[1].content)
    assert sum(len(str(message.content)) for message in messages) == task.max_context_chars
    assert task.objective in content
    assert '"allow_mutations": false' in content
    assert "evidence-latest" in content
    assert "最新の観察🧪" in content
    assert content.endswith(" [truncated]")
    assert content.count("RECENT OBSERVATION — UNTRUSTED DATA") == 1
    assert "stale page" not in content
    assert "older-" not in content
    assert "lower-priority" not in content
    assert "UNRESOLVED QUESTIONS" not in content
    assert "REQUIREMENT — UNTRUSTED DATA" not in content
    assert "SUPPLEMENTARY CONTEXT — UNTRUSTED DATA" not in content
    assert [item.model_dump() for item in items] == original


def test_reusing_context_builder_does_not_retain_prior_run_information() -> None:
    """Verify reusing context builder does not retain prior run information.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for reusing context builder does not retain prior run
            information."""
    builder = ContextBuilder()
    first_task = BrowserExplorationTask(
        url="https://first.example.com", objective="Inspect first application"
    )
    first_messages = builder.build(
        first_task, None, [observation("first", "first application content")], [], [], []
    )
    second_task = BrowserExplorationTask(
        url="https://second.example.com", objective="Inspect second application"
    )

    second_messages = builder.build(second_task, None, [], [], [], [])

    first_content = str(first_messages[1].content)
    second_content = str(second_messages[1].content)
    assert "first application content" in first_content
    assert second_task.objective in second_content
    assert second_task.url in second_content
    assert "first" not in second_content
    assert "RECENT OBSERVATION" not in second_content
    assert len(second_messages) == 2
    assert first_messages[0] is not second_messages[0]
    assert first_messages[1] is not second_messages[1]
