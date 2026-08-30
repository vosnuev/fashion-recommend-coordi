from __future__ import annotations

from django.conf import settings

from apps.chat.services.identity import guest_ttl


class ChatGuestCookieRefreshMiddleware:
    """유효한 게스트 활동이 있으면 HttpOnly 쿠키의 7일 TTL도 함께 연장한다."""

    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        raw_token = getattr(request, "chat_guest_cookie_refresh_token", "")
        if raw_token:
            response.set_cookie(
                settings.CHAT_GUEST_COOKIE_NAME,
                raw_token,
                max_age=int(guest_ttl().total_seconds()),
                httponly=True,
                secure=settings.CHAT_GUEST_COOKIE_SECURE,
                samesite=settings.CHAT_GUEST_COOKIE_SAMESITE,
                path="/api/v1/",
            )
        return response
