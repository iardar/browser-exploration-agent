"""Conservative client policy plus an explicit external network boundary.

Client URL inspection cannot contain redirects or browser subresource requests.
An operator must configure an isolated, restricted MCP browser environment and
attest to that boundary before this policy permits any browser action.
"""

import ipaddress
import re
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

from .browser_tools import ARGUMENTS, NETWORK_DETAIL_PARTS

if TYPE_CHECKING:
    from .contracts import BrowserExplorationTask


class SafetySettings(Protocol):
    """Expose just the network-policy settings needed by the action checks."""

    network_restricted: bool
    allow_private_network: bool


class SafetyError(Exception):
    """A proposed action or observed destination violates the configured policy."""


_URL = re.compile(r"^\s*(?:-\s*)?Page URL:\s*(\S+)\s*$", re.MULTILINE)
_REF = re.compile(r"\[ref=([^\]\s]+)\]")
_WRAPPED_REF = re.compile(r"(?:ref=([^\s\[\]]+)|\[ref=([^\s\[\]]+)\]|#([^\s\[\]]+))")
_LINK_URL = re.compile(r"^\s*-\s*/url:\s*(.*?)\s*$")
_BLOCKED_WORDS = re.compile(
    r"\b(delete|destroy|remove|erase|logout|log\s*out|sign\s*out|login|log\s*in|"
    r"sign\s*in|signin|signup|sign\s*up|register|authenticate|authorize|oauth|"
    r"upload|publish|submit|save|purchase|checkout|pay|unsubscribe|subscribe|"
    r"follow|unfollow|favorite|unfavorite|like|unlike|create)\b",
    re.IGNORECASE,
)
_BLOCKED_CONTROL_WORDS = re.compile(
    r"\b(delete|destroy|remove|erase|logout|log\s*out|sign\s*out|login|log\s*in|"
    r"sign\s*in|signin|signup|sign\s*up|register|authenticate|authorize|oauth|"
    r"upload|publish|save|purchase|checkout|pay|unsubscribe|subscribe|follow|unfollow|"
    r"favorite|unfavorite|like|unlike|create)\b",
    re.IGNORECASE,
)
_FORM_SUBMISSION_CONTROL = re.compile(r"\b(submit|search)\b", re.IGNORECASE)
_SENSITIVE_FIELD_WORDS = re.compile(
    r"\b(password|passcode|pin|one[\s-]*time|otp|verification\s+code|secret|token|"
    r"api[\s_-]*key|credit\s*card|card\s*number|cvv|cvc|security\s*code)\b",
    re.IGNORECASE,
)
_FORM_FIELD_TYPES = frozenset({"textbox", "checkbox", "radio", "combobox", "slider"})
_METADATA_HOSTS = frozenset(
    {"metadata.google.internal", "metadata.google", "instance-data.ec2.internal"}
)
_METADATA_IPS = frozenset({"169.254.169.254", "169.254.170.2", "fd00:ec2::254"})


def _host(value: str) -> str:
    """Normalize a hostname so comparisons ignore case and a trailing dot."""
    try:
        return value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise SafetyError("Destination has an invalid hostname.") from exc


class SafetyPolicy:
    """Check proposed browser actions independently of what the model says is safe.

    Trusted task permissions set the limits. Snapshot text supplies observed
    element references and link targets, but cannot grant permission. These client
    checks supplement the operator's external browser network restrictions.
    """

    def __init__(self, task: "BrowserExplorationTask", settings: SafetySettings) -> None:
        """Start policy tracking for this task with no observed page elements yet."""
        self.task = task
        self.settings = settings
        self.current_url = ""
        self.link_targets: dict[str, str] = {}
        self.element_labels: dict[str, str] = {}
        # A new snapshot invalidates previous action references, even at the same URL.
        self.reference_revision = 0

    def validate_url(self, url: str) -> str:
        """Check a destination's scheme, exact hostname, and recognizable IP restrictions.

        Return a normalized URL or raise ``SafetyError``. This does not resolve DNS
        or prevent the browser from making later redirect or subresource requests.
        """
        if any(ord(character) < 33 or ord(character) == 127 for character in url):
            raise SafetyError("Destination contains whitespace or control characters.")
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise SafetyError("Destination is not a valid HTTP(S) URL.") from exc
        if parsed.scheme not in {"http", "https"} or not hostname:
            raise SafetyError("Only absolute HTTP(S) destinations are permitted.")
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            raise SafetyError("Credential-bearing destination URLs are forbidden.")
        if "%" in hostname or "\\" in parsed.netloc:
            raise SafetyError("Encoded or ambiguous destination hostnames are forbidden.")
        hostname = _host(hostname)
        if hostname in _METADATA_HOSTS:
            raise SafetyError("Cloud metadata destinations are always forbidden.")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None:
            comparable = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
            ip = comparable or address
            if str(ip) in _METADATA_IPS or str(address) in _METADATA_IPS:
                raise SafetyError("Cloud metadata destinations are always forbidden.")
            if (not ip.is_global or ip.is_multicast) and not self.settings.allow_private_network:
                raise SafetyError("Private or reserved IP destinations require explicit opt-in.")
            if ip.is_unspecified or ip.is_multicast:
                raise SafetyError("Unspecified and multicast destinations are forbidden.")
        else:
            if re.fullmatch(r"[0-9.]+", hostname) or hostname.startswith("0x"):
                raise SafetyError("Ambiguous numeric IP destinations are forbidden.")
            private = "." not in hostname or hostname.endswith(
                (".localhost", ".local", ".internal", ".lan", ".home", ".test", ".invalid")
            )
            if private and not self.settings.allow_private_network:
                raise SafetyError("Local or private hostnames require explicit opt-in.")
        if hostname not in {_host(domain) for domain in self.task.allowed_domains}:
            raise SafetyError("Destination is outside the task's exact allowed domains.")
        host_part = f"[{hostname}]" if ":" in hostname else hostname
        netloc = host_part if port is None else f"{host_part}:{port}"
        return urlunsplit(
            (parsed.scheme, netloc, parsed.path or "/", parsed.query, parsed.fragment)
        )

    def observe(self, text: str, *, require_url: bool = False) -> str:
        """Read a tool's page URL and observed element references from snapshot text.

        A snapshot describes elements such as ``[ref=e43]`` and nested link URLs.
        Save those values so later actions can target observed controls. New page
        locations are validated, and a new snapshot replaces the old element map.
        """
        locations = _URL.findall(text)
        if require_url and not locations:
            raise SafetyError("Browser omitted its page URL; navigation cannot be checked.")
        for location in locations:
            self.current_url = self.validate_url(location)
        if locations or "### Snapshot" in text:
            self.reference_revision += 1
            self.link_targets = {}
            self.element_labels = {}
            pending_link: tuple[str, int] | None = None
            for line in text.splitlines():
                indent = len(line) - len(line.lstrip())
                ref_match = _REF.search(line)
                if ref_match:
                    ref = ref_match.group(1)
                    self.element_labels[ref] = line.strip()
                    is_link = re.match(r"^\s*-\s+link(?=[\s:])", line) is not None
                    pending_link = (ref, indent) if is_link else None
                target_match = _LINK_URL.match(line)
                if target_match and pending_link and indent > pending_link[1]:
                    target = target_match.group(1).strip("\"'")
                    self.link_targets[pending_link[0]] = urljoin(self.current_url, target)
                    pending_link = None
                elif line.strip() and pending_link and indent <= pending_link[1] and not ref_match:
                    pending_link = None
        return self.current_url

    def _readonly_destination(self, url: str) -> str:
        """Reject known risky route/query words after checking the destination URL."""
        validated = self.validate_url(url)
        parsed = urlsplit(validated)
        words = re.sub(r"[_/\-]", " ", unquote(parsed.path))
        if _BLOCKED_WORDS.search(words):
            raise SafetyError("Known authentication and state-changing routes are denied.")
        for key, value in parse_qsl(parsed.query):
            if key.lower() in {"action", "do", "cmd", "command", "operation", "method"}:
                if _BLOCKED_WORDS.search(value):
                    raise SafetyError("Navigation contains a known state-changing operation.")
        return validated

    def _observed_reference(self, value: Any) -> str:
        """Normalize reference notation only when it names an observed element exactly."""
        if isinstance(value, str):
            if value in self.element_labels:
                return value
            wrapped = _WRAPPED_REF.fullmatch(value)
            if wrapped:
                candidate = next(part for part in wrapped.groups() if part is not None)
                if candidate in self.element_labels:
                    return candidate
        editable = [
            ref
            for ref, label in self.element_labels.items()
            if re.match(r"-\s+(?:combobox|textbox|searchbox)\b", label)
        ]
        examples = editable or list(self.element_labels)[:5]
        hint = ", ".join(examples[:5]) if examples else "none; take a fresh snapshot first"
        raise SafetyError(
            "Interaction requires a reference in the current snapshot. "
            'For [ref=e43], pass the bare string "e43" in ref/target, without a selector. '
            f"Current reference examples: {hint}."
        )

    def authorize(
        self, name: str, args: dict[str, Any], capabilities: dict[str, str]
    ) -> tuple[str, dict[str, Any]]:
        """Return the permitted tool name and cleaned arguments, or raise a rejection.

        Check the known capability, allowed argument keys, task permissions, and
        observed element references before execution. A default-policy link click
        becomes validated navigation instead. Input and submission permissions
        remain separate, and known sensitive or destructive controls stay denied.
        """
        if not self.settings.network_restricted:
            raise SafetyError(
                "Browser execution requires network_restricted=true after the operator configures "
                "browser egress restrictions; client URL checks alone cannot contain redirects."
            )
        capability = next((key for key, value in capabilities.items() if value == name), None)
        if capability not in ARGUMENTS:
            raise SafetyError("This browser tool is not authorized.")
        if set(args) - ARGUMENTS[capability]:
            raise SafetyError("Tool arguments include unsupported or filesystem parameters.")
        # Normalize a copy so checking the proposal does not rewrite the model's original data.
        clean = dict(args)
        if capability == "network":
            if "all" in clean and type(clean["all"]) is not bool:
                raise SafetyError("Network history option must be a boolean.")
            if "filter" in clean:
                pattern = clean["filter"]
                if (
                    not isinstance(pattern, str)
                    or not pattern
                    or len(pattern) > 256
                    or any(ord(char) < 32 or ord(char) == 127 for char in pattern)
                ):
                    raise SafetyError(
                        "Network URL filter must be a nonempty pattern of at most 256 characters."
                    )
        if capability == "network_detail":
            if not self.task.allow_network_details:
                raise SafetyError("Network request details require allow_network_details.")
            index = clean.get("index")
            if type(index) is not int or not 1 <= index <= 9_007_199_254_740_991:
                raise SafetyError("Network request index must be a positive integer.")
            if "part" in clean:
                part = clean["part"]
                if not isinstance(part, str) or part not in NETWORK_DETAIL_PARTS:
                    raise SafetyError("Unsupported network request detail part.")
        if capability == "navigate":
            if not isinstance(clean.get("url"), str):
                raise SafetyError("Navigation requires a URL string.")
            clean["url"] = self._readonly_destination(clean["url"])
        form_input_allowed = self.task.allow_form_input or self.task.allow_form_submission
        if capability in {"type", "fill_form"} and not form_input_allowed:
            raise SafetyError("Form input requires explicit allow_form_input authorization.")
        if capability == "fill_form":
            fields = clean.get("fields")
            if not isinstance(fields, list) or not 1 <= len(fields) <= 20:
                raise SafetyError("Form filling requires between 1 and 20 observed fields.")
            authorized_fields: list[dict[str, Any]] = []
            allowed_field_keys = {"target", "element", "name", "type", "value"}
            for field in fields:
                if not isinstance(field, dict) or set(field) - allowed_field_keys:
                    raise SafetyError("Form fields contain unsupported arguments.")
                target = self._observed_reference(field.get("target"))
                observed_label = self.element_labels[target]
                if _BLOCKED_CONTROL_WORDS.search(observed_label) or _SENSITIVE_FIELD_WORDS.search(
                    observed_label
                ):
                    raise SafetyError(
                        "Sensitive, authentication and state-changing fields are denied."
                    )
                field_type = field.get("type")
                value = field.get("value")
                if field_type not in _FORM_FIELD_TYPES or not isinstance(value, str):
                    raise SafetyError("Form field type and value are invalid.")
                if len(value) > 2_000 or "\x00" in value:
                    raise SafetyError("Form field values must be bounded text.")
                if field_type == "checkbox" and value.lower() not in {"true", "false"}:
                    raise SafetyError("Checkbox values must be true or false.")
                authorized = dict(field)
                authorized["target"] = target
                authorized["name"] = observed_label[:300]
                if "element" in authorized:
                    authorized["element"] = observed_label[:300]
                authorized_fields.append(authorized)
            clean["fields"] = authorized_fields
            return name, clean
        if (
            capability in {"click", "hover", "type"}
            or {
                "ref",
                "target",
                "element",
            }
            & clean.keys()
        ):
            if "ref" in clean and "target" in clean:
                raise SafetyError("Use one observed reference, not multiple targeting arguments.")
            ref = self._observed_reference(clean.get("ref", clean.get("target")))
            clean["ref" if "ref" in clean else "target"] = ref
            observed_label = self.element_labels[ref]
            if _BLOCKED_CONTROL_WORDS.search(observed_label):
                raise SafetyError("Authentication, destructive and publishing controls are denied.")
            if "element" in clean:
                clean["element"] = observed_label[:300]
            if capability == "click" and not self.task.allow_mutations:
                if self.task.allow_form_submission and _FORM_SUBMISSION_CONTROL.search(
                    observed_label
                ):
                    return name, clean
                destination = self.link_targets.get(ref)
                if destination is None:
                    raise SafetyError(
                        "Arbitrary clicks can mutate data; "
                        "default policy permits observed links only."
                    )
                # Follow the observed URL through navigation instead of firing a click handler.
                return capabilities["navigate"], {"url": self._readonly_destination(destination)}
        if capability == "type":
            text = clean.get("text")
            if not isinstance(text, str) or len(text) > 2_000 or "\x00" in text:
                raise SafetyError("Typed input must be bounded text.")
            if not isinstance(clean.get("submit", False), bool) or not isinstance(
                clean.get("slowly", False), bool
            ):
                raise SafetyError("Typing options must be boolean values.")
            typed_ref = clean.get("ref", clean.get("target"))
            assert isinstance(typed_ref, str)  # Validated against the current snapshot above.
            if _SENSITIVE_FIELD_WORDS.search(self.element_labels[typed_ref]):
                raise SafetyError("Typing credentials and other sensitive values is denied.")
            if clean.get("submit", False) and not self.task.allow_form_submission:
                raise SafetyError(
                    "Form submission requires explicit allow_form_submission authorization."
                )
        if capability == "click":
            if clean.get("button", "left") != "left" or clean.get("doubleClick", False):
                raise SafetyError("Only a single primary-button click is supported.")
            if clean.get("modifiers"):
                raise SafetyError("Modified clicks are unsupported.")
        return name, clean
