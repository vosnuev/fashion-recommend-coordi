import json

from rest_framework.renderers import BaseRenderer


class ServerSentEventRenderer(BaseRenderer):
    """DRF가 EventSource의 Accept 헤더를 406으로 거절하지 않게 한다."""

    media_type = "text/event-stream"
    format = "event-stream"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None or isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode(self.charset)
        # StreamingHttpResponse는 이 메서드를 거치지 않는다. 여기에는 인증·404 등
        # 스트림 시작 전 DRF 오류만 들어오므로 단일 JSON 본문으로 안전하게 반환한다.
        return json.dumps(data, ensure_ascii=False, default=str).encode(self.charset)
