from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.global_search, name="search"),
    path("coming-soon/<slug:feature>/", views.coming_soon, name="coming-soon"),
]
