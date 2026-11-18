from django.urls import path

from . import views

urlpatterns = [
    path("bao-cao/", views.hub, name="reports"),
    path("bao-cao/thu-chi/", views.ledger, name="money_ledger"),
    path("bao-cao/cong-no/", views.debts, name="money_debts"),
    path("bao-cao/misa/", views.misa_export, name="money_misa"),
    path("doi-tuong/", views.parties, name="money_parties"),
    path("chung-tu/<int:pk>/", views.voucher, name="money_voucher"),
]
