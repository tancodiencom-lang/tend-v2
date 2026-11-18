from django.urls import path

from . import views

urlpatterns = [
    path("cong/", views.attendance, name="field_attendance"),
    path("tho/", views.workers, name="field_workers"),
    path("anh/<int:pk>/", views.photo, name="field_photo"),
]
