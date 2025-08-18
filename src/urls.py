import os
from django.conf import settings
from django.http import JsonResponse
from django.urls import path, re_path, include
from django.conf.urls.static import static
# from django.conf.urls import url
from django.contrib import admin
from django.views.generic.base import RedirectView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from drf_yasg.views import get_schema_view
from drf_yasg import openapi

from src.files.urls import files_router
from rest_framework import permissions
from src.users.urls import users_router
from src.video.urls import video_router
from src.video.views import VideoViewSet 

schema_view = get_schema_view(
    openapi.Info(
        title="BFC API",
        default_version="v1",
        description="API documentation for BFC project",
    ),
    public=True,
    permission_classes=(permissions.AllowAny,),
    url=os.environ.get('BASE_URL'),
)

router = DefaultRouter()
router.registry.extend(users_router.registry)
router.registry.extend(files_router.registry)
router.registry.extend(video_router.registry)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('summernote/', include('django_summernote.urls')),
    path('api/v1/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/v1/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/v1/', include((router.urls, 'v1'))),
    # path('api/v1/videos/upload/', VideoViewSet.as_view(), name='video-upload'),
    path('swagger<str:format>/', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    path('health/', include('health_check.urls')),
    re_path(r'^$', RedirectView.as_view(url='/swagger/', permanent=False)),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# ✅ Custom 404 handler
def custom_page_not_found(request, exception):
    return JsonResponse(
        {
            "status": "failure",
            "message": "Invalid API endpoint.",
            "error": "The requested URL was not found on this server.",
        },
        status=404,
    )


handler404 = 'src.urls.custom_page_not_found'
