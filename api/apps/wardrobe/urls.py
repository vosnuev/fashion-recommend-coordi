from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.wardrobe.views import (
    WardrobeBatchDetailView,
    WardrobeBatchView,
    WardrobeCallbackView,
    WardrobeReindexCallbackView,
    WardrobeFilterListView,
    WardrobeHashtagDetailView,
    WardrobeHashtagItemsView,
    WardrobeHashtagListCreateView,
    WardrobeHashtagOrderView,
    WardrobeItemAddToClosetView,
    WardrobeItemHashtagsView,
    WardrobeItemDetailView,
    WardrobeItemListView,
    WardrobeViewPreferenceView,
    WardrobeUploadJobView,
    WardrobeUploadView,
    SharedWardrobeViewSet,
)

app_name = "wardrobe"

router = DefaultRouter()
router.register(r"shared-wardrobes", SharedWardrobeViewSet, basename="shared-wardrobes")

urlpatterns = [
    path("wardrobe/batches/", WardrobeBatchView.as_view(), name="batch-list-create"),
    path("wardrobe/batches/<uuid:batch_id>/", WardrobeBatchDetailView.as_view(), name="batch-detail"),
    # 옷장 아이템 등록 (비동기)
    path("wardrobe/uploads/", WardrobeUploadView.as_view(), name="upload"),
    path("wardrobe/uploads/<uuid:job_id>/", WardrobeUploadJobView.as_view(), name="upload-job"),
    # 이미지 프로세서 콜백 (내부 토큰 인증)
    path("internal/wardrobe/callback/", WardrobeCallbackView.as_view(), name="callback"),
    path(
        "internal/wardrobe/reindex-callback/",
        WardrobeReindexCallbackView.as_view(),
        name="reindex-callback",
    ),
    # 고정 기본 카테고리와 개인 옷장 해시태그
    path(
        "wardrobe/categories/",
        WardrobeFilterListView.as_view(),
        name="wardrobe-filter-list",
    ),
    path(
        "wardrobe/hashtags/",
        WardrobeHashtagListCreateView.as_view(),
        name="hashtag-list-create",
    ),
    path(
        "wardrobe/hashtags/order/",
        WardrobeHashtagOrderView.as_view(),
        name="hashtag-order",
    ),
    path(
        "wardrobe/hashtags/<uuid:hashtag_id>/",
        WardrobeHashtagDetailView.as_view(),
        name="hashtag-detail",
    ),
    path(
        "wardrobe/hashtags/<uuid:hashtag_id>/items/",
        WardrobeHashtagItemsView.as_view(),
        name="hashtag-items",
    ),
    path(
        "wardrobe/view-preferences/",
        WardrobeViewPreferenceView.as_view(),
        name="view-preferences",
    ),
    # 옷장 아이템 조회·수정·삭제
    path("wardrobe/items/", WardrobeItemListView.as_view(), name="items"),
    path("wardrobe/items/<uuid:item_id>/", WardrobeItemDetailView.as_view(), name="item-detail"),
    path(
        "wardrobe/items/<uuid:item_id>/hashtags/",
        WardrobeItemHashtagsView.as_view(),
        name="item-hashtags",
    ),
    # ── 공유 옷장 (Shared Wardrobe) ──
    path("", include(router.urls)),
    # 룩 사진에서 뽑힌 옷을 옷장에 들이기
    path(
        "wardrobe/items/<uuid:item_id>/add-to-closet/",
        WardrobeItemAddToClosetView.as_view(),
        name="item-add-to-closet",
    ),
]
