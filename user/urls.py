"""
URL routes for server-rendered pages in the `user` app
(dashboards, add product, supplier list). Maps paths to views.py.
"""

from django.urls import path

from . import views

urlpatterns = [
    # Authentication
    path('signup/', views.signup_view, name='signup'),
    path('complete-profile/', views.complete_profile, name='complete_profile'),
    path('dashboard/', views.user_dashboard, name='user_dashboard'),
    path('edit-profile/', views.edit_profile, name='edit_profile'),

    # Client
    path('client-dashboard/', views.client_dashboard, name='client_dashboard'),

    # Supplier
    path('become-supplier/', views.become_supplier, name='become_supplier'),
    path('supplier-dashboard/', views.supplier_dashboard, name='supplier_dashboard'),
    path('supplier-products/', views.supplier_products, name='supplier_products'),
    path('add-product/', views.add_product, name='add_product'),
    path('edit-product/<int:product_id>/', views.edit_product, name='edit_product'),

    # Search & browse
    path('search/', views.search_products, name='search'),
    path('suppliers/', views.suppliers_list, name='suppliers'),
    path('supplier/<int:supplier_id>/', views.supplier_detail, name='supplier_detail'),
    path('supplier/<int:supplier_id>/devis/', views.request_quote, name='request_quote'),
    path('product/<int:product_id>/', views.product_detail, name='product_detail'),
    path('contact-supplier/<int:supplier_id>/', views.contact_supplier, name='contact_supplier'),
    path('supplier-quote-forms/', views.supplier_quote_forms, name='supplier_quote_forms'),
    path(
        'supplier-quote-forms/<int:category_id>/',
        views.supplier_quote_form_edit,
        name='supplier_quote_form_edit',
    ),
    path('compare/', views.compare_products, name='compare_products'),

    # API
    path('api/top-suppliers/', views.api_top_suppliers, name='api_top_suppliers'),
    path('api/search-suggestions/', views.api_search_suggestions, name='api_search_suggestions'),
]
