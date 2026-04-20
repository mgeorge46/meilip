"""URL configuration for meili_property project."""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("accounting/", include("accounting.urls")),
    path("core/", include("core.urls")),
    path("billing/", include("billing.urls")),
    path("", include("dashboard.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom error handlers
handler403 = "dashboard.views.handler403"
handler404 = "dashboard.views.handler404"
handler500 = "dashboard.views.handler500"
