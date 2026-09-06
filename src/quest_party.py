"""Role and identity resolution for the configurable auto-quest party."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence, TypeVar


ClientT = TypeVar("ClientT")
FOLLOW_RETRY_DELAYS = (2.0, 5.0, 10.0, 20.0, 30.0)
FRIEND_ICON_PRESETS = {
    # The selector starts at frame 42 of icon sheet 1. Button7 and Button8
    # therefore map to frames 49 and 50; Button9 is frame 0 on sheet 2.
    "goblin": (1, 49),
    "grass": (1, 50),
    "fish": (2, 0),
}


@dataclass(frozen=True)
class QuestParty:
    questers: list[ClientT]
    hitters: list[ClientT]
    idle: list[ClientT]
    hitter_assignments: list[tuple[ClientT, ClientT]]


def friend_follow_retry_delay(failure_count: int) -> float:
    """Return bounded backoff for a consecutive friend-teleport failure."""
    index = max(0, min(int(failure_count) - 1, len(FOLLOW_RETRY_DELAYS) - 1))
    return FOLLOW_RETRY_DELAYS[index]


def _clean(value) -> str:
    return str(value or "").strip().casefold()


def stable_client_identity(client) -> str:
    """Return the best persistent identity available for a hooked client."""
    nickname = _clean(getattr(client, "account_nick", None))
    if nickname:
        return f"account:{nickname}"
    gid = getattr(client, "player_gid", None)
    if gid:
        return f"gid:{gid}"
    return f"title:{_clean(getattr(client, 'title', ''))}"


def client_identity_aliases(client) -> set[str]:
    """Accept new persistent IDs and legacy p-title settings during migration."""
    title = _clean(getattr(client, "title", ""))
    nickname = _clean(getattr(client, "account_nick", None))
    gid = getattr(client, "player_gid", None)
    aliases = {title, f"title:{title}"} if title else set()
    if nickname:
        aliases.update({nickname, f"account:{nickname}"})
    if gid:
        aliases.update({str(gid).casefold(), f"gid:{gid}".casefold()})
    return aliases


def resolve_quester_friend_icon(
    client, configured_icons: Mapping[str, object] | None
) -> tuple[int, int] | None:
    """Resolve an optional friend-list icon fallback for a questing client."""
    normalized = {
        _clean(identity): value
        for identity, value in (configured_icons or {}).items()
        if _clean(identity)
    }
    raw = next(
        (
            normalized[alias]
            for alias in client_identity_aliases(client)
            if alias in normalized
        ),
        None,
    )
    if not isinstance(raw, Mapping):
        return None
    try:
        icon_list = int(raw.get("icon_list"))
        icon_index = int(raw.get("icon_index"))
    except (TypeError, ValueError):
        return None
    if icon_list not in (1, 2) or icon_index < 0:
        return None
    return icon_list, icon_index


def _normalized(values: Iterable[str] | None) -> set[str]:
    return {_clean(value) for value in (values or []) if _clean(value)}


def _find_selected(clients: list[ClientT], configured: set[str]) -> list[ClientT]:
    return [
        client
        for client in clients
        if configured.intersection(client_identity_aliases(client))
    ]


def resolve_quest_party(
    clients: Sequence[ClientT],
    *,
    enabled: bool,
    quester_titles: Iterable[str] | None,
    hitter_titles: Iterable[str] | None,
    assignment_mode: str = "auto",
    manual_assignments: Mapping[str, str] | None = None,
) -> QuestParty:
    """Resolve persistent role selections against currently injected clients.

    Old p-title settings remain valid. New settings prefer a GID, then the saved
    account nickname, so reconnecting in another order does not swap roles.
    """
    live_clients = list(clients)
    if not enabled:
        return QuestParty(live_clients, [], [], [])

    quester_keys = _normalized(quester_titles)
    hitter_keys = _normalized(hitter_titles) - quester_keys
    questers = _find_selected(live_clients, quester_keys)
    hitters = [
        client
        for client in _find_selected(live_clients, hitter_keys)
        if client not in questers
    ]
    selected_ids = {id(client) for client in questers + hitters}
    idle = [client for client in live_clients if id(client) not in selected_ids]

    assignments: list[tuple[ClientT, ClientT]] = []
    normalized_manual = {
        _clean(hitter_id): _clean(quester_id)
        for hitter_id, quester_id in (manual_assignments or {}).items()
        if _clean(hitter_id) and _clean(quester_id)
    }
    for index, hitter in enumerate(hitters):
        quester = None
        if assignment_mode == "manual":
            target_keys = {
                normalized_manual[alias]
                for alias in client_identity_aliases(hitter)
                if alias in normalized_manual
            }
            quester = next(
                (
                    candidate
                    for candidate in questers
                    if target_keys.intersection(client_identity_aliases(candidate))
                ),
                None,
            )
        if quester is None and questers:
            quester = questers[index % len(questers)]
        if quester is not None:
            assignments.append((hitter, quester))

    return QuestParty(questers, hitters, idle, assignments)
