from io import BytesIO
from unittest.mock import MagicMock, patch
from uuid import UUID

from django.test import SimpleTestCase

from apps.style_calendar.services import storage


class CalendarStorageKeyTests(SimpleTestCase):
    calendar_id = UUID("11111111-1111-1111-1111-111111111111")
    link_id = UUID("22222222-2222-2222-2222-222222222222")

    def test_calendar_key_structure(self) -> None:
        self.assertEqual(
            storage.original_key(7, self.calendar_id, "My Photo.JPEG"),
            f"calendar/7/{self.calendar_id}/original.jpeg",
        )
        self.assertEqual(
            storage.original_key(
                7,
                self.calendar_id,
                "incorrect.jpg",
                "image/png",
            ),
            f"calendar/7/{self.calendar_id}/original.png",
        )
        self.assertEqual(
            storage.selected_item_key(
                7,
                self.calendar_id,
                self.link_id,
                "wardrobe/7/item.PNG",
            ),
            f"calendar/7/{self.calendar_id}/selected/{self.link_id}.png",
        )

    def test_calendar_prefix_rejects_unsafe_path_segments(self) -> None:
        with self.assertRaises(ValueError):
            storage.calendar_prefix("../other-user", self.calendar_id)
        with self.assertRaises(ValueError):
            storage.calendar_prefix(7, "../other-calendar")

class CalendarStorageOperationTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        client_patcher = patch.object(storage, "_client", return_value=self.client)
        bucket_patcher = patch.object(storage, "BUCKET", "calendar-bucket")
        wardrobe_bucket_patcher = patch.object(
            storage,
            "WARDROBE_BUCKET",
            "wardrobe-bucket",
        )
        client_patcher.start()
        bucket_patcher.start()
        wardrobe_bucket_patcher.start()
        self.addCleanup(client_patcher.stop)
        self.addCleanup(bucket_patcher.stop)
        self.addCleanup(wardrobe_bucket_patcher.stop)

    def test_upload_fileobj_uses_private_calendar_bucket(self) -> None:
        fileobj = BytesIO(b"image")

        storage.upload_fileobj(fileobj, "calendar/1/id/original.jpg", "image/jpeg")

        self.client.upload_fileobj.assert_called_once_with(
            fileobj,
            "calendar-bucket",
            "calendar/1/id/original.jpg",
            ExtraArgs={"ContentType": "image/jpeg"},
        )

    def test_copy_wardrobe_item_uses_server_side_copy(self) -> None:
        storage.copy_wardrobe_item(
            "wardrobe/1/item.png",
            "calendar/1/id/selected/link.png",
        )

        self.client.copy_object.assert_called_once_with(
            CopySource={"Bucket": "wardrobe-bucket", "Key": "wardrobe/1/item.png"},
            Bucket="calendar-bucket",
            Key="calendar/1/id/selected/link.png",
        )

    def test_copy_calendar_original_to_wardrobe_uses_server_side_copy(self) -> None:
        storage.copy_calendar_original_to_wardrobe(
            "calendar/1/id/original.jpg",
            "wardrobe/1/job/original.jpg",
        )

        self.client.copy_object.assert_called_once_with(
            CopySource={
                "Bucket": "calendar-bucket",
                "Key": "calendar/1/id/original.jpg",
            },
            Bucket="wardrobe-bucket",
            Key="wardrobe/1/job/original.jpg",
        )

    def test_presigned_get_uses_configured_ttl(self) -> None:
        self.client.generate_presigned_url.return_value = "https://signed.example/image"

        result = storage.presigned_get("calendar/1/id/original.jpg", ttl=600)

        self.assertEqual(result, "https://signed.example/image")
        self.client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={
                "Bucket": "calendar-bucket",
                "Key": "calendar/1/id/original.jpg",
            },
            ExpiresIn=600,
        )

    def test_empty_key_does_not_generate_presigned_url(self) -> None:
        self.assertEqual(storage.presigned_get(""), "")
        self.client.generate_presigned_url.assert_not_called()

    def test_delete_objects_deduplicates_and_batches_keys(self) -> None:
        keys = [f"calendar/1/id/items/{index}.png" for index in range(1001)]
        keys.append(keys[0])

        storage.delete_objects(keys)

        self.assertEqual(self.client.delete_objects.call_count, 2)
        first_batch = self.client.delete_objects.call_args_list[0].kwargs
        second_batch = self.client.delete_objects.call_args_list[1].kwargs
        self.assertEqual(len(first_batch["Delete"]["Objects"]), 1000)
        self.assertEqual(len(second_batch["Delete"]["Objects"]), 1)

    @patch("apps.style_calendar.services.storage.delete_objects")
    def test_delete_calendar_lists_only_exact_calendar_prefix(
        self,
        mock_delete_objects,
    ) -> None:
        paginator = self.client.get_paginator.return_value
        paginator.paginate.return_value = [
            {
                "Contents": [
                    {"Key": "calendar/7/calendar-id/original.jpg"},
                    {"Key": "calendar/7/calendar-id/items/1.png"},
                ]
            }
        ]

        storage.delete_calendar(7, "calendar-id")

        paginator.paginate.assert_called_once_with(
            Bucket="calendar-bucket",
            Prefix="calendar/7/calendar-id/",
        )
        deleted_keys = list(mock_delete_objects.call_args.args[0])
        self.assertEqual(
            deleted_keys,
            [
                "calendar/7/calendar-id/original.jpg",
                "calendar/7/calendar-id/items/1.png",
            ],
        )

    def test_missing_bucket_configuration_fails_before_s3_call(self) -> None:
        with (
            patch.object(storage, "BUCKET", ""),
            self.assertRaises(storage.CalendarStorageConfigurationError),
        ):
            storage.presigned_get("calendar/1/id/original.jpg")

        self.client.generate_presigned_url.assert_not_called()
