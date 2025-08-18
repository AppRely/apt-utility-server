# views.py
import logging
from rest_framework import viewsets
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from .models import Video
from .serializers import VideoSerializer

logger = logging.getLogger(__name__)

class VideoViewSet(viewsets.ModelViewSet):
    queryset = Video.objects.all()
    serializer_class = VideoSerializer
    http_method_names = ['get', 'post']  # Only allow read + create
    parser_classes = (MultiPartParser, FormParser)  # Support file uploads
    permission_classes = [IsAuthenticated]  # Require authentication

    @swagger_auto_schema(
    operation_description="Upload a new video file",
    manual_parameters=[
        openapi.Parameter(
            name="file",
            in_=openapi.IN_FORM,
            type=openapi.TYPE_FILE,
            description="Video file to upload (e.g., .mp4, .avi, .mov, .mkv)",
            required=True,
        ),
        openapi.Parameter(
            name="title",
            in_=openapi.IN_FORM,
            type=openapi.TYPE_STRING,
            description="Title of the video",
            required=False,
            example="Sample Video",
        ),
        openapi.Parameter(
            name="description",
                in_=openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                description="Optional description of the video",
                required=False,
                example="A sample video description",
            ),
        ],
        responses={
            201: openapi.Response("Video uploaded successfully", VideoSerializer),
            400: openapi.Response("Bad Request", examples={
                "application/json": {
                    "file": ["Only video files (.mp4, .avi, .mov, .mkv) are allowed."]
                }
            }),
        }
    )
    def create(self, request, *args, **kwargs):
        """POST /videos/ → upload a video file"""
        logger.info("VideoViewSet.create called by user: %s with data: %s", request.user, request.data)
        try:
            response = super().create(request, *args, **kwargs)
            logger.info("Video uploaded successfully by user: %s", request.user)
            return response
        except Exception as e:
            logger.error("Error uploading video: %s", str(e), exc_info=True)
            raise

    @swagger_auto_schema(
        operation_description="List all videos",
        responses={200: VideoSerializer(many=True)}
    )
    def list(self, request, *args, **kwargs):
        """GET /videos/ → list videos"""
        logger.info("VideoViewSet.list called by user: %s", request.user)
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Retrieve a single video by ID",
        responses={200: VideoSerializer()}
    )
    def retrieve(self, request, *args, **kwargs):
        """GET /videos/{id}/ → retrieve single video"""
        logger.info("VideoViewSet.retrieve called by user: %s", request.user)
        return super().retrieve(request, *args, **kwargs)