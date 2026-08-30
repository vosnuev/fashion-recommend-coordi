from django.urls import path

from .views import (
    DailyLookSaveView,
    DailyLookTodayView,
    DailyLookVirtualTryOnView,
    OutfitAnalysisClaimView,
    OutfitAnalysisDetailView,
    OutfitAnalysisHistoryView,
    OutfitAnalysisView,
    OutfitRenderEventStreamView,
    ProductClickEngagementView,
    ProductClickEventView,
    WishlistAddView,
    WishlistItemView,
    WishlistView,
    RecommendationCardDetailView,
    RecommendationCardRenderView,
    RecommendationCardVirtualTryOnView,
    RecommendationFeedbackView,
    RecommendationHistoryView,
    RecommendationResultDetailView,
    SavedOutfitView,
)

app_name = "recommend"

urlpatterns = [
    path("outfits/analyze/", OutfitAnalysisView.as_view(), name="outfit-analysis"),
    path(
        "outfits/analyses/",
        OutfitAnalysisHistoryView.as_view(),
        name="outfit-analysis-list",
    ),
    # <uuid:...> 컨버터가 "claim"과 겹치지는 않지만, 읽는 순서를 위해 먼저 둔다
    path(
        "outfits/analyses/claim/",
        OutfitAnalysisClaimView.as_view(),
        name="outfit-analysis-claim",
    ),
    path(
        "outfits/analyses/<uuid:analysis_id>/",
        OutfitAnalysisDetailView.as_view(),
        name="outfit-analysis-detail",
    ),
    # 오늘의 룩 — 조회가 곧 생성 트리거다 (사용자 입력이 없는 기능).
    path("looks/today/", DailyLookTodayView.as_view(), name="daily-look-today"),
    # 저장은 본문이 없다. 담을 대상은 그날의 추천 하나로 정해져 있고, golden_id를
    # 클라이언트가 보내게 하면 남의 코디도 담을 수 있는 구멍이 된다.
    path(
        "looks/today/save/",
        DailyLookSaveView.as_view(),
        name="daily-look-save",
    ),
    path(
        "looks/<uuid:look_id>/virtual-try-on/",
        DailyLookVirtualTryOnView.as_view(),
        name="daily-look-virtual-try-on",
    ),
    path(
        "recommendations/",
        RecommendationHistoryView.as_view(),
        name="recommendation-list",
    ),
    path(
        "recommendations/<uuid:result_id>/",
        RecommendationResultDetailView.as_view(),
        name="recommendation-detail",
    ),
    path(
        "recommendations/<uuid:result_id>/cards/<uuid:card_id>/",
        RecommendationCardDetailView.as_view(),
        name="recommendation-card-detail",
    ),
    path(
        "recommendations/<uuid:result_id>/cards/<uuid:card_id>/feedback/",
        RecommendationFeedbackView.as_view(),
        name="recommendation-feedback",
    ),
    path(
        "recommendations/<uuid:result_id>/cards/<uuid:card_id>/save/",
        SavedOutfitView.as_view(),
        name="recommendation-save",
    ),
    path(
        (
            "recommendations/<uuid:result_id>/cards/<uuid:card_id>/"
            "items/<uuid:item_id>/click/"
        ),
        ProductClickEventView.as_view(),
        name="recommendation-product-click",
    ),
    # 찜(판매 상품). 담기는 추천 아이템 경로에서(클릭 수집과 같은 자리),
    # 목록·빼기는 상품 자체를 다루므로 평평한 경로에 둔다.
    path(
        (
            "recommendations/<uuid:result_id>/cards/<uuid:card_id>/"
            "items/<uuid:item_id>/wish/"
        ),
        WishlistAddView.as_view(),
        name="wishlist-add",
    ),
    path("wishlist/", WishlistView.as_view(), name="wishlist"),
    path("wishlist/<uuid:wish_id>/", WishlistItemView.as_view(), name="wishlist-item"),
    path(
        "recommendations/product-clicks/<uuid:product_click_id>/engagement/",
        ProductClickEngagementView.as_view(),
        name="recommendation-product-click-engagement",
    ),
    path(
        "recommendations/<uuid:result_id>/cards/<uuid:card_id>/render/",
        RecommendationCardRenderView.as_view(),
        name="recommendation-card-render",
    ),
    path(
        "recommendations/<uuid:result_id>/cards/<uuid:card_id>/virtual-try-on/",
        RecommendationCardVirtualTryOnView.as_view(),
        name="recommendation-card-virtual-try-on",
    ),
    path(
        "recommendations/render-jobs/<uuid:job_id>/events/",
        OutfitRenderEventStreamView.as_view(),
        name="outfit-render-events",
    ),
]
