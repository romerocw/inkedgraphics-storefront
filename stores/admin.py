from django.contrib import admin

from .models import Client, Store


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "primary_color", "contact_email", "created_at")
    search_fields = ("name", "contact_email")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("name", "client", "status", "opens_at", "closes_at")
    list_filter = ("status", "client")
    search_fields = ("name", "slug", "client__name")
    prepopulated_fields = {"slug": ("name",)}
