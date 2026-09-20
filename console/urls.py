from django.urls import path

from . import views

app_name = "console"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("login/", views.ConsoleLoginView.as_view(), name="login"),
    path("logout/", views.ConsoleLogoutView.as_view(), name="logout"),
    path("clients/", views.ClientListView.as_view(), name="clients"),
    path("clients/new/", views.ClientCreateView.as_view(), name="client_new"),
    path("clients/<int:pk>/", views.ClientUpdateView.as_view(), name="client_edit"),
    path("stores/", views.StoreListView.as_view(), name="stores"),
    path("stores/new/", views.StoreCreateView.as_view(), name="store_new"),
    path("stores/<int:pk>/", views.StoreUpdateView.as_view(), name="store_edit"),
]
