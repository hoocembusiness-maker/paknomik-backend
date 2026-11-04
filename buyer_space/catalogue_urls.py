from django.urls import path

from buyer_space import catalogue_api

urlpatterns = [
    path("filters/", catalogue_api.FilterView.as_view(), name="api_paksuites_filters"),
    path("favorites/", catalogue_api.FavoriteListView.as_view(), name="api_paksuites_favorites"),
    path("ensembles/", catalogue_api.EnsembleListView.as_view(), name="api_paksuites_ensembles"),
    path(
        "ensembles/<slug:slug>/favorite/",
        catalogue_api.FavoriteToggleView.as_view(),
        name="api_paksuites_favorite",
    ),
    path(
        "ensembles/<slug:slug>/quote/",
        catalogue_api.EnsembleQuickQuoteView.as_view(),
        name="api_paksuites_ensemble_quote",
    ),
    path(
        "ensembles/<slug:slug>/",
        catalogue_api.EnsembleDetailView.as_view(),
        name="api_paksuites_ensemble",
    ),
    path(
        "lines/<int:line_id>/quote/",
        catalogue_api.LineQuoteView.as_view(),
        name="api_paksuites_line_quote",
    ),
    path(
        "admin/ensembles/",
        catalogue_api.AdminEnsembleListView.as_view(),
        name="api_paksuites_admin_ensembles",
    ),
    path(
        "admin/ensembles/<str:code>/",
        catalogue_api.AdminEnsembleDetailView.as_view(),
        name="api_paksuites_admin_ensemble",
    ),
]
