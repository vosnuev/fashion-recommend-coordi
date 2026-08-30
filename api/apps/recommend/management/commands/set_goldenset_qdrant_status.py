"""기존 Qdrant 골든 코디의 데이터셋 상태를 재임베딩 없이 갱신한다."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from qdrant_client import models as qm

from apps.goldenset.models import GoldenDataset
from apps.recommend.services.qdrant import GOLDEN_OUTFIT_COLLECTION, get_client


def _normalize_status(value: object) -> str:
    status = str(value or "").strip().upper()
    if status not in GoldenDataset.Status.values:
        allowed = ", ".join(GoldenDataset.Status.values)
        raise CommandError(
            f"지원하지 않는 골든셋 상태입니다: {status!r} (허용값: {allowed})"
        )
    return status


def _selector(dataset_version: str, source_status: str | None) -> qm.Filter:
    must: list[qm.Condition] = [
        qm.FieldCondition(
            key="dataset_version",
            match=qm.MatchValue(value=dataset_version),
        )
    ]
    if source_status:
        must.append(
            qm.Filter(
                should=[
                    qm.FieldCondition(
                        key="dataset_status",
                        match=qm.MatchValue(value=source_status),
                    ),
                    qm.FieldCondition(
                        key="status",
                        match=qm.MatchValue(value=source_status),
                    ),
                ]
            )
        )
    return qm.Filter(must=must)


class Command(BaseCommand):
    help = "기존 Qdrant 골든 코디의 데이터셋 상태 payload를 갱신한다."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--dataset-version",
            required=True,
            help="갱신할 Qdrant payload의 dataset_version",
        )
        parser.add_argument(
            "--status",
            required=True,
            help="변경할 상태 (PILOT/DRAFT/ACTIVE/ARCHIVED)",
        )
        parser.add_argument(
            "--from-status",
            help="현재 상태가 이 값인 포인트만 변경",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="대상 개수만 확인하고 payload는 변경하지 않음",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        dataset_version = str(options["dataset_version"] or "").strip()
        if not dataset_version:
            raise CommandError("--dataset-version은 비어 있을 수 없습니다.")
        target_status = _normalize_status(options["status"])
        source_status = (
            _normalize_status(options["from_status"])
            if options.get("from_status")
            else None
        )
        selector = _selector(dataset_version, source_status)
        client = get_client()
        try:
            count = int(
                client.count(
                    collection_name=GOLDEN_OUTFIT_COLLECTION,
                    count_filter=selector,
                    exact=True,
                ).count
            )
        except Exception as exc:
            raise CommandError(f"Qdrant 대상 조회 실패: {exc}") from exc

        source_label = source_status or "모든 상태"
        self.stdout.write(
            f"대상: dataset_version={dataset_version}, {source_label} → "
            f"{target_status}, {count}건"
        )
        if count == 0:
            raise CommandError(
                "상태를 변경할 골든 코디가 없습니다. 버전과 현재 상태를 확인하세요."
            )
        if options["dry_run"]:
            self.stdout.write("dry-run: Qdrant payload를 변경하지 않았습니다.")
            return

        try:
            client.set_payload(
                collection_name=GOLDEN_OUTFIT_COLLECTION,
                payload={
                    "status": target_status,
                    "dataset_status": target_status,
                },
                points=selector,
                wait=True,
            )
        except Exception as exc:
            raise CommandError(f"Qdrant 상태 갱신 실패: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Qdrant 골든 코디 {count}건 갱신 완료"))
