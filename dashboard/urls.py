from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.global_search, name="search"),
    path("kpi/", views.kpi_api, name="kpi-api"),
    path("coming-soon/<slug:feature>/", views.coming_soon, name="coming-soon"),
]
