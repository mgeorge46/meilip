"""Employee-only monitoring views for NotificationDelivery.

Lightweight list + detail — used for ops troubleshooting (which tenant
didn't receive their receipt, what was the provider response, etc.).
"""
from django.urls import path

from .views import NotificationDeliveryDetail, NotificationDeliveryList

app_name = "notifications"

urlpatterns = [
    path("", NotificationDeliveryList.as_view(), name="delivery-list"),
    path("<int:pk>/", NotificationDeliveryDetail.as_view(), name="delivery-detail"),
]
