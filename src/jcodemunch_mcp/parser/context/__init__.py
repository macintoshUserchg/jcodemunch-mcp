"""Context providers for enriching code indexes with business metadata.

Context providers detect ecosystem tools (dbt, Terraform, OpenAPI, etc.)
and inject business context into symbols and file summaries during indexing.
"""

from .base import ContextProvider, FileContext, discover_providers, enrich_symbols, collect_metadata

# Import provider modules so @register_provider decorators execute.
# Each module registers itself on import — add new providers here.
# Gate dbt provider: it depends on SQL language (dbt models are SQL files with Jinja).
# The dbt provider is only loaded when SQL is enabled in config.languages.
from ...config import is_language_enabled as _is_lang_enabled
if _is_lang_enabled("sql"):
    from . import dbt  # noqa: F401
from . import git_blame  # noqa: F401

__all__ = [
    "ContextProvider",
    "FileContext",
    "collect_metadata",
    "discover_providers",
    "enrich_symbols",
]
