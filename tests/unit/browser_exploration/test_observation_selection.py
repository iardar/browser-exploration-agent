"""Keep actual snapshot content visible when query parameters dominate its prefix."""

from browser_exploration.observations import summarize_observation


def test_snapshot_link_queries_do_not_push_result_headings_out_of_context() -> None:
    """Verify snapshot link queries do not push result headings out of context.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for snapshot link queries do not push result headings out
            of context."""
    query = "&".join(f"tracking{index}=" + "x" * 200 for index in range(20))
    text = (
        "### Page\n- Page URL: https://example.com/search\n### Snapshot\n"
        '- link "Images" [ref=e10]:\n  - /url: /search?q=Armavir&'
        + query
        + '\n- heading "Armavir results" [ref=e20]\n- text: Details about Armavir\n'
    )
    summary = summarize_observation(text, 300)
    assert len(summary) <= 300
    assert "Armavir results" in summary
    assert "Details about Armavir" in summary
    assert "[query omitted]" in summary
    assert "tracking" not in summary


def test_overlong_non_snapshot_text_has_an_explicit_truncation_marker() -> None:
    """Verify overlong non snapshot text has an explicit truncation marker.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for overlong non snapshot text has an explicit truncation
            marker."""
    assert summarize_observation("x" * 100, 40) == "x" * 28 + " [truncated]"
    assert len(summarize_observation("x" * 100, 5)) == 5
