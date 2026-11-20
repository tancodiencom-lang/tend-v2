from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="marketing"),
    path("bai/<int:pk>/", views.post_detail, name="marketing_post"),
    path("bai/<int:pk>/<slug:action>/", views.post_action, name="marketing_post_action"),
    path("y-tuong/", views.idea_add, name="marketing_idea_add"),
    path("y-tuong/<int:pk>/bo/", views.idea_drop, name="marketing_idea_drop"),
    path("kho-anh/", views.asset_list, name="marketing_assets"),
    path("kho-anh/<int:pk>/doi/", views.asset_toggle, name="marketing_asset_toggle"),
    path("anh/<int:pk>/", views.asset_image, name="marketing_image"),
    path("cai-dat/", views.settings_page, name="marketing_settings"),
]
