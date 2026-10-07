from typing import Protocol


class ScopeMembershipRepository(Protocol):
    async def scope_ids_for(self, object_id: str) -> frozenset[str]:
        """Return only database-authorized scopes for a validated Entra object ID."""
        ...
