"""Bundled providers.

Importing this package registers every adapter below. Each adapter imports its
SDK lazily, so a provider you do not use never has to be installed.

To add one: create a module here, decorate the class with
``@register_provider``, and import it in this file. ``_template.py`` is a
ready-to-copy starting point and is intentionally *not* imported.
"""

from __future__ import annotations

from category_helper.llm.providers import (  # noqa: F401 - imported for the registration side effect
    anthropic_provider,
    gemini_provider,
    openai_compatible,
    openai_provider,
)
