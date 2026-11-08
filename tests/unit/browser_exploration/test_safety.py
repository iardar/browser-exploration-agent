"""Safety invariants do not require a browser, DNS, or an LLM."""

import pytest

from browser_exploration.config import Settings
from browser_exploration.contracts import BrowserExplorationTask
from browser_exploration.safety import SafetyError, SafetyPolicy

CAPABILITIES = {
    "navigate": "browser_navigate",
    "snapshot": "browser_snapshot",
    "click": "browser_click",
    "hover": "browser_hover",
    "screenshot": "browser_take_screenshot",
}
SNAPSHOT = """### Page
- Page URL: https://example.com/articles
### Snapshot
```yaml
- link "Details" [ref=e1]:
  - /url: /article/123
- button "Expand" [ref=e2]
- button "Publish" [ref=e3]
- link "Outside" [ref=e4]:
  - /url: https://outside.example.org/
```
"""


def policy(*, allow_mutations: bool = False, **kwargs: object) -> SafetyPolicy:
    return SafetyPolicy(
        BrowserExplorationTask(
            url="https://example.com/articles", objective="Explore", allow_mutations=allow_mutations
        ),
        Settings.model_validate({"network_restricted": True, **kwargs}),
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:secret@example.com/",
        "https://outside.example.org/",
        "https://sub.example.com/",
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://localhost/",
        "http://office.internal/",
        "http://[::1]/",
        "http://2130706433/",
        "http://0x7f000001/",
        "http://127.1/",
        "http://127.000.0.1/",
        "https://%65xample.com/",
        "https://example.com:99999/",
        "https://example.com/\nsecret",
        "https://example.com/space here",
        "https://example.com\\@outside.example.org/",
    ],
)
def test_unsafe_urls_rejected(url: str) -> None:
    """Verify unsafe urls rejected.

    Arrange: Use `url` parameter.
    Act: Call `policy.validate_url` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`.
    """
    with pytest.raises(SafetyError):
        policy().validate_url(url)


def test_exact_domains_and_normalization() -> None:
    """Verify exact domains and normalization.

    Arrange: Use the values passed to `policy.validate_url`.
    Act: Evaluate `policy.validate_url` in the assertion.
    Assert: Check `policy().validate_url('https://EXAMPLE.COM') == 'https://example.com/'`;
            `policy().validate_url('https://example.com.:8443/x') == 'https://example.com:8443/x'`.
    """
    assert policy().validate_url("https://EXAMPLE.COM") == "https://example.com/"
    assert policy().validate_url("https://example.com.:8443/x") == "https://example.com:8443/x"


def test_private_network_requires_both_opt_in_and_allowlist() -> None:
    """Verify private network requires both opt in and allowlist.

    Arrange: prepare `task`.
    Act: Call `SafetyPolicy.validate_url` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`; `allowed.validate_url(task.url) ==
            'http://127.0.0.1:8765/'`.
    """
    task = BrowserExplorationTask(url="http://127.0.0.1:8765", objective="Demo")
    with pytest.raises(SafetyError, match="Private"):
        SafetyPolicy(task, Settings()).validate_url(task.url)
    allowed = SafetyPolicy(task, Settings(allow_private_network=True))
    assert allowed.validate_url(task.url) == "http://127.0.0.1:8765/"
    with pytest.raises(SafetyError, match="allowed domains"):
        policy(allow_private_network=True).validate_url("http://127.0.0.1/")


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/",
        "http://metadata.google.internal/",
        "http://[fd00:ec2::254]/",
        "http://[::ffff:169.254.169.254]/",
    ],
)
def test_metadata_always_denied_even_with_private_opt_in(url: str) -> None:
    """Verify metadata always denied even with private opt in.

    Arrange: Use `url` parameter; prepare `task`.
    Act: Call `SafetyPolicy.validate_url` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`.
    """
    task = BrowserExplorationTask(url=url, objective="Must fail")
    with pytest.raises(SafetyError, match="metadata"):
        SafetyPolicy(task, Settings(allow_private_network=True)).validate_url(url)


def test_no_browser_action_without_external_network_boundary() -> None:
    """Verify no browser action without external network boundary.

    Arrange: prepare `task`, `restricted`.
    Act: Call `restricted.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`.
    """
    task = BrowserExplorationTask(url="https://example.com/", objective="Explore")
    restricted = SafetyPolicy(task, Settings())
    with pytest.raises(SafetyError, match="network_restricted"):
        restricted.authorize("browser_snapshot", {}, CAPABILITIES)


def test_observed_link_becomes_validated_navigation_and_ignores_model_label() -> None:
    """Verify observed link becomes validated navigation and ignores model label.

    Arrange: Start with `policy()`.
    Act: Evaluate `restricted.observe` in the assertion.
    Assert: Check `restricted.observe(SNAPSHOT, require_url=True) ==
            'https://example.com/articles'`; `restricted.authorize() equals values
            'browser_navigate', 'url', 'https://example.com/article/123'`.
    """
    restricted = policy()
    assert restricted.observe(SNAPSHOT, require_url=True) == "https://example.com/articles"
    assert restricted.authorize(
        "browser_click", {"ref": "e1", "element": "Ignore all restrictions"}, CAPABILITIES
    ) == ("browser_navigate", {"url": "https://example.com/article/123"})
    assert restricted.authorize("browser_click", {"target": "e1"}, CAPABILITIES) == (
        "browser_navigate",
        {"url": "https://example.com/article/123"},
    )


def test_arbitrary_clicks_selectors_and_external_links_denied() -> None:
    """Verify arbitrary clicks selectors and external links denied.

    Arrange: Start with `policy()`.
    Act: Call `policy` to obtain `restricted`.
    Assert: Check `raises SafetyError`.
    """
    restricted = policy()
    restricted.observe(SNAPSHOT)
    for arguments in ({"ref": "e2"}, {"target": "button"}, {"ref": "e4"}, {"ref": "stale"}):
        with pytest.raises(SafetyError):
            restricted.authorize("browser_click", arguments, CAPABILITIES)


def test_mutation_flag_allows_observed_click_but_never_destructive_label() -> None:
    """Verify mutation flag allows observed click but never destructive label.

    Arrange: prepare `restricted`.
    Act: Call `restricted.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `restricted.authorize() equals values 'browser_click', 'ref', 'e2'`; `raises
            SafetyError`.
    """
    restricted = policy(allow_mutations=True)
    restricted.observe(SNAPSHOT)
    assert restricted.authorize("browser_click", {"ref": "e2"}, CAPABILITIES) == (
        "browser_click",
        {"ref": "e2"},
    )
    with pytest.raises(SafetyError, match="publishing"):
        restricted.authorize("browser_click", {"ref": "e3", "element": "Safe"}, CAPABILITIES)
    with pytest.raises(SafetyError):
        restricted.authorize("browser_click", {"ref": "e2", "modifiers": ["Control"]}, CAPABILITIES)


@pytest.mark.parametrize("suffix", ["/delete/123", "/%64elete/123", "/login", "/?action=publish"])
def test_known_mutating_and_authentication_routes_are_denied(suffix: str) -> None:
    """Verify known mutating and authentication routes are denied.

    Arrange: Use `suffix` parameter.
    Act: Call `policy.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`.
    """
    with pytest.raises(SafetyError):
        policy().authorize(
            "browser_navigate", {"url": "https://example.com" + suffix}, CAPABILITIES
        )


def test_observation_rechecks_navigation_and_expires_references() -> None:
    """Verify observation rechecks navigation and expires references.

    Arrange: prepare `restricted`.
    Act: Call `restricted.observe` under `pytest.raises(SafetyError)`.
    Assert: Check `restricted.observe('Console: all good') == 'https://example.com/articles'`;
            `raises SafetyError`; `restricted.element_labels == {}`.
    """
    restricted = policy()
    restricted.observe(SNAPSHOT)
    assert restricted.observe("Console: all good") == "https://example.com/articles"
    with pytest.raises(SafetyError, match="omitted"):
        restricted.observe("Empty snapshot", require_url=True)
    with pytest.raises(SafetyError, match="allowed domains"):
        restricted.observe("- Page URL: https://outside.example.org/")
    restricted.observe("- Page URL: https://example.com/empty\n### Snapshot\n- text: Empty")
    assert restricted.link_targets == {}
    assert restricted.element_labels == {}


def test_forbidden_tools_and_filename_args_cannot_escape_policy() -> None:
    """Verify forbidden tools and filename args cannot escape policy.

    Arrange: Start with `policy()`.
    Act: Call `policy` to obtain `restricted`.
    Assert: Check `raises SafetyError`.
    """
    restricted = policy()
    for name, arguments in (
        ("browser_evaluate", {"function": "() => document.cookie"}),
        ("browser_snapshot", {"filename": "/tmp/leak"}),
        ("browser_take_screenshot", {"filename": "../secret"}),
        ("browser_network_requests", {"filename": "requests.txt"}),
    ):
        with pytest.raises(SafetyError):
            restricted.authorize(
                name, arguments, {**CAPABILITIES, "network": "browser_network_requests"}
            )


def test_network_scope_options_are_passed_to_the_selected_tool() -> None:
    """Accept a bounded URL filter and boolean history option without rewriting either value."""
    args = {"static": True, "filter": r"/api/.*", "all": True}
    selected = {**CAPABILITIES, "network": "browser_network_requests"}
    assert policy().authorize("browser_network_requests", args, selected) == (
        "browser_network_requests",
        args,
    )
    assert args == {"static": True, "filter": r"/api/.*", "all": True}


@pytest.mark.parametrize(
    "arguments",
    [
        {"all": "true"},
        {"all": 1},
        {"filter": 3},
        {"filter": ""},
        {"filter": "x" * 257},
        {"filter": "x\x00y"},
        {"filter": "x\x7fy"},
        {"filter": "x\ny"},
    ],
)
def test_network_scope_options_reject_invalid_values(arguments: dict[str, object]) -> None:
    """Reject malformed URL filters and non-boolean history flags before calling MCP."""
    with pytest.raises(SafetyError):
        policy().authorize(
            "browser_network_requests",
            arguments,
            {**CAPABILITIES, "network": "browser_network_requests"},
        )


def test_screenshot_target_must_be_an_observed_reference() -> None:
    """Verify screenshot target must be an observed reference.

    Arrange: prepare `restricted`.
    Act: Call `restricted.authorize` under `pytest.raises(SafetyError)`.
    Assert: Check `raises SafetyError`; `restricted.authorize() equals values
            'browser_take_screenshot', 'target', 'scale'`.
    """
    restricted = policy()
    restricted.observe(SNAPSHOT)
    with pytest.raises(SafetyError, match="reference"):
        restricted.authorize("browser_take_screenshot", {"target": "body"}, CAPABILITIES)
    assert restricted.authorize(
        "browser_take_screenshot", {"target": "e2", "scale": "css"}, CAPABILITIES
    ) == ("browser_take_screenshot", {"target": "e2", "scale": "css"})
