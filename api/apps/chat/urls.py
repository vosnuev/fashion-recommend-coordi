from django.urls import path

from apps.chat.views import (
    ChatAttachmentMoodAnalysisView,
    ChatAttachmentMoodDecisionView,
    ChatRunDetailView,
    ChatRunEventStreamView,
    ChatRunPersonaAlternativeView,
    ChatRunPersonaRetryView,
    ChatSessionAttachmentUploadView,
    ChatSessionDeriveView,
    ChatSessionDetailView,
    ChatSessionListCreateView,
    ChatSessionMessageListView,
    ChatSessionMessagePageView,
    ChatSessionResponseModeView,
    ChatSessionSearchView,
    GuestClaimView,
    GuestIdentityView,
    StylistListView,
)

app_name = "chat"

urlpatterns = [
    path("chat/stylists/", StylistListView.as_view(), name="stylist-list"),
    path("chat/guest/", GuestIdentityView.as_view(), name="guest-identity"),
    path("chat/guest/claim/", GuestClaimView.as_view(), name="guest-claim"),
    path("chat/sessions/", ChatSessionListCreateView.as_view(), name="session-list"),
    path(
        "chat/sessions/search/",
        ChatSessionSearchView.as_view(),
        name="session-search",
    ),
    path(
        "chat/sessions/<uuid:session_id>/",
        ChatSessionDetailView.as_view(),
        name="session-detail",
    ),
    path(
        "chat/sessions/<uuid:session_id>/response-mode/",
        ChatSessionResponseModeView.as_view(),
        name="session-response-mode",
    ),
    path(
        "chat/sessions/<uuid:session_id>/derive/",
        ChatSessionDeriveView.as_view(),
        name="session-derive",
    ),
    path(
        "chat/sessions/<uuid:session_id>/messages/",
        ChatSessionMessageListView.as_view(),
        name="session-messages",
    ),
    path(
        "chat/sessions/<uuid:session_id>/messages/page/",
        ChatSessionMessagePageView.as_view(),
        name="session-message-page",
    ),
    path(
        "chat/sessions/<uuid:session_id>/attachments/",
        ChatSessionAttachmentUploadView.as_view(),
        name="session-attachment-upload",
    ),
    path(
        "chat/sessions/<uuid:session_id>/attachments/<uuid:attachment_id>/analysis/",
        ChatAttachmentMoodAnalysisView.as_view(),
        name="attachment-mood-analysis",
    ),
    path(
        "chat/sessions/<uuid:session_id>/attachments/<uuid:attachment_id>/mood-decision/",
        ChatAttachmentMoodDecisionView.as_view(),
        name="attachment-mood-decision",
    ),
    path(
        "chat/runs/<uuid:run_id>/",
        ChatRunDetailView.as_view(),
        name="run-detail",
    ),
    path(
        "chat/runs/<uuid:run_id>/events/",
        ChatRunEventStreamView.as_view(),
        name="run-events",
    ),
    path(
        "chat/runs/<uuid:run_id>/personas/<str:persona_id>/retry/",
        ChatRunPersonaRetryView.as_view(),
        name="run-persona-retry",
    ),
    path(
        "chat/runs/<uuid:run_id>/personas/<str:persona_id>/alternative/",
        ChatRunPersonaAlternativeView.as_view(),
        name="run-persona-alternative",
    ),
]
