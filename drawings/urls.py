from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="drawings_index"),
    path("bo-autocad/", views.kit, name="drawings_kit"),
    path("bo-autocad/tai/", views.kit_download, name="drawings_kit_download"),
    path("<str:code>/", views.view, name="drawings_view"),
    path("<str:code>/khoi-luong/", views.quantities, name="drawings_quantities"),
    path("<str:code>/nhap/", views.upload, name="drawings_upload"),
    path("<str:code>/nhap/<str:token>/", views.preview, name="drawings_preview"),
]
