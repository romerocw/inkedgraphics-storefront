from django.contrib import admin

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("store_product", "variant", "quantity", "unit_price", "product_name", "variant_label", "sku")
    readonly_fields = ("product_name", "variant_label", "sku")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "store", "buyer_name", "recipient_name", "status", "total", "created_at")
    list_filter = ("status", "store")
    search_fields = ("order_number", "buyer_name", "buyer_email", "recipient_name")
    readonly_fields = ("order_number", "subtotal", "total", "created_at", "updated_at")
    inlines = [OrderItemInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.recalculate()
