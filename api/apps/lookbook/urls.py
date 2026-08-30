from django.urls import path

from apps.lookbook.views import (
    DiscoveryLookDetailView,
    DiscoveryLookListView,
    DiscoveryLookCoverView,
    LookbookDetailView,
    LookbookListView,
    LookbookPhotoCreateView,
    LookbookProcessingStatusView,
    LookbookPublicFeedView,
    LookbookWardrobeCreateView,
)

app_name = "lookbook"

urlpatterns = [
    path(
        "lookbooks/discover/",
        DiscoveryLookListView.as_view(),
        name="lookbook-discover",
    ),
    path(
        "lookbooks/discover/<str:look_id>/",
        DiscoveryLookDetailView.as_view(),
        name="lookbook-discover-detail",
    ),
    path(
        "lookbooks/discover/<str:external_id>/cover/",
        DiscoveryLookCoverView.as_view(),
        name="lookbook-discover-cover",
    ),
    path(
        "lookbooks/photo/",
        LookbookPhotoCreateView.as_view(),
        name="lookbook-photo-create",
    ),
    path(
        "lookbooks/wardrobe/",
        LookbookWardrobeCreateView.as_view(),
        name="lookbook-wardrobe-create",
    ),
    # 공개 피드는 <uuid> 상세보다 먼저 와야 한다 — 'public' 이 uuid 로 안 잡히긴 하지만
    # 읽는 사람 눈에도 목록 계열이 나란히 있는 편이 낫다.
    path("lookbooks/public/", LookbookPublicFeedView.as_view(), name="lookbook-public"),
    path("lookbooks/", LookbookListView.as_view(), name="lookbook-list"),
    path(
        "lookbooks/<uuid:lookbook_id>/",
        LookbookDetailView.as_view(),
        name="lookbook-detail",
    ),
    path(
        "lookbooks/<uuid:lookbook_id>/processing-status/",
        LookbookProcessingStatusView.as_view(),
        name="lookbook-processing-status",
    ),
]
