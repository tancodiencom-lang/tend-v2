from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core import views as core_views
from overview import views as overview_views

admin.site.site_header = "Tend · Thiết lập"
urlpatterns = [
    path("", core_views.home, name="home"),
    path("dang-nhap/", core_views.Login.as_view(), name="login"),
    path("dang-xuat/", auth_views.LogoutView.as_view(), name="logout"),
    path("tong-quan/", overview_views.overview, name="overview"),
    path("tong-quan/<str:code>/", overview_views.overview_project, name="overview_project"),
    path("ban-ve/", include("drawings.urls")),
    path("bao-cao/", overview_views.reports, name="reports"),
    path("tai-khoan/", overview_views.account, name="account"),
    path("quan-tri/", admin.site.urls),
]
