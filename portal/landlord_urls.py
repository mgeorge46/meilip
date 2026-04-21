"""Landlord portal URL namespace — mounted at /landlord/."""
from django.urls import path

from . import views

app_name = "landlord"

urlpatterns = [
    path("", views.LandlordDashboardView.as_view(), name="dashboard"),
    path("houses/", views.LandlordHouseListView.as_view(), name="house-list"),
    path("statements/", views.LandlordStatementListView.as_view(), name="statement-list"),
    path(
        "statements/request/",
        views.LandlordStatementRequestView.as_view(),
        name="statement-request",
    ),
    path(
        "statements/<int:pk>/download/",
        views.LandlordStatementDownloadView.as_view(),
        name="statement-download",
    ),
    path("profile/", views.LandlordProfileView.as_view(), name="profile"),
]
