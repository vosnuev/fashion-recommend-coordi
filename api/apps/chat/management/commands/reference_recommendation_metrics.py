from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.chat.services.reference_recommendation_metrics import (
    CloudWatchReferenceRecommendationEventSource,
    ReferenceRecommendationMetricsError,
    ReferenceRecommendationMetricsQuery,
    aggregate_reference_recommendation_metrics,
)


def _datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CommandError(f"ISO 8601 시각 형식이 아닙니다: {value}") from exc
    if parsed.tzinfo is None:
        raise CommandError("--start와 --end에는 시간대가 포함되어야 합니다.")
    return parsed


def _parse_jsonl(stream: TextIO) -> list[dict]:
    events: list[dict] = []
    for line_number, raw_line in enumerate(stream, start=1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise CommandError(
                f"JSONL {line_number}번째 줄이 올바른 JSON이 아닙니다."
            ) from exc
        if not isinstance(event, dict):
            raise CommandError(
                f"JSONL {line_number}번째 줄은 JSON 객체여야 합니다."
            )
        events.append(event)
    return events


def _jsonl_events(path: str) -> list[dict]:
    if path == "-":
        return _parse_jsonl(sys.stdin)
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return _parse_jsonl(stream)
    except OSError as exc:
        raise CommandError(f"이벤트 파일을 열 수 없습니다: {path}") from exc


class Command(BaseCommand):
    help = "레퍼런스 추천 운영 이벤트를 기간·모드별 지표로 집계합니다."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--start", required=True, help="조회 시작 ISO 8601 시각")
        parser.add_argument("--end", required=True, help="조회 종료 ISO 8601 시각")
        parser.add_argument(
            "--mode",
            choices=("WARDROBE_BASED", "NEW_ITEM"),
            help="추천 모드 필터",
        )
        parser.add_argument(
            "--response-mode",
            choices=("DEFAULT", "STYLIST"),
            help="기본/스타일리스트 응답 필터",
        )
        parser.add_argument(
            "--input",
            help="로컬 JSONL 경로. '-'이면 표준 입력, 생략하면 CloudWatch 조회",
        )
        parser.add_argument(
            "--log-group",
            default=settings.REFERENCE_RECOMMENDATION_LOG_GROUP,
            help="CloudWatch 로그 그룹",
        )
        parser.add_argument(
            "--region",
            default=settings.AWS_REGION,
            help="AWS 리전",
        )

    def handle(self, *args, **options) -> None:
        query = ReferenceRecommendationMetricsQuery(
            started_at=_datetime(options["start"]),
            ended_at=_datetime(options["end"]),
            recommendation_mode=options.get("mode"),
            is_stylist=(
                True
                if options.get("response_mode") == "STYLIST"
                else False
                if options.get("response_mode") == "DEFAULT"
                else None
            ),
        )
        try:
            if options.get("input"):
                events = _jsonl_events(options["input"])
            else:
                log_group = str(options.get("log_group") or "").strip()
                if not log_group:
                    raise CommandError(
                        "REFERENCE_RECOMMENDATION_LOG_GROUP 또는 --log-group이 필요합니다."
                    )
                events = CloudWatchReferenceRecommendationEventSource(
                    log_group_name=log_group,
                    region_name=options["region"],
                    limit=settings.REFERENCE_RECOMMENDATION_QUERY_LIMIT,
                ).fetch(query)
            metrics = aggregate_reference_recommendation_metrics(
                events,
                query=query,
            )
        except (ReferenceRecommendationMetricsError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(metrics, ensure_ascii=False, indent=2))
