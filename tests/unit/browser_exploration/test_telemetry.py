from browser_exploration.telemetry import TokenAccumulator


def test_tokens_use_only_provider_reported_values() -> None:
    """Verify tokens use only provider reported values.

    Arrange: Start with `TokenAccumulator()`.
    Act: Evaluate `usage.counts` in the assertion.
    Assert: Check `usage.counts() == {'input_tokens': None, 'output_tokens': None, 'total_tokens':
            None}`; `usage.calls == 2`; `usage.counts() == {'input_tokens': 30, 'output_tokens': 7,
            'total_tokens': 37}`.
    """
    usage = TokenAccumulator()
    assert usage.counts() == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    usage.add({"input_tokens": 10, "output_tokens": 3, "total_tokens": 13})
    usage.add({"input_tokens": 20, "output_tokens": 4, "total_tokens": 24})
    assert usage.calls == 2
    assert usage.counts() == {"input_tokens": 30, "output_tokens": 7, "total_tokens": 37}


def test_incomplete_provider_usage_stays_unknown() -> None:
    """Verify incomplete provider usage stays unknown.

    Arrange: Start with `TokenAccumulator()`.
    Act: Evaluate `usage.counts` in the assertion.
    Assert: Check `usage.counts() == {'input_tokens': 12, 'output_tokens': 4, 'total_tokens':
            None}`; `usage.counts() == {'input_tokens': None, 'output_tokens': None, 'total_tokens':
            None}`.
    """
    usage = TokenAccumulator()
    usage.add({"input_tokens": 10, "output_tokens": 3, "total_tokens": 13})
    usage.add({"input_tokens": 2, "output_tokens": 1})
    assert usage.counts() == {"input_tokens": 12, "output_tokens": 4, "total_tokens": None}
    usage.add(None)
    assert usage.counts() == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
