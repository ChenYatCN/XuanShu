"""Per-client ownership for input-producing automation.

The mouse handler only serializes individual clicks.  Higher-level automation
needs a wider critical section so another subsystem cannot insert a teleport,
key press, or popup click between selecting a combat card and its target.
"""

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Optional


_CLIENT_OWNERSHIP_ATTR = "_xuanshu_automation_ownership"


class ClientAutomationOwnership:
    """A task-reentrant async lock shared by every automation for one client."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner_task: Optional[asyncio.Task] = None
        self._owner_labels: list[str] = []

    @property
    def locked(self) -> bool:
        return self._owner_task is not None

    @property
    def owner_label(self) -> Optional[str]:
        return self._owner_labels[-1] if self._owner_labels else None

    def claim(self, label: str) -> "_OwnershipClaim":
        return _OwnershipClaim(self, label)

    async def _acquire(self, label: str) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("automation ownership requires an asyncio task")

        if self._owner_task is task:
            self._owner_labels.append(label)
            return

        await self._lock.acquire()
        self._owner_task = task
        self._owner_labels.append(label)

    def _release(self) -> None:
        task = asyncio.current_task()
        if task is None or self._owner_task is not task:
            raise RuntimeError("automation ownership released by a non-owner task")

        self._owner_labels.pop()
        if not self._owner_labels:
            self._owner_task = None
            self._lock.release()


class _OwnershipClaim(AbstractAsyncContextManager):
    def __init__(self, ownership: ClientAutomationOwnership, label: str) -> None:
        self._ownership = ownership
        self._label = label

    async def __aenter__(self) -> ClientAutomationOwnership:
        await self._ownership._acquire(self._label)
        return self._ownership

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        self._ownership._release()


def get_client_automation_ownership(client) -> ClientAutomationOwnership:
    """Return the ownership object attached to the actual WizWalker client."""
    ownership = getattr(client, _CLIENT_OWNERSHIP_ATTR, None)
    # AsyncMock/MagicMock manufacture arbitrary attributes on demand.  Accept
    # only our concrete type so test doubles and proxy-like clients still get a
    # real ownership lock.
    if not isinstance(ownership, ClientAutomationOwnership):
        ownership = ClientAutomationOwnership()
        setattr(client, _CLIENT_OWNERSHIP_ATTR, ownership)
    return ownership


def automation_owner(client, label: str) -> _OwnershipClaim:
    """Claim exclusive input ownership of ``client`` for the current task."""
    return get_client_automation_ownership(client).claim(label)
