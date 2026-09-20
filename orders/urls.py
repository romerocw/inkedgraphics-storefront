from django.urls import path

from . import views

urlpatterns = [
    path("cart/", views.cart_view, name="cart"),
    path("cart/add/<slug:slug>/", views.cart_add, name="cart_add"),
    path("cart/update/", views.cart_update, name="cart_update"),
    path("checkout/", views.checkout, name="checkout"),
    path("order/<str:order_number>/pay/", views.order_pay, name="order_pay"),
    path("order/<str:order_number>/", views.order_detail, name="order_detail"),
]
