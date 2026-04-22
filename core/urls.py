from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    # Landlord
    path("landlords/", views.LandlordListView.as_view(), name="landlord-list"),
    path("landlords/new/", views.LandlordCreateView.as_view(), name="landlord-create"),
    path("landlords/<int:pk>/", views.LandlordDetailView.as_view(), name="landlord-detail"),
    path("landlords/<int:pk>/edit/", views.LandlordUpdateView.as_view(), name="landlord-update"),
    path("landlords/<int:pk>/delete/", views.LandlordDeleteView.as_view(), name="landlord-delete"),

    # Estate
    path("estates/", views.EstateListView.as_view(), name="estate-list"),
    path("estates/new/", views.EstateCreateView.as_view(), name="estate-create"),
    path("estates/<int:pk>/", views.EstateDetailView.as_view(), name="estate-detail"),
    path("estates/<int:pk>/edit/", views.EstateUpdateView.as_view(), name="estate-update"),
    path("estates/<int:pk>/delete/", views.EstateDeleteView.as_view(), name="estate-delete"),

    # House
    path("houses/", views.HouseListView.as_view(), name="house-list"),
    path("houses/new/", views.HouseCreateView.as_view(), name="house-create"),
    path("houses/<int:pk>/", views.HouseDetailView.as_view(), name="house-detail"),
    path("houses/<int:pk>/edit/", views.HouseUpdateView.as_view(), name="house-update"),
    path("houses/<int:pk>/delete/", views.HouseDeleteView.as_view(), name="house-delete"),

    # Tenant
    path("tenants/", views.TenantListView.as_view(), name="tenant-list"),
    path("tenants/new/", views.TenantCreateView.as_view(), name="tenant-create"),
    path("tenants/<int:pk>/", views.TenantDetailView.as_view(), name="tenant-detail"),
    path("tenants/<int:pk>/edit/", views.TenantUpdateView.as_view(), name="tenant-update"),
    path("tenants/<int:pk>/message/", views.TenantMessageView.as_view(), name="tenant-message"),
    path("tenants/<int:pk>/delete/", views.TenantDeleteView.as_view(), name="tenant-delete"),

    # Tenancy (TenantHouse)
    path("tenancies/new/", views.TenantHouseCreateView.as_view(), name="tenancy-create"),
    path("tenancies/<int:pk>/activate/", views.TenantHouseActivateView.as_view(), name="tenancy-activate"),
    path("tenancies/<int:pk>/exit/", views.TenantHouseExitView.as_view(), name="tenancy-exit"),

    # Employee
    path("employees/", views.EmployeeListView.as_view(), name="employee-list"),
    path("employees/new/", views.EmployeeCreateView.as_view(), name="employee-create"),
    path("employees/<int:pk>/", views.EmployeeDetailView.as_view(), name="employee-detail"),
    path("employees/<int:pk>/edit/", views.EmployeeUpdateView.as_view(), name="employee-update"),
    path("employees/<int:pk>/delete/", views.EmployeeDeleteView.as_view(), name="employee-delete"),

    # Supplier
    path("suppliers/", views.SupplierListView.as_view(), name="supplier-list"),
    path("suppliers/new/", views.SupplierCreateView.as_view(), name="supplier-create"),
    path("suppliers/<int:pk>/", views.SupplierDetailView.as_view(), name="supplier-detail"),
    path("suppliers/<int:pk>/edit/", views.SupplierUpdateView.as_view(), name="supplier-update"),
    path("suppliers/<int:pk>/delete/", views.SupplierDeleteView.as_view(), name="supplier-delete"),
]
