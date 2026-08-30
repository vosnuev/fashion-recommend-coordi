from django.contrib import admin

from .models import SharedWardrobeCategory, WardrobeItem, WardrobeUploadJob


@admin.register(WardrobeUploadJob)
class WardrobeUploadJobAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "status", "created_at", "finished_at"]
    list_filter = ["status"]
    search_fields = ["id", "user__username"]
    readonly_fields = ["id", "created_at"]


@admin.register(WardrobeItem)
class WardrobeItemAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "item_name", "category_large",
                    "category_small", "confirmed", "created_at"]
    list_filter = ["category_large", "confirmed"]
    search_fields = ["item_name", "user__username"]
    readonly_fields = ["id", "job", "s3_key", "seg_meta", "created_at", "updated_at"]


@admin.register(SharedWardrobeCategory)
class SharedWardrobeCategoryAdmin(admin.ModelAdmin):
    list_display = ["id", "room", "name", "created_by", "created_at"]
    search_fields = ["name", "room__title", "created_by__username"]
    readonly_fields = ["id", "created_at"]
