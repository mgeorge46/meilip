from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password-reset/", views.password_reset_request, name="password-reset"),
    path("password-reset/<uuid:token>/", views.password_reset_confirm, name="password-reset-confirm"),
    path("password-change/", views.password_change, name="password-change"),
    path("profile/", views.profile_view, name="profile"),
]
