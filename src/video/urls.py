from rest_framework.routers import SimpleRouter

from .views import VideoViewSet

video_router = SimpleRouter()
video_router.register(r"videos", VideoViewSet, basename="videos")
