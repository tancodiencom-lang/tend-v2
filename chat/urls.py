from django.urls import path

from . import views

urlpatterns = [
    path("telegram/<str:secret>/", views.telegram_webhook, name="chat_telegram"),
    path("ket-noi/", views.connect, name="chat_connect"),
]
