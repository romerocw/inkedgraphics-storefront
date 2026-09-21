from django.urls import path

from . import views

app_name = "console"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("login/", views.ConsoleLoginView.as_view(), name="login"),
    path("logout/", views.ConsoleLogoutView.as_view(), name="logout"),
    path("profile/", views.MyAccountView.as_view(), name="my_account"),
    path("password/", views.ConsolePasswordChangeView.as_view(), name="password_change"),
    path("forgot/", views.ConsolePasswordResetView.as_view(), name="password_reset"),
    path("forgot/sent/", views.ConsolePasswordResetSentView.as_view(), name="password_reset_sent"),
    path("reset/<uidb64>/<token>/", views.ConsolePasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", views.ConsolePasswordResetDoneView.as_view(), name="password_reset_done"),
    path("clients/", views.ClientListView.as_view(), name="clients"),
    path("clients/new/", views.ClientCreateView.as_view(), name="client_new"),
    path("clients/<int:pk>/", views.ClientUpdateView.as_view(), name="client_edit"),
    path("stores/", views.StoreListView.as_view(), name="stores"),
    path("stores/new/", views.StoreCreateView.as_view(), name="store_new"),
    path("stores/<int:pk>/", views.StoreDetailView.as_view(), name="store_detail"),
    path("stores/<int:pk>/edit/", views.StoreUpdateView.as_view(), name="store_edit"),
    path("stores/<int:pk>/products/", views.StoreProductsView.as_view(), name="store_products"),
    path("stores/<int:pk>/products/add/", views.StoreProductsAddView.as_view(), name="store_products_add"),
    path("stores/<int:pk>/orders.csv", views.StoreOrdersCSVView.as_view(), name="store_orders_csv"),
    path("orders/", views.OrderListView.as_view(), name="orders"),
    path("orders/bulk/", views.OrdersBulkView.as_view(), name="orders_bulk"),
    path("orders/<str:order_number>/", views.OrderDetailView.as_view(), name="order_detail"),
    path("orders/<str:order_number>/status/", views.OrderStatusView.as_view(), name="order_status"),
    path("products/", views.ProductListView.as_view(), name="products"),
    path("products/new/", views.ProductCreateView.as_view(), name="product_new"),
    path("products/<int:pk>/", views.ProductUpdateView.as_view(), name="product_edit"),
]
