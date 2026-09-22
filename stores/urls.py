from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    # Before the store page so the social card keeps a stable, unsigned URL of its own.
    path("<slug:slug>/share-image.png", views.share_image, name="store_share_image"),
    path("<slug:slug>/", views.store_detail, name="store_detail"),
]
