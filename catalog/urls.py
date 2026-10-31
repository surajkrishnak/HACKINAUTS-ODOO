from django.urls import path
from catalog import views

app_name = "catalog"

urlpatterns = [
    # Taxes
    path("taxes/", views.TaxListView.as_view(), name="tax_list"),
    path("taxes/create/", views.TaxCreateView.as_view(), name="tax_create"),
    path("taxes/<int:pk>/edit/", views.TaxUpdateView.as_view(), name="tax_edit"),
    path("taxes/<int:pk>/toggle/", views.TaxToggleActiveView.as_view(), name="tax_toggle"),

    # Products
    path("products/", views.ProductListView.as_view(), name="product_list"),
    path("products/create/", views.ProductCreateView.as_view(), name="product_create"),
    path("products/<int:pk>/", views.ProductDetailView.as_view(), name="product_detail"),
    path("products/<int:pk>/edit/", views.ProductUpdateView.as_view(), name="product_edit"),

    # Plans
    path("plans/", views.PlanListView.as_view(), name="plan_list"),
    path("plans/create/", views.PlanCreateView.as_view(), name="plan_create"),
    path("plans/<int:pk>/", views.PlanDetailView.as_view(), name="plan_detail"),
    path("plans/<int:pk>/edit/", views.PlanUpdateView.as_view(), name="plan_edit"),
    path("plans/<int:pk>/action/<str:action>/", views.PlanActionView.as_view(), name="plan_action"),

    # Discounts
    path("discounts/", views.DiscountListView.as_view(), name="discount_list"),
    path("discounts/create/", views.DiscountCreateView.as_view(), name="discount_create"),
    path("discounts/<int:pk>/edit/", views.DiscountUpdateView.as_view(), name="discount_edit"),
]
