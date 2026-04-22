from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from simple_history.admin import SimpleHistoryAdmin

from .models import AuditLog, LoginAttempt, PasswordResetToken, Role, User, UserRole


class UserRoleInline(admin.TabularInline):
    model = UserRole
    fk_name = "user"
    extra = 0
    autocomplete_fields = ("role",)


@admin.register(User)
class UserAdmin(SimpleHistoryAdmin, DjangoUserAdmin):
    ordering = ("email",)
    list_display = (
        "email",
        "first_name",
        "last_name",
        "phone",
        "is_active",
        "is_staff",
        "force_password_change",
    )
    list_filter = ("is_active", "is_staff", "is_superuser", "force_password_change")
    search_fields = ("email", "phone", "first_name", "last_name")
    readonly_fields = ("last_login", "last_login_at", "last_login_ip", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "phone", "profile_picture")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "force_password_change",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Login audit", {"fields": ("last_login", "last_login_at", "last_login_ip")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "phone",
                    "first_name",
                    "last_name",
                    "password1",
                    "password2",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
    )
    inlines = [UserRoleInline]


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "is_system", "is_active")
    list_filter = ("is_system", "is_active")
    search_fields = ("name", "description")


@admin.register(UserRole)
class UserRoleAdmin(SimpleHistoryAdmin):
    list_display = ("user", "role", "is_active", "assigned_at", "assigned_by")
    list_filter = ("role", "is_active")
    search_fields = ("user__email", "role__name")
    autocomplete_fields = ("user", "role", "assigned_by")


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = ("email", "success", "ip_address", "timestamp", "failure_reason")
    list_filter = ("success",)
    search_fields = ("email", "ip_address")
    date_hierarchy = "timestamp"
    readonly_fields = tuple(f.name for f in LoginAttempt._meta.fields)


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "token", "created_at", "expires_at", "used_at")
    search_fields = ("user__email", "token")
    readonly_fields = ("token", "created_at", "expires_at", "used_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "actor", "action", "target_type", "target_id", "ip_address")
    list_filter = ("action", "target_type")
    search_fields = ("actor__email", "target_repr", "ip_address", "path")
    date_hierarchy = "timestamp"
    readonly_fields = tuple(f.name for f in AuditLog._meta.fields)

    def has_add_permission(self, request):
        return False  # append-only: only the code writes audit rows

    def has_delete_permission(self, request, obj=None):
        return False  # never delete audit rows
