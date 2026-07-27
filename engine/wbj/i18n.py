"""Tiny bilingual helper for user-facing web-app text (EN default, ES option).

`L(lang, en, es)` picks the string for the requested language. Generators
(`narrative`, `price_targets`, `company_brief`, quick N/S reasons) thread a
`lang` argument down and wrap each user-visible string in `L(...)`. English
is the default so every existing caller and test keeps working unchanged.
"""

from __future__ import annotations


def L(lang: str, en: str, es: str) -> str:
    """Return `es` when lang == 'es', otherwise `en`."""
    return es if lang == "es" else en
