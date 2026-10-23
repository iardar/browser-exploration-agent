"""Provider-reported token accounting; missing usage stays explicitly unknown."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TokenAccumulator:
    """Each total becomes unknown if any call omitted that provider usage field."""

    calls: int = 0
    _totals: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    )
    _unknown: set[str] = field(default_factory=set)

    def add(self, usage_metadata: Mapping[str, Any] | None) -> None:
        self.calls += 1
        for key in self._totals:
            value = usage_metadata.get(key) if usage_metadata else None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self._totals[key] += value
            else:
                self._unknown.add(key)

    def counts(self) -> dict[str, int | None]:
        return {
            key: total if self.calls and key not in self._unknown else None
            for key, total in self._totals.items()
        }
