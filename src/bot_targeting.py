"""Helpers for routing bot scripts to selected client groups."""

from collections.abc import Iterable, Sequence
from typing import Any


def normalize_client_titles(titles: Any) -> tuple[str, ...] | None:
    """Return unique, non-empty client titles while preserving their order.

    ``None`` means that no explicit target list was supplied, which keeps the
    legacy behaviour of targeting every hooked client.  An empty iterable is an
    explicit request to target no clients.
    """

    if titles is None:
        return None
    if isinstance(titles, str):
        titles = [titles]
    if not isinstance(titles, Iterable):
        return ()

    normalized = []
    seen = set()
    for title in titles:
        value = str(title).strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            normalized.append(value)
    return tuple(normalized)


def unpack_bot_command(data: Any) -> tuple[str, tuple[str, ...] | None]:
    """Read both the new targeted payload and the legacy plain-text payload."""

    if isinstance(data, dict):
        text = str(data.get("text") or "")
        return text, normalize_client_titles(data.get("clients"))
    return str(data or ""), None


def resolve_bot_clients(
    clients: Sequence[Any], requested_titles: tuple[str, ...] | None
) -> list[Any]:
    """Resolve requested titles against live clients in the live client order."""

    if requested_titles is None:
        return list(clients)
    requested = {title.casefold() for title in requested_titles}
    return [
        client
        for client in clients
        if str(getattr(client, "title", "")).casefold() in requested
    ]


def bot_group_key(clients: Sequence[Any]) -> tuple[str, ...]:
    """Build the stable display/task key for a selected group."""

    return tuple(str(getattr(client, "title", "")) for client in clients)


def overlapping_bot_groups(
    group_keys: Iterable[tuple[str, ...]], selected_titles: Iterable[str] | None
) -> list[tuple[str, ...]]:
    """Return groups that overlap the selection, or every group for ``None``."""

    keys = list(group_keys)
    if selected_titles is None:
        return keys
    selected = {str(title).casefold() for title in selected_titles}
    return [
        key
        for key in keys
        if selected.intersection(title.casefold() for title in key)
    ]
