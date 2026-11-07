"""Negative URL/action cases and snapshot-reference trust boundaries, without DNS."""

from typing import Any

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
    "network": "browser_network_requests",
}


def make_policy(
    url: str = "https://example.com/articles/first",
    *,
    mutations: bool = False,
    private: bool = False,
) -> SafetyPolicy:
    return SafetyPolicy(
        BrowserExplorationTask(url=url, objective="Inspect safely", allow_mutations=mutations),
        Settings(network_restricted=True, allow_private_network=private),
    )


def observe_control(policy: SafetyPolicy, *, label: str = "Expand details") -> None:
    policy.observe(
        f"- Page URL: https://example.com/articles/first\n### Snapshot\n"
        f'- button "{label}" [ref=b1]\n'
    )


@pytest.mark.parametrize(
    "url", ["https://[broken/", "https://example.com/\x7f", "https://\ud800.org/"]
)
def test_malformed_observed_destinations_are_rejected_without_dns(url: str) -> None:
    """Verify malformed observed destinations are rejected without dns.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for malformed observed destinations are rejected without
            dns."""
    with pytest.raises(SafetyError):
        make_policy().validate_url(url)


@pytest.mark.parametrize(
    "url", ["http://0.0.0.0/", "http://224.0.0.1/", "http://[::]/", "http://[ff02::1]/"]
)
def test_private_opt_in_never_authorizes_unspecified_or_multicast_destinations(url: str) -> None:
    """Verify private opt in never authorizes unspecified or multicast destinations.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for private opt in never authorizes unspecified or
            multicast destinations."""
    with pytest.raises(SafetyError, match="Unspecified and multicast"):
        make_policy(url, private=True).validate_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://169.254.170.2/", "http://metadata.google/", "http://instance-data.ec2.internal/"],
)
def test_additional_metadata_aliases_are_always_blocked(url: str) -> None:
    """Verify additional metadata aliases are always blocked.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for additional metadata aliases are always blocked.
    """
    with pytest.raises(SafetyError, match="metadata"):
        make_policy(url, private=True).validate_url(url)


def test_public_ipv6_preserves_port_path_query_and_fragment() -> None:
    """Verify public ipv6 preserves port path query and fragment.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for public ipv6 preserves port path query and fragment.
    """
    url = "https://[2606:4700:4700::1111]:8443/article?q=one#details"
    assert make_policy(url).validate_url(url) == url


def test_ipv4_mapped_loopback_requires_private_opt_in() -> None:
    """Verify ipv4 mapped loopback requires private opt in.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for ipv4 mapped loopback requires private opt in.
    """
    url = "http://[::ffff:127.0.0.1]:8765/"
    with pytest.raises(SafetyError, match="Private"):
        make_policy(url).validate_url(url)
    assert make_policy(url, private=True).validate_url(url) == url


def test_unicode_hostname_is_matched_as_normalized_idna_not_as_suffix() -> None:
    """Verify unicode hostname is matched as normalized idna not as suffix.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for unicode hostname is matched as normalized idna not as
            suffix."""
    policy = make_policy("https://bücher.example/")
    assert policy.validate_url("https://BÜCHER.example.:8443/a") == (
        "https://xn--bcher-kva.example:8443/a"
    )
    with pytest.raises(SafetyError, match="allowed domains"):
        policy.validate_url("https://xn--bcher-kva.example.attacker.org/")


@pytest.mark.parametrize(
    "arguments", [{}, {"url": None}, {"url": 123}, {"url": ["https://example.com"]}]
)
def test_navigation_rejects_missing_or_non_string_url(arguments: dict[str, Any]) -> None:
    """Verify navigation rejects missing or non string url.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for navigation rejects missing or non string url.
    """
    with pytest.raises(SafetyError, match="URL string"):
        make_policy().authorize("browser_navigate", arguments, CAPABILITIES)


@pytest.mark.parametrize("capability", ["click", "hover", "screenshot"])
def test_conflicting_observed_references_are_rejected(capability: str) -> None:
    """Verify conflicting observed references are rejected.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for conflicting observed references are rejected.
    """
    policy = make_policy(mutations=True)
    observe_control(policy)
    with pytest.raises(SafetyError, match="one observed reference"):
        policy.authorize(CAPABILITIES[capability], {"ref": "b1", "target": "b1"}, CAPABILITIES)


@pytest.mark.parametrize(
    "arguments", [{"button": "right"}, {"button": "middle"}, {"doubleClick": True}]
)
def test_mutation_authorization_does_not_allow_secondary_or_double_clicks(
    arguments: dict[str, Any],
) -> None:
    """Verify mutation authorization does not allow secondary or double clicks.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for mutation authorization does not allow secondary or
            double clicks."""
    policy = make_policy(mutations=True)
    observe_control(policy)
    with pytest.raises(SafetyError, match="single primary-button"):
        policy.authorize("browser_click", {"target": "b1", **arguments}, CAPABILITIES)


@pytest.mark.parametrize(
    "label", ["Sign in", "Create account", "Upload document", "Publish comment", "Delete record"]
)
def test_mutation_authorization_never_overrides_prohibited_observed_control_label(
    label: str,
) -> None:
    """Verify mutation authorization never overrides prohibited observed control label.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for mutation authorization never overrides prohibited
            observed control label."""
    policy = make_policy(mutations=True)
    observe_control(policy, label=label)
    with pytest.raises(SafetyError, match="Authentication, destructive and publishing"):
        policy.authorize("browser_click", {"ref": "b1", "element": "Harmless"}, CAPABILITIES)


def test_hover_uses_observed_description_without_mutating_caller_arguments() -> None:
    """Verify hover uses observed description without mutating caller arguments.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for hover uses observed description without mutating caller
            arguments."""
    policy = make_policy()
    observe_control(policy, label="Details " + "x" * 350)
    arguments = {"target": "b1", "element": "Model-invented instruction"}
    name, authorized = policy.authorize("browser_hover", arguments, CAPABILITIES)
    assert name == "browser_hover"
    assert authorized["element"].startswith('- button "Details ')
    assert len(authorized["element"]) == 300
    assert arguments["element"] == "Model-invented instruction"


@pytest.mark.parametrize("query", ["page=2", "action=inspect", "page=2&operation=read&sort=asc"])
def test_observational_query_values_remain_available(query: str) -> None:
    """Verify observational query values remain available.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for observational query values remain available.
    """
    url = f"https://example.com/article?{query}"
    assert make_policy().authorize("browser_navigate", {"url": url}, CAPABILITIES) == (
        "browser_navigate",
        {"url": url},
    )


@pytest.mark.parametrize("key", ["do", "cmd", "command", "operation", "method", "ACTION"])
def test_mutation_query_aliases_are_rejected_even_with_other_safe_parameters(key: str) -> None:
    """Verify mutation query aliases are rejected even with other safe parameters.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for mutation query aliases are rejected even with other
            safe parameters."""
    with pytest.raises(SafetyError, match="state-changing operation"):
        make_policy(mutations=True).authorize(
            "browser_navigate", {"url": f"https://example.com/?page=1&{key}=delete"}, CAPABILITIES
        )


def test_relative_observed_link_resolves_against_current_location() -> None:
    """Verify relative observed link resolves against current location.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for relative observed link resolves against current
            location."""
    policy = make_policy()
    policy.observe(
        "- Page URL: https://example.com/articles/first\n### Snapshot\n"
        '- link "Second" [ref=l1]:\n  - /url: "../articles/second"\n'
    )
    assert policy.authorize("browser_click", {"target": "l1"}, CAPABILITIES) == (
        "browser_navigate",
        {"url": "https://example.com/articles/second"},
    )


def test_same_level_url_does_not_attach_to_a_preceding_link() -> None:
    """Verify same level url does not attach to a preceding link.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for same level url does not attach to a preceding link.
    """
    policy = make_policy()
    policy.observe(
        "- Page URL: https://example.com/articles/first\n### Snapshot\n"
        '- link "No target" [ref=l1]\n- generic "Unrelated content"\n  - /url: /other\n'
    )
    assert policy.link_targets == {}
    with pytest.raises(SafetyError, match="observed links"):
        policy.authorize("browser_click", {"ref": "l1"}, CAPABILITIES)


def test_intervening_control_reference_cannot_inherit_previous_link_target() -> None:
    """Verify intervening control reference cannot inherit previous link target.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for intervening control reference cannot inherit previous
            link target."""
    policy = make_policy()
    policy.observe(
        "- Page URL: https://example.com/articles/first\n### Snapshot\n"
        '- link "No target" [ref=l1]\n- button "Details" [ref=b1]:\n  - /url: /other\n'
    )
    assert policy.link_targets == {}


def test_link_in_button_label_does_not_authorize_a_readonly_click() -> None:
    """Verify link in button label does not authorize a readonly click.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for link in button label does not authorize a readonly
            click."""
    policy = make_policy()
    policy.observe(
        "- Page URL: https://example.com/articles/first\n### Snapshot\n"
        '- button "Preview link" [ref=b1]:\n  - /url: /articles/second\n'
    )
    assert policy.link_targets == {}
    with pytest.raises(SafetyError, match="observed links"):
        policy.authorize("browser_click", {"ref": "b1"}, CAPABILITIES)


def test_snapshot_without_new_url_invalidates_old_element_references() -> None:
    """Verify snapshot without new url invalidates old element references.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for snapshot without new url invalidates old element
            references."""
    policy = make_policy()
    observe_control(policy)
    policy.observe('### Snapshot\n- heading "No controls"')
    assert policy.current_url == "https://example.com/articles/first"
    with pytest.raises(SafetyError, match="current snapshot"):
        policy.authorize("browser_hover", {"ref": "b1"}, CAPABILITIES)


def test_multiple_observed_page_urls_cannot_hide_an_intermediate_outside_destination() -> None:
    """Verify multiple observed page urls cannot hide an intermediate outside destination.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for multiple observed page urls cannot hide an intermediate
            outside destination."""
    policy = make_policy()
    with pytest.raises(SafetyError, match="allowed domains"):
        policy.observe(
            "- Page URL: https://example.com/start\n"
            "- Page URL: https://outside.example.org/\n"
            "- Page URL: https://example.com/end\n"
        )


def test_a_tool_name_cannot_acquire_an_unrecognized_capability() -> None:
    """Verify a tool name cannot acquire an unrecognized capability.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for a tool name cannot acquire an unrecognized capability.
    """
    with pytest.raises(SafetyError, match="not authorized"):
        make_policy().authorize("browser_snapshot", {}, {"evaluate": "browser_snapshot"})


def test_readonly_inspection_without_element_target_is_allowed() -> None:
    """Verify readonly inspection without element target is allowed.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for readonly inspection without element target is allowed.
    """
    assert make_policy().authorize("browser_network_requests", {"static": False}, CAPABILITIES) == (
        "browser_network_requests",
        {"static": False},
    )
