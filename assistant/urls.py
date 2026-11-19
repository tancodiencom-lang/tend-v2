from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="assistant"),
    path("viec/<int:pk>/<slug:action>/", views.act, name="assistant_act"),
]
