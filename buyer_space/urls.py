from django.urls import path

from buyer_space import views

urlpatterns = [
    path("account/", views.BuyerAccountView.as_view(), name="api_buyer_space_account"),
    path("entries/", views.BuyerPragmadinarEntryListView.as_view(), name="api_buyer_entries"),
    path("projects/", views.ProjectListCreateView.as_view(), name="api_buyer_projects"),
    path("projects/<int:pk>/", views.ProjectDetailView.as_view(), name="api_buyer_project_detail"),
    path(
        "projects/<int:pk>/<slug:kind>/",
        views.ProjectLinkView.as_view(),
        name="api_buyer_project_links",
    ),
    path(
        "packaging-items/",
        views.PackagingItemListCreateView.as_view(),
        name="api_buyer_packaging_items",
    ),
    path(
        "packaging-items/<int:pk>/",
        views.PackagingItemDetailView.as_view(),
        name="api_buyer_packaging_item_detail",
    ),
    path("products/", views.BuyerProductListCreateView.as_view(), name="api_buyer_space_products"),
    path(
        "products/<int:pk>/",
        views.BuyerProductDetailView.as_view(),
        name="api_buyer_space_product_detail",
    ),
    path(
        "library-products/",
        views.LibraryProductListView.as_view(),
        name="api_buyer_library_products",
    ),
    path(
        "packaging-types/",
        views.PackagingTypePickerView.as_view(),
        name="api_buyer_packaging_types",
    ),
    path(
        "suite-kits/",
        views.SuiteKitPickerView.as_view(),
        name="api_buyer_suite_kits",
    ),
    path(
        "service-availability/",
        views.ServiceAvailabilityView.as_view(),
        name="api_buyer_service_availability",
    ),
    path(
        "service-requests/",
        views.ServiceRequestListCreateView.as_view(),
        name="api_buyer_service_requests",
    ),
    path(
        "service-requests/<int:pk>/",
        views.ServiceRequestDetailView.as_view(),
        name="api_buyer_service_request_detail",
    ),
    path(
        "service-requests/<int:pk>/messages/",
        views.ServiceRequestMessageView.as_view(),
        name="api_buyer_service_request_messages",
    ),
    path(
        "service-requests/<int:pk>/transition/",
        views.ServiceRequestTransitionView.as_view(),
        name="api_buyer_service_request_transition",
    ),
    path(
        "service-requests/<int:pk>/deliverables/",
        views.ServiceRequestDeliverableView.as_view(),
        name="api_buyer_service_request_deliverables",
    ),
    path(
        "service-requests/<int:pk>/paksuites-options/",
        views.PaksuitesOptionView.as_view(),
        name="api_buyer_paksuites_options",
    ),
    path(
        "paksuites-options/<int:pk>/quotes/",
        views.PaksuitesQuoteView.as_view(),
        name="api_buyer_paksuites_quotes",
    ),
]
