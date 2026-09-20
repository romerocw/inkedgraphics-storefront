from django.urls import path

from . import payments, views

urlpatterns = [
    path("cart/", views.cart_view, name="cart"),
    path("cart/add/<slug:slug>/", views.cart_add, name="cart_add"),
    path("cart/update/", views.cart_update, name="cart_update"),
    path("checkout/", views.checkout, name="checkout"),
    path("order/<str:order_number>/pay/", payments.order_pay, name="order_pay"),
    path("order/<str:order_number>/success/", payments.order_success, name="order_success"),
    path("order/<str:order_number>/", views.order_detail, name="order_detail"),
    path("stripe/webhook/", payments.stripe_webhook, name="stripe_webhook"),
]
