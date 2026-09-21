from django.contrib import admin

from .models import OutboxEmail


@admin.register(OutboxEmail)
class OutboxEmailAdmin(admin.ModelAdmin):
    list_display = ["created_at", "kind", "to_email", "subject", "status", "attempts", "sent_at"]
    list_filter = ["status", "kind"]
    search_fields = ["to_email", "subject"]
