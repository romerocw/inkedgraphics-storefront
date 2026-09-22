from django import template

register = template.Library()


@register.filter
def surcharge(store_product, variant):
    """What a buyer pays on top of the base price for this variant, for the size picker."""
    return store_product.price_for(variant) - store_product.price
