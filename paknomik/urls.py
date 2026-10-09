"""
URL configuration for paknomik project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf.urls.static import static  # type: ignore
from django.conf import settings  # type: ignore
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.views.static import serve

from user.oauth_views import GoogleOAuthLoginView
urlpatterns = [
    path('admin/', admin.site.urls)
    ,path('',include('home.urls')),
    path('',include('user.urls')),
    path('api/', include('user.api.urls')),
    path('auth/', include('django.contrib.auth.urls')),
]
if settings.DEBUG:
    urlpatterns.append(path("__reload__/", include("django_browser_reload.urls")))
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
if settings.SERVE_MEDIA and not settings.DEBUG:
    urlpatterns.append(
        re_path(
            r'^media/(?P<path>.*)$',
            serve,
            {'document_root': settings.MEDIA_ROOT},
        )
    )
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
urlpatterns += staticfiles_urlpatterns()
urlpatterns.append(
    path('accounts/google/login/', GoogleOAuthLoginView.as_view(), name='google_login')
)
urlpatterns.append(path('accounts/', include('allauth.urls')))