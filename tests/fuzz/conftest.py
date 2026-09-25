"""Hypothesis budget for the fuzz and limit suite (PLAN.md section 16.2).

The ``ci`` profile (the default) keeps the suite to a few minutes: ``CQ_FUZZ_EXAMPLES``
examples per property (default 150), no per-example deadline (the database-backed
properties are slow on a loaded runner), and a derandomized search, so a CI failure
reproduces locally. ``CQ_FUZZ_PROFILE=long`` explores ten times as many random examples.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck, settings

_EXAMPLES = int(os.environ.get("CQ_FUZZ_EXAMPLES", "150"))
_HEALTH = [HealthCheck.too_slow, HealthCheck.function_scoped_fixture]

settings.register_profile(
    "ci",
    max_examples=_EXAMPLES,
    derandomize=True,
    deadline=None,
    suppress_health_check=_HEALTH,
    print_blob=True,
)
settings.register_profile(
    "long",
    max_examples=_EXAMPLES * 10,
    deadline=None,
    suppress_health_check=_HEALTH,
    print_blob=True,
)
settings.load_profile(os.environ.get("CQ_FUZZ_PROFILE", "ci"))
