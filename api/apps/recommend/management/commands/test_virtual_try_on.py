"""로컬 이미지 두 장으로 가상 착장 결과를 확인한다."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.recommend.services.mixed_outfit_render import OutfitRenderError
from apps.recommend.services.virtual_try_on import VirtualTryOnService


class Command(BaseCommand):
    help = "전신 사진과 코디 이미지로 Qwen 가상 착장 테스트를 실행합니다."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser) -> None:
        parser.add_argument("--person", required=True, help="사용자 전신 사진 경로")
        parser.add_argument("--outfit", required=True, help="추천 코디 이미지 경로")
        parser.add_argument(
            "--mode",
            choices=["person", "mannequin"],
            default="person",
        )
        parser.add_argument("--output", default="virtual_try_on_result.png")
        # 이전 테스트 명령과의 호환을 위해 받기만 한다.
        parser.add_argument("--mannequin-output")

    def handle(self, *args, **options) -> None:
        try:
            person = Path(options["person"]).read_bytes()
            outfit = Path(options["outfit"]).read_bytes()
        except OSError as exc:
            raise CommandError(f"입력 이미지를 읽지 못했습니다: {exc}") from exc

        service = VirtualTryOnService()
        try:
            if options["mode"] == "mannequin":
                result = service.fit_mannequin(person, outfit)
            else:
                result = service.fit_person(person, outfit)
        except OutfitRenderError as exc:
            raise CommandError(str(exc)) from exc

        output_path = Path(options["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(result.content)
        self.stdout.write(self.style.SUCCESS(f"착장 결과: {output_path.resolve()}"))
