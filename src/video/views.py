import logging
import os
import json
import h5py
import base64
import numpy as np
import cv2

from django.http import FileResponse, HttpResponse, JsonResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .models import Video
from .serializers import VideoSerializer

# Import Movie class from movies.py and Trk from TrkFile.py
from .movies import Movie
from .TrkFile import Trk

logger = logging.getLogger(__name__)


def replace_nan_with_none(obj):
    import math

    if isinstance(obj, float) and (math.isnan(obj)):
        return None
    elif isinstance(obj, list):
        return [replace_nan_with_none(x) for x in obj]
    else:
        return obj


class VideoViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing video uploads, streaming, and metadata.
    Uses APT's movies.py Movie class for all video/frame access.
    """

    queryset = Video.objects.all()
    serializer_class = VideoSerializer
    parser_classes = (MultiPartParser, FormParser)
    permission_classes = [AllowAny]

    def _stream_video_with_range(self, file_path, range_header):
        """
        Handle HTTP Range requests for partial content streaming.
        """
        file_size = os.path.getsize(file_path)
        try:
            # Parse Range header
            range_values = range_header.replace('bytes=', '').split('-')
            start = int(range_values[0]) if range_values[0] else 0
            end = int(range_values[1]) if len(range_values) > 1 and range_values[1] else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1
            with open(file_path, 'rb') as f:
                f.seek(start)
                data = f.read(length)
            response = HttpResponse(data, status=206, content_type='video/mp4')
            response['Content-Range'] = f'bytes {start}-{end}/{file_size}'
            response['Accept-Ranges'] = 'bytes'
            response['Content-Length'] = str(length)
            response['Cache-Control'] = 'public, max-age=3600'
            return response
        except Exception as e:
            logger.error("Error processing Range request: %s", str(e), exc_info=True)
            raise

    @swagger_auto_schema(
        operation_description="Stream the video file with HTTP Range support for efficient playback and seeking. Returns partial content if Range header is provided, otherwise streams the full file.",
        responses={206: 'Partial Content', 200: 'Full Content', 404: 'Not Found'}
    )
    @action(detail=True, methods=['get'], url_path='stream')
    def stream(self, request, pk=None):
        """
        GET /videos/{id}/stream/ → Stream video file with HTTP Range support.
        """
        try:
            video = self.get_object()
            file_path = video.video_file.path
            range_header = request.headers.get('Range')
            if range_header:
                return self._stream_video_with_range(file_path, range_header)
            # Full file
            response = FileResponse(open(file_path, 'rb'), content_type='video/mp4')
            response['Content-Length'] = str(os.path.getsize(file_path))
            response['Accept-Ranges'] = 'bytes'
            response['Cache-Control'] = 'public, max-age=3600'
            return response
        except Exception as e:
            logger.error("Error streaming video: %s", str(e), exc_info=True)
            raise

    @swagger_auto_schema(
        operation_description="Get a list of frame numbers and base64-encoded thumbnails for the video. Useful for carousel or preview UI.",
        responses={200: 'JSON with frame thumbnails'}
    )
    @action(detail=True, methods=['get'], url_path='frames')
    def frames(self, request, pk=None):
        """
        GET /videos/{id}/frames/ → Get list of all frame numbers and thumbnails for carousel UI.
        Uses Movie class for frame extraction.
        """
        video = self.get_object()
        video_path = video.video_file.path
        try:
            movie = Movie(video_path)
            total_frames = movie.get_n_frames()
            frame_urls = []
            MAX_FRAMES = min(100, total_frames)
            for i in range(MAX_FRAMES):
                try:
                    frame, _ = movie.get_frame(i)
                except Exception as e:
                    continue
                # For color or grayscale, ensure 3-channel for jpeg
                if len(frame.shape) == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                thumb = cv2.resize(frame, (160, 90))
                _, img_encoded = cv2.imencode('.jpg', thumb)
                thumb_b64 = base64.b64encode(img_encoded).decode('ascii')
                frame_urls.append({"frame_number": i, "thumbnail": f"data:image/jpeg;base64,{thumb_b64}"})
            return JsonResponse({"frames": frame_urls, "total_frames": total_frames})
        except Exception as e:
            return JsonResponse({"error": f"Error extracting frames: {str(e)}"}, status=500)

    @swagger_auto_schema(
        operation_description="Get a specific frame image (base64 JPEG), video metadata, and tracking (TRK) data for the given frame number.",
        manual_parameters=[openapi.Parameter('frame_number', openapi.IN_PATH, type=openapi.TYPE_INTEGER, required=True, description='Frame number to fetch')],
        responses={200: 'JSON with frame image and TRK data', 404: 'Frame not found'}
    )
    @action(detail=True, methods=['get'], url_path='frame/(?P<frame_number>\\d+)')
    def frame(self, request, pk=None, frame_number=None):
        """
        GET /videos/{id}/frame/{frame_number}/ → Get frame image, video metadata, and trk data.
        Uses Movie class for frame extraction.
        """
        video = self.get_object()
        frame_number = int(frame_number)
        video_path = video.video_file.path
        try:
            movie = Movie(video_path)
            nframes = movie.get_n_frames()
            if frame_number < 0 or frame_number >= nframes:
                return JsonResponse({"error": "Frame not found"}, status=404)
            frame, timestamp = movie.get_frame(frame_number)
            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            _, img_encoded = cv2.imencode('.jpg', frame)
            img_b64 = base64.b64encode(img_encoded).decode('ascii')
        except Exception as e:
            return JsonResponse({"error": f"Frame extraction error: {str(e)}"}, status=500)

        trk_path = getattr(video, 'trk_file', None)
        trk_data = None
        if trk_path and os.path.exists(trk_path.path):
            try:
                trk = Trk(trk_path.path)
                frame_trk_data = trk.getframe(frame_number)
                frame_trk_data = frame_trk_data.tolist() if hasattr(frame_trk_data, 'tolist') else str(frame_trk_data)
                frame_trk_data = replace_nan_with_none(frame_trk_data)
                trk_data = frame_trk_data
            except Exception as e:
                trk_data = None

        response = {
            "frame_number": frame_number,
            "timestamp": timestamp,
            "image": f"data:image/jpeg;base64,{img_b64}",
            "trk_data": trk_data,
            "video_id": video.pk,
            "video_metadata": {
                "title": video.video_file_title,
                "description": video.description,
                "uploaded_at": video.uploaded_at,
            },
        }
        return JsonResponse(response)

    @swagger_auto_schema(
        operation_description="Stream/download the raw TRK file content for the video. Returns the file as an attachment.",
        responses={200: 'TRK file', 404: 'TRK file not found'}
    )
    @action(detail=True, methods=['get'], url_path='stream-trk')
    def stream_trk(self, request, pk=None):
        """
        GET /videos/{id}/stream-trk/ → Stream/download the TRK file content.
        """
        video = self.get_object()
        trk_path = getattr(video, 'trk_file', None)
        if not trk_path or not os.path.exists(trk_path.path):
            return JsonResponse({"error": "TRK file not found"}, status=404)
        response = FileResponse(
            open(trk_path.path, 'rb'),
            as_attachment=True,
            filename=os.path.basename(trk_path.path),
            content_type="application/octet-stream",
        )
        response['Cache-Control'] = 'public, max-age=3600'
        return response

    @swagger_auto_schema(
        operation_description="Get tracking (TRK) data for a specific frame number from the TRK file.",
        manual_parameters=[openapi.Parameter('frame_number', openapi.IN_PATH, type=openapi.TYPE_INTEGER, required=True, description='Frame number to fetch')],
        responses={200: 'JSON with TRK data', 404: 'TRK file not found', 500: 'Error reading TRK data'}
    )
    @action(detail=True, methods=['get'], url_path='trk/(?P<frame_number>\\d+)')
    def trk_frame(self, request, pk=None, frame_number=None):
        """
        GET /videos/{id}/trk/{frame_number}/ → Get trk tracking data for a frame.
        """
        video = self.get_object()
        trk_path = getattr(video, 'trk_file', None)
        frame_number = int(frame_number)
        if not trk_path or not os.path.exists(trk_path.path):
            return JsonResponse({"error": "TRK file not found"}, status=404)
        try:
            trk = Trk(trk_path.path)
            frame_trk_data = trk.getframe(frame_number)
            frame_trk_data = frame_trk_data.tolist() if hasattr(frame_trk_data, 'tolist') else str(frame_trk_data)
            frame_trk_data = replace_nan_with_none(frame_trk_data)
            return JsonResponse({"frame_number": frame_number, "trk_data": frame_trk_data})
        except Exception as e:
            return JsonResponse({"error": f"Failed to get TRK data: {str(e)}"}, status=500)

    @swagger_auto_schema(
        operation_description="Upload a new video file with TRK data",
        manual_parameters=[
            openapi.Parameter(
                "project_name", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Project name"
            ),
            openapi.Parameter("video_file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Video file"),
            openapi.Parameter("trk_file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Track file"),
        ],
        responses={201: VideoSerializer, 400: "Bad Request"},
    )
    def create(self, request, *args, **kwargs):
        try:
            response = super().create(request, *args, **kwargs)
            return response
        except Exception as e:
            logger.error("Error uploading video: %s", str(e), exc_info=True)
            raise

    @swagger_auto_schema(operation_description="List all uploaded videos")
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(operation_description="Retrieve video metadata and stream URLs")
    def retrieve(self, request, *args, **kwargs):
        """
        GET /videos/{id}/ → Only return metadata + URLs. (trk not parsed here)
        """
        video = self.get_object()
        video_stream_url = request.build_absolute_uri(f"/api/v1/videos/{video.pk}/stream/")
        trk_stream_url = request.build_absolute_uri(f"/api/v1/videos/{video.pk}/stream-trk/")
        response_data = {
            "id": video.pk,
            "project_name": video.project_name,
            "video_file_title": video.video_file_title,
            "video_stream_url": video_stream_url,
            "trk_file_title": video.trk_file_title,
            "trk_stream_url": trk_stream_url,
            "description": video.description,
            "uploaded_at": video.uploaded_at,
        }
        return Response(response_data)
