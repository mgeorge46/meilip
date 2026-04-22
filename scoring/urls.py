from django.urls import path

from . import views

app_name = "scoring"

urlpatterns = [
    path("", views.TenantScoreListView.as_view(), name="score-list"),
]
