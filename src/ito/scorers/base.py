"""Scorer protocol and registry.

A scorer maps frame features to a single ``[0, 1]`` confidence. Keeping every
scorer on the same scale is what lets one gate configuration (``on`` / ``off``
/ ``dwell``) serve every action, and what will let the stage-2 prototypical
matcher drop in here without touching the gate or the policy layer.
"""

from __future__ import annotations

from typing import Dict, Protocol, Type, runtime_checkable

from ..types import Features


@runtime_checkable
class Scorer(Protocol):
    name: str

    def score(self, features: Features) -> float:
        ...


def build_scorer(kind: str, params: Dict | None = None) -> Scorer:
    """Instantiate a scorer by name, rejecting unknown parameters.

    Rejecting unknown keys matters: a silent typo in ``dwell_ms`` (or
    ``start_deg``) would otherwise produce a system that looks calibrated and
    is not.
    """
    from .geometric import REGISTRY

    if kind not in REGISTRY:
        raise KeyError(f"unknown scorer {kind!r}; available: {sorted(REGISTRY)}")
    cls: Type = REGISTRY[kind]
    params = dict(params or {})
    allowed = set(getattr(cls, "__annotations__", {}))
    unknown = set(params) - allowed
    if unknown:
        raise KeyError(f"unknown parameters for scorer {kind!r}: {sorted(unknown)}")
    return cls(**params)
