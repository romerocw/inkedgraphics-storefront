from django.contrib import admin

from .models import Product, ProductVariant, StoreProduct


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku_prefix", "default_price", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "sku_prefix")
    inlines = [ProductVariantInline]


@admin.register(StoreProduct)
class StoreProductAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "product", "price", "is_active", "sort_order")
    list_filter = ("store", "is_active")
    search_fields = ("display_name", "product__name", "store__name")
