"""Stable import point for this bundle's product spec.

The test suite imports ``SPEC`` from ``idea``; this module exists so tooling
and notebooks have one obvious place to reach for it too.
"""

from idea import SPEC

__all__ = ["SPEC"]
