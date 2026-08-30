from django.urls import path

from apps.style_calendar.views import (
    CalendarEntryByDateView,
    CalendarEntryDetailView,
    CalendarEntryListView,
    CalendarPhotoCreateView,
    CalendarProcessingStatusView,
    CalendarWardrobeCreateView,
    CalendarWardrobeItemLinkView,
    CalendarWardrobeItemUnlinkView,
)

app_name = "style_calendar"

urlpatterns = [
    path(
        "calendars/photo/",
        CalendarPhotoCreateView.as_view(),
        name="calendar-photo-create",
    ),
    path(
        "calendars/wardrobe/",
        CalendarWardrobeCreateView.as_view(),
        name="calendar-wardrobe-create",
    ),
    path("calendars/", CalendarEntryListView.as_view(), name="calendar-list"),
    path(
        "calendars/by-date/",
        CalendarEntryByDateView.as_view(),
        name="calendar-by-date",
    ),
    path(
        "calendars/<uuid:calendar_id>/",
        CalendarEntryDetailView.as_view(),
        name="calendar-detail",
    ),
    path(
        "calendars/<uuid:calendar_id>/items/",
        CalendarWardrobeItemLinkView.as_view(),
        name="calendar-item-link",
    ),
    path(
        "calendars/<uuid:calendar_id>/items/<uuid:wardrobe_item_id>/",
        CalendarWardrobeItemUnlinkView.as_view(),
        name="calendar-item-unlink",
    ),
    path(
        "calendars/<uuid:calendar_id>/processing-status/",
        CalendarProcessingStatusView.as_view(),
        name="calendar-processing-status",
    ),
]
