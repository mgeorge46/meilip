"""Auth forms: login, password reset request/confirm, profile edit."""
from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

User = get_user_model()


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True}))
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        min_length=1,
    )

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        password = cleaned.get("password")
        if email and password:
            user = authenticate(self.request, username=email, password=password)
            if user is None:
                raise ValidationError("Invalid email or password.", code="invalid_login")
            if not user.is_active:
                raise ValidationError("This account is disabled.", code="inactive")
            self.user = user
        return cleaned


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField()


class PasswordResetConfirmForm(forms.Form):
    new_password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password1")
        p2 = cleaned.get("new_password2")
        if p1 and p2 and p1 != p2:
            raise ValidationError("The two passwords do not match.")
        if p1:
            validate_password(p1, user=self.user)
        return cleaned


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone", "profile_picture"]


class PasswordChangeForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput)
    new_password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        pw = self.cleaned_data.get("current_password")
        if not self.user.check_password(pw):
            raise ValidationError("Current password is incorrect.")
        return pw

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password1")
        p2 = cleaned.get("new_password2")
        if p1 and p2 and p1 != p2:
            raise ValidationError("The two passwords do not match.")
        if p1:
            validate_password(p1, user=self.user)
        return cleaned


# ---------------------------------------------------------------------------
# Admin user management — Phase G.4
# ---------------------------------------------------------------------------
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password as _validate_pw
from .models import Role


class AdminUserCreateForm(forms.Form):
    """Admin creates a new User row, optionally bound to an existing
    Tenant or Landlord profile, and assigns one or more roles in a single
    submit."""
    email = forms.EmailField()
    phone = forms.CharField(max_length=16)
    first_name = forms.CharField(max_length=80)
    last_name = forms.CharField(max_length=80)
    password = forms.CharField(
        required=False, widget=forms.PasswordInput,
        help_text="Leave blank to auto-generate a one-time password.",
    )
    force_password_change = forms.BooleanField(
        required=False, initial=True,
        help_text="Require the user to change their password on first login.",
    )
    roles = forms.MultipleChoiceField(
        choices=Role.Name.choices,
        widget=forms.CheckboxSelectMultiple,
        required=True,
    )
    bind_tenant = forms.IntegerField(
        required=False, widget=forms.HiddenInput,
        help_text="Optional Tenant.pk to bind to via core.Tenant.user OneToOne.",
    )
    bind_landlord = forms.IntegerField(
        required=False, widget=forms.HiddenInput,
        help_text="Optional Landlord.pk.",
    )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if phone and get_user_model().objects.filter(phone=phone).exists():
            raise forms.ValidationError("A user with this phone already exists.")
        return phone

    def clean_password(self):
        pw = self.cleaned_data.get("password") or ""
        if pw:
            _validate_pw(pw)
        return pw

    def clean(self):
        data = super().clean()
        # If user picks TENANT or LANDLORD role, profile binding is offered;
        # forbid binding both. The view enforces the actual OneToOne checks.
        if data.get("bind_tenant") and data.get("bind_landlord"):
            raise forms.ValidationError("Cannot bind a single user to both a tenant and a landlord profile.")
        return data


class AdminUserEditForm(forms.ModelForm):
    """Admin edits an existing User's basic fields + role membership."""
    roles = forms.MultipleChoiceField(
        choices=Role.Name.choices,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )
    force_password_change = forms.BooleanField(required=False)

    class Meta:
        model = get_user_model()
        fields = ["first_name", "last_name", "phone"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["roles"].initial = self.instance.active_role_names()
            self.fields["force_password_change"].initial = self.instance.force_password_change

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if phone:
            qs = get_user_model().objects.filter(phone=phone)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("Another user already uses this phone.")
        return phone
