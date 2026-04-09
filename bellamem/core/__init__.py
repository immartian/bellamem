"""bellamem.core — pure, domain-agnostic belief calculus.

This package must not import from bellamem.adapters. Keep it clean.
"""

from .gene import Belief, Gene, mass_of
from .bella import Bella, Claim
from .expand import expand
from .store import save, load
from .principles import seed_principles, PRINCIPLES_FIELD

__all__ = [
    "Belief", "Gene", "mass_of",
    "Bella", "Claim",
    "expand",
    "save", "load",
    "seed_principles", "PRINCIPLES_FIELD",
]
