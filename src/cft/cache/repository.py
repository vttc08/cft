from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from cft.cache.store import JsonFileStore
from cft.config.paths import AppPaths
from cft.models.cache import (
    DistributionCacheRecord,
    ProfileCacheState,
    normalize_distribution_type,
)


class UnsupportedStateVersionError(ValueError):
    pass


StateUpdate = Callable[[ProfileCacheState], ProfileCacheState]


@dataclass(frozen=True)
class ProfileStateRepository:
    """Typed access to the profile-scoped state.json persistence boundary."""

    paths: AppPaths

    def exists(self, profile_name: str) -> bool:
        return self.paths.profile_state_file(profile_name).exists()

    def load(self, profile_name: str) -> ProfileCacheState:
        payload = JsonFileStore(self.paths.profile_state_file(profile_name)).read()
        state = ProfileCacheState.from_payload(payload, profile_name=profile_name)
        if state.schema_version != 1:
            raise UnsupportedStateVersionError(
                f"Unsupported state schema version {state.schema_version}; expected version 1."
            )
        return state

    def save(self, state: ProfileCacheState) -> bool:
        self.paths.ensure_profile_dirs(state.profile_name)
        return JsonFileStore(self.paths.profile_state_file(state.profile_name)).write_if_changed(
            state.to_payload()
        )

    def update(self, profile_name: str, update: StateUpdate) -> ProfileCacheState:
        state = update(self.load(profile_name))
        if state.profile_name != profile_name:
            state = replace(state, profile_name=profile_name)
        self.save(state)
        return state

    def mark_onboarding_seen(self, profile_name: str) -> ProfileCacheState:
        return self.update(profile_name, lambda state: replace(state, onboarding_seen=True))

    def set_distribution_type(
        self,
        *,
        profile_name: str,
        distribution_id: str,
        distribution_type: str,
    ) -> ProfileCacheState:
        normalized_type = normalize_distribution_type(distribution_type)

        def apply(state: ProfileCacheState) -> ProfileCacheState:
            existing = state.distributions.get(distribution_id) or DistributionCacheRecord(
                distribution_id=distribution_id
            )
            return replace(
                state,
                distributions={
                    **state.distributions,
                    distribution_id: replace(existing, type=normalized_type),
                },
            )

        return self.update(profile_name, apply)
