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
    path("audit/", views.audit_log_view, name="audit-log"),

    # User management (Phase G.3 + G.4)
    path("users/", views.user_list_view, name="user-list"),
    path("users/new/", views.user_create_view, name="user-create"),
    path("users/<int:pk>/", views.user_detail_view, name="user-detail"),
    path("users/<int:pk>/edit/", views.user_edit_view, name="user-edit"),
    path("users/<int:pk>/block/", views.user_block_view, name="user-block"),
    path("users/<int:pk>/unblock/", views.user_unblock_view, name="user-unblock"),
    path("users/<int:pk>/reset-password/", views.user_reset_password_view, name="user-reset-password"),
]
