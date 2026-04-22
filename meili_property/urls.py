"""URL configuration for meili_property project."""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.urls import include, path


def healthz(request):
    """Liveness probe for docker-compose / nginx / LB."""
    return HttpResponse("ok", content_type="text/plain")


def readyz(request):
    """Readiness probe — verifies DB is reachable."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return JsonResponse({"status": "ready"})
    except Exception as e:  # pragma: no cover - plumbing
        return JsonResponse({"status": "error", "detail": str(e)}, status=503)


urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("accounting/", include("accounting.urls")),
    path("core/", include("core.urls")),
    path("billing/", include("billing.urls")),
    path("scoring/", include("scoring.urls")),
    path("notifications/", include("notifications.urls")),
    path("api/", include("api.urls")),
    path("tenant/", include(("portal.tenant_urls", "tenant"), namespace="tenant")),
    path("landlord/", include(("portal.landlord_urls", "landlord"), namespace="landlord")),
    path("", include("dashboard.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom error handlers
handler403 = "dashboard.views.handler403"
handler404 = "dashboard.views.handler404"
handler500 = "dashboard.views.handler500"
