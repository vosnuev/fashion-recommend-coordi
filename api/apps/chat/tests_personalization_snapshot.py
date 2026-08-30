from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.chat.models import (
    ChatIdentity,
    validate_personalization_snapshot,
)
from apps.chat.services.personalization_snapshot import (
    PERSONALIZATION_SNAPSHOT_SCHEMA_VERSION,
    build_personalization_snapshot,
)


class PersonalizationSnapshotContractTests(SimpleTestCase):
    def test_guest_snapshot_has_stable_empty_source_versions(self) -> None:
        identity = ChatIdentity(
            id=uuid.uuid4(),
            identity_type=ChatIdentity.IdentityType.GUEST,
        )

        snapshot = build_personalization_snapshot(identity=identity)

        self.assertEqual(
            snapshot["schema_version"],
            PERSONALIZATION_SNAPSHOT_SCHEMA_VERSION,
        )
        self.assertFalse(snapshot["personalized"])
        self.assertEqual(snapshot["identity_type"], ChatIdentity.IdentityType.GUEST)
        self.assertEqual(snapshot["sources"]["wardrobe"]["count"], 0)
        self.assertEqual(
            snapshot["sources"]["behavior"]["recommendations"]["count"],
            0,
        )
        validate_personalization_snapshot(snapshot)

    def test_legacy_empty_snapshot_is_valid(self) -> None:
        validate_personalization_snapshot({})

    def test_malformed_snapshot_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_personalization_snapshot({"schema_version": "1.0"})
