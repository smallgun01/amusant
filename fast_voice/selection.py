"""Explicit provider selection with fail-closed behavior."""

from collections.abc import Mapping

from .contracts import LiveProvider


class ProviderSelectionError(ValueError):
    """The explicit provider choice is absent, unknown, or misregistered."""


def select_live_provider(
    providers: Mapping[str, LiveProvider], selected_provider: str | None
) -> LiveProvider:
    """Return only the explicitly selected adapter; never auto-select or fallback.

    The caller owns obtaining ``selected_provider`` from user-controlled app config.
    This pure function performs no network, credential, or environment access.
    """

    if selected_provider is None or not selected_provider or selected_provider != selected_provider.strip():
        raise ProviderSelectionError("An explicit, non-empty Live provider selection is required.")
    provider = providers.get(selected_provider)
    if provider is None:
        raise ProviderSelectionError(f"Unknown Live provider: {selected_provider!r}.")
    if provider.provider_id != selected_provider:
        raise ProviderSelectionError(
            f"Provider registry key {selected_provider!r} does not match adapter id {provider.provider_id!r}."
        )
    return provider
