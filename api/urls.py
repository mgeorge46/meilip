"""External API URLs (`/api/v1/...`)."""
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from .views import (
    NotificationSendView,
    NotificationStatusView,
    PaymentWebhookView,
)

app_name = "api"

urlpatterns = [
    path("v1/payments/", PaymentWebhookView.as_view(), name="payments-webhook"),
    path("v1/notifications/", NotificationSendView.as_view(), name="notifications-send"),
    path(
        "v1/notifications/<int:pk>/",
        NotificationStatusView.as_view(), name="notifications-status",
    ),
    path("v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("v1/docs/", SpectacularSwaggerView.as_view(url_name="api:schema"), name="docs"),
]
