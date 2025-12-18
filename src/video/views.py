import logging
import os
import base64
import cv2
import math
import numpy as np
from django.http import FileResponse, HttpResponse, JsonResponse
from django.db.models import Q
from django.conf import settings
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from rest_framework import serializers
from .models import Video, Project, VideoData, ObjectTrack, ActivityLog
from .serializers import (
    ProjectUploadSerializer, 
    VideoSerializer, 
    FrameObjectRangeSerializer,
    FrameInfoSerializer,
    ProjectSerializer,
    ListUniqueIdsSerializer,
    ObjectTrackDetailsSerializer,
    LinkObjectSerializer,
    ActivityLogSerializer,
    ActivityLogRequestSerializer,
    BreakObjectSerializer,
    SwapObjectSerializer,
    DeleteObjectSerializer
)

# Import Movie class from movies.py and Trk from TrkFile.py
from .movies import Movie
from .TrkFile import Trk

logger = logging.getLogger(__name__)


def replace_nan_with_none(obj):

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
    parser_classes = (MultiPartParser, FormParser, JSONParser)
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
        responses={206: 'Partial Content', 200: 'Full Content', 404: 'Not Found'},
    )
    @action(detail=True, methods=['get'], url_path='stream')
    def stream(self, request, pk=None):
        """
        GET /videos/{id}/stream/ → Stream video file with HTTP Range support.
        Falls back to Project model if Video doesn't exist.
        """
        try:
            # Try to get from Video model first
            video = self.get_object()
            file_path = video.video_file.path
        except Video.DoesNotExist:
            # Fall back to Project model
            try:
                project = Project.objects.get(pk=pk)
                video_folder = os.path.join(settings.MEDIA_ROOT, "video_folder")
                if project.video_name:
                    file_path = os.path.join(video_folder, project.video_name)
                else:
                    return JsonResponse({"error": "Video filename missing in project"}, status=404)
                
                if not os.path.exists(file_path):
                    return JsonResponse({"error": f"Video file not found at {file_path}"}, status=404)
            except Project.DoesNotExist:
                return JsonResponse({"error": "Video or Project not found"}, status=404)
        except Exception as e:
            logger.error("Error streaming video: %s", str(e), exc_info=True)
            raise
        
        try:
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
            logger.error("Error streaming video file: %s", str(e), exc_info=True)
            return JsonResponse({"error": str(e)}, status=500)

    @swagger_auto_schema(
        operation_description="Get a list of frame numbers and base64-encoded thumbnails for the video. Useful for carousel or preview UI.",
        responses={200: 'JSON with frame thumbnails'},
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
        manual_parameters=[
            openapi.Parameter(
                'frame_number', openapi.IN_PATH, type=openapi.TYPE_INTEGER, required=True, description='Frame number to fetch'
            )
        ],
        responses={200: 'JSON with frame image and TRK data', 404: 'Frame not found'},
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
        responses={200: 'TRK file', 404: 'TRK file not found'},
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
        manual_parameters=[
            openapi.Parameter(
                'frame_number', openapi.IN_PATH, type=openapi.TYPE_INTEGER, required=True, description='Frame number to fetch'
            )
        ],
        responses={200: 'JSON with TRK data', 404: 'TRK file not found', 500: 'Error reading TRK data'},
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
            # Use TrkFile.py API for all relevant data
            frame_data = trk.getframe(frame_number)  # returns frame data (could be ndarray or list)
            # Use startframes, endframes, nframes, etc. from Trk/Tracklet
            startframes = getattr(trk, 'startframes', None)
            endframes = getattr(trk, 'endframes', None)
            nframes = getattr(trk, 'nframes', None)
            # If available, use get_min_max_val, get_idx_vals, etc.
            min_val, max_val = None, None
            if hasattr(trk, 'get_min_max_val'):
                try:
                    min_val, max_val = trk.get_min_max_val()
                except Exception:
                    min_val, max_val = None, None
            # Trajectory: if Trk has a trajectory method, use it; else, fallback to mean
            trajectory = None
            if hasattr(trk, 'trajectory'):
                try:
                    trajectory = trk.trajectory(frame_number)
                except Exception:
                    trajectory = None
            else:
                # fallback: mean of frame_data if possible
                try:
                    arr = np.array(frame_data)
                    trajectory = np.mean(arr, axis=(0, 1)).tolist() if arr.ndim >= 2 else None
                except Exception:
                    trajectory = None
            # Compose response
            result = {
                "frame_number": frame_number,
                "frame_data": replace_nan_with_none(frame_data.tolist() if hasattr(frame_data, 'tolist') else frame_data),
                "startframes": replace_nan_with_none(startframes.tolist() if hasattr(startframes, 'tolist') else startframes),
                "endframes": replace_nan_with_none(endframes.tolist() if hasattr(endframes, 'tolist') else endframes),
                "nframes": replace_nan_with_none(nframes.tolist() if hasattr(nframes, 'tolist') else nframes),
                "min_val": min_val,
                "max_val": max_val,
                "trajectory": replace_nan_with_none(trajectory),
            }
            return JsonResponse(result)
        except Exception as e:
            return JsonResponse({"error": f"Failed to get TRK data: {str(e)}"}, status=500)

    #return object/coordinate data for a start and end frame from DB.
    @swagger_auto_schema(
        operation_description="Return object/coordinate data for a consecutive frame range (max 150 frames) from DB.",
        manual_parameters=[
            openapi.Parameter(
                'start',
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description='Start frame id (inclusive)',
            ),
            openapi.Parameter(
                'end',
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description='End frame id (inclusive, max span 150)',
            ),
        ],
        responses={200: 'JSON payload for the requested frame range'},
    )
    @action(detail=True, methods=['get'], url_path='frame-object-range')
    # def frame_object_range(self, request, pk=None):
    #     """
    #     GET /api/v1/videos/{id}/frame-object-range?start=<start>&end=<end>
    #     """
    #     data = request.query_params.copy()
    #     data['video_id'] = pk
        
    #     serializer = FrameObjectRangeSerializer(data=data)
    #     if serializer.is_valid():
    #         payload = serializer.get_data()
    #         return JsonResponse(payload)
    #     return JsonResponse(serializer.errors, status=400)
    def frame_object_range(self, request, pk=None):
        """
        GET /api/v1/videos/{id}/frame-object-range?start=<start>&end=<end>
        """
        data = request.query_params.copy()
        data['video_id'] = pk
        
        serializer = FrameObjectRangeSerializer(data=data)
        if serializer.is_valid():
            data = serializer.validated_data
            video_id = data['video_id']
            start = data['start']
            end = data['end']

            # Check if end frame is out of range
            max_row = VideoData.objects.filter(video_id=video_id).order_by('-frame_no').first()
            if max_row and end > max_row.frame_no:
                return JsonResponse(
                    {
                        "error": f"Requested end frame {end} is out of range. "
                                f"Last available frame is {max_row.frame_no}."
                    },
                    status=400
                )

            # Identify missing frames in this window
            existing = set(
                VideoData.objects.filter(
                    video_id=video_id,
                    frame_no__gte=start,
                    frame_no__lte=end,
                ).values_list("frame_no", flat=True)
            )

            full_range = range(start, end + 1)
            missing_frames = [f for f in full_range if f not in existing]

            # Fallback for each missing frame → previous valid
            fallback_frames = []
            for f in missing_frames:
                prev = self._get_previous_valid_frame(video_id, f)
                if prev:
                    fallback_frames.append(prev)

            # Merge fallback
            if fallback_frames:
                # We don't alter serializer, only override queryset
                serializer.extra_frames = fallback_frames

            payload = serializer.get_data()
            
            # Remove unwanted fields from response (in-place mutation)
            if 'objects' in payload:
                for obj in payload['objects']:
                    if 'frames' in obj:
                        for frame in obj['frames']:
                            # Delete the fields you don't want
                            frame.pop('confidence', None)
                            frame.pop('tag', None) 
                            frame.pop('timestamp', None)
            
            return JsonResponse(payload)
        return JsonResponse(serializer.errors, status=400)

    #Get frame information by video ID and frame number from database. Returns all tracking data for the specified frame.
    @swagger_auto_schema(
        operation_description="Get frame information by video ID and frame number from database. Returns all tracking data for the specified frame.",
        manual_parameters=[
            openapi.Parameter(
                'video',
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description='Video ID (project_id)',
            ),
            openapi.Parameter(
                'frame',
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description='Frame number',
            ),
        ],
        pagination_class=None,
        responses={200: 'JSON with frame data and tracking information', 400: 'Validation error'},
    )
    @action(detail=False, methods=['get'], url_path='frame')
    def get_frame_info(self, request):
        """
        GET /api/v1/frame?video=ID&frame=NUM → Get frame information from database.
        """
        serializer = FrameInfoSerializer(data=request.query_params)
        if serializer.is_valid():
            video_id = serializer.validated_data['video']
            frame_no = serializer.validated_data['frame']

            # Check if frame is out of range
            max_row = VideoData.objects.filter(video_id=video_id).order_by('-frame_no').first()
            if max_row and frame_no > max_row.frame_no:
                return JsonResponse(
                    {
                        "error": f"Requested frame {frame_no} is out of range. "
                                f"Last available frame is {max_row.frame_no}."
                    },
                    status=400
                )

            # MISSING FRAME FALLBACK
            if not self._has_frame_data(video_id, frame_no):
                fallback = self._get_previous_valid_frame(video_id, frame_no)
                if fallback is None:
                    return JsonResponse(
                        {"error": "No valid previous frame found"},
                        status=404
                    )
                serializer.validated_data['frame'] = fallback

            payload = serializer.get_data()
            return JsonResponse(payload)
        return JsonResponse(serializer.errors, status=400)


    @swagger_auto_schema(
        operation_description="Upload a new video file with TRK data",
        manual_parameters=[
            openapi.Parameter(
                "project_name", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Project name"
            ),
            openapi.Parameter("video_file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Video file"),
            openapi.Parameter("tracking_file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Track file"),
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

    # POST /videos/project-upload/ → expects form-data with project_name, video_file, tracking_file
    @swagger_auto_schema(
        operation_description="Upload project video + TRK data",
        request_body=ProjectUploadSerializer,
        responses={201: "Success", 400: "Validation error", 500: "Server error"},
    )
    @action(detail=False, methods=["post"], url_path="project-upload", 
            parser_classes=[MultiPartParser, FormParser])
    def project_upload(self, request):
        """
        POST /videos/project-upload/ → expects form-data with project_name, video_file, tracking_file
        """
        try:
            if "video_file" not in request.FILES:
                return JsonResponse({
                    "status": "error",
                    "message": "Missing video_file"
                }, status=400)
                
            serializer = ProjectUploadSerializer(data=request.data, context={"request": request})
            
            if not serializer.is_valid():
                return JsonResponse({
                    "status": "error", 
                    "errors": serializer.errors
                }, status=400)
            
            result = serializer.save()
            project_id = result.get("project_id")
            rows_inserted = result.get("rows_inserted", 0)
            
            return JsonResponse({
                "status": "success",
                "project_id": project_id,
                "rows_inserted": rows_inserted,
                "message": "Files saved and TRK data inserted successfully",
            }, status=201)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return JsonResponse({
                "status": "error",
                "message": f"Server error: {str(e)}"
            }, status=500)
    


    #GET project list 
    @swagger_auto_schema(
    operation_description="Get list of all in-progress projects with essential details",
    responses={
        200: ProjectSerializer(many=True),
        400: "Invalid parameters",
        500: "Server error"
    },
    )
    @action(detail=False, methods=['get'], url_path='project-list')
    def project_list(self, request):
        """
        GET /videos/project-list/
        Returns only projects where project_status = 'inprogress'
        Ordered by newest first.
        """
        try:

            projects = Project.objects.filter(Q(project_status="inprogress") | Q(project_status="completed"),status="Completed").order_by('project_id')      

            serializer = ProjectSerializer(projects, many=True)
            return Response(serializer.data, status=200)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response(
                {
                    "status": "error",
                    "message": "Failed to fetch projects",
                    "error": str(e)
                },
                status=500
            )

    @swagger_auto_schema(
        operation_description="Stream the project video file with HTTP Range support.",
        responses={206: 'Partial Content', 200: 'Full Content', 404: 'Not Found'},
    )
    @action(detail=True, methods=['get'], url_path='project-stream')
    def project_stream(self, request, pk=None):
        """
        GET /videos/{id}/project-stream/ → Stream video file from Project model.
        """
        try:
            # Fetch Project manually since ViewSet queryset is Video
            project = Project.objects.get(pk=pk)
            
            # Reconstruct disk path from filename
            video_folder = os.path.join(settings.MEDIA_ROOT, "video_folder")
            if project.video_name:
                file_path = os.path.join(video_folder, project.video_name)
            else:
                return JsonResponse({"error": "Video filename missing in project"}, status=404)
            
            if not file_path or not os.path.exists(file_path):
                return JsonResponse({"error": f"Video file not found at {file_path}"}, status=404)

            range_header = request.headers.get('Range')
            if range_header:
                return self._stream_video_with_range(file_path, range_header)
            
            # Full file
            response = FileResponse(open(file_path, 'rb'), content_type='video/mp4')
            response['Content-Length'] = str(os.path.getsize(file_path))
            response['Accept-Ranges'] = 'bytes'
            response['Cache-Control'] = 'public, max-age=3600'
            return response
        except Project.DoesNotExist:
            return JsonResponse({"error": "Project not found"}, status=404)
        except Exception as e:
            logger.error("Error streaming video: %s", str(e), exc_info=True)
            return JsonResponse({"error": str(e)}, status=500)

    @swagger_auto_schema(
        operation_description="Stream/download the raw TRK file content for the project.",
        responses={200: 'TRK file', 404: 'TRK file not found'},
    )
    @action(detail=True, methods=['get'], url_path='project-stream-trk')
    def project_stream_trk(self, request, pk=None):
        """
        GET /videos/{id}/project-stream-trk/ → Stream/download the TRK file content from Project model.
        """
        logger.info(f"Streaming TRK for project {pk}")
        try:
            project = Project.objects.get(pk=pk)
            # Reconstruct disk path from filename
            track_folder = os.path.join(settings.MEDIA_ROOT, "track_folder")
            if project.trk_file_name:
                trk_path = os.path.join(track_folder, project.trk_file_name)
            else:
                return JsonResponse({"error": "TRK filename missing in project"}, status=404)
        
            if not trk_path or not os.path.exists(trk_path):
                return JsonResponse({"error": "TRK file not found"}, status=404)
                
            response = FileResponse(
                open(trk_path, 'rb'),
                as_attachment=True,
                filename=os.path.basename(trk_path),
                content_type="application/octet-stream",
            )
            response['Cache-Control'] = 'public, max-age=3600'
            return response
        except Project.DoesNotExist:
             return JsonResponse({"error": "Project not found"}, status=404)

    # MISSING-FRAME HELPERS
    def _has_frame_data(self, video_id, frame_no):
        """Check if frame exists in VideoData."""
        return VideoData.objects.filter(
            video_id=video_id,
            frame_no=frame_no
        ).exists()

    def _get_previous_valid_frame(self, video_id, frame_no):
        """Return nearest previous valid frame, else None."""
        row = (
            VideoData.objects
            .filter(video_id=video_id, frame_no__lt=frame_no)
            .order_by('-frame_no')
            .first()
        )
        return row.frame_no if row else None

    @swagger_auto_schema(
        operation_description="Get list of all unique object IDs for the project.",
        responses={200: "List of unique IDs", 400: "Validation error", 500: "Server error"}
    )
    @action(detail=True, methods=['get'], url_path='unique-ids')
    def get_unique_ids(self, request, pk=None):
        """
        GET /api/v1/videos/{project_id}/unique-ids/  
        """
        try:
            serializer = ListUniqueIdsSerializer(data={}, context={"project_id": pk})
            serializer.is_valid(raise_exception=True)

            payload = serializer.get_all_ids()
            return JsonResponse(payload, status=200)

        except serializers.ValidationError as ve:
            return JsonResponse(ve.detail, status=400)

        except Exception as e:
            logger.error(f"Error getting unique IDs: {str(e)}", exc_info=True)
            return JsonResponse({"error": "Server Error", "detail": str(e)}, status=500)


    @swagger_auto_schema(
        operation_description="Get start/end frame for a unique object and check if a frame lies inside the range.",
        manual_parameters=[
            openapi.Parameter(
                "frame", openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
                required=True, description="Frame number to check"
            )
        ],
        responses={200: "Object details", 400: "Validation error", 404: "Not Found", 500: "Server Error"}
    )
    @action(detail=True, methods=["get"], url_path="unique-ids/(?P<object_id>\\d+)")
    def get_unique_id_details(self, request, pk=None, object_id=None):
        """
        GET /api/v1/videos/{project_id}/unique-ids/{object_id}/?frame=NUM
        """
        try:
            frame_value = request.query_params.get("frame")

            if frame_value in (None, "", "null"):
                return JsonResponse(
                    {"frame": ["Frame query parameter is required."]}, status=400
                )

            serializer = ObjectTrackDetailsSerializer(
                data={"object_id": object_id, "frame": frame_value},
                context={"project_id": pk},
            )
            serializer.is_valid(raise_exception=True)

            payload = serializer.get_object_data()
            return JsonResponse(payload, status=200)

        except serializers.ValidationError as ve:
            return JsonResponse({"validation_error": ve.detail}, status=400)

        except ObjectTrack.DoesNotExist:
            return JsonResponse({"error": "Object not found"}, status=404)

        except Exception as e:
            logger.error(f"Error: {str(e)}", exc_info=True)
            return JsonResponse({"error": "Server Error", "detail": str(e)}, status=500)

    
    @swagger_auto_schema(
    method="put",
    operation_description="Merge object_2 into object_1.",
    request_body=LinkObjectSerializer,
    responses={200: "Objects merged successfully"}
    )
    @action(detail=True, methods=["put"], url_path="link-objects")
    def link_objects(self, request, pk=None):
        """
        PUT /api/v1/videos/{video_id}/link-objects/
        """
        try:
            serializer = LinkObjectSerializer(
                data=request.data,
                context={"video_id": pk}
            )
            serializer.is_valid(raise_exception=True)

            payload = serializer.merge_data()
            return JsonResponse(payload)

        except Exception as e:
            logger.error(f"Error linking objects: {str(e)}", exc_info=True)
            return JsonResponse({"error": str(e)}, status=500)


    @swagger_auto_schema(
        method="post",
        operation_description="Create an activity log entry. Each operation creates a new row in audit trail.",
        request_body=ActivityLogSerializer,
        responses={201: "Activity logged successfully", 400: "Validation error", 500: "Internal server error"}
    )
    @action(detail=True, methods=["post"], url_path="add-activity-log")
    def add_activity_log(self, request, pk=None):
        """
        POST /api/v1/videos/{project_id}/add-activity-log/
        """
        try:
            # Clone request data so we can modify it
            data = request.data.copy()
            data["project_id"] = pk

            serializer = ActivityLogSerializer(data=data)
            serializer.is_valid(raise_exception=True)
            serializer.save()

            return Response(
                {
                    "status": "success",
                    "message": "Activity log entry created",
                    "data": serializer.data,
                },
                status=status.HTTP_201_CREATED
            )

        except serializers.ValidationError as ve:
            # DRF validation error -> return friendly error message
            return Response(
                {
                    "status": "error",
                    "message": "Invalid input data",
                    "errors": ve.detail,   # precise field errors
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        except Exception as e:
            # Unexpected system-level error (DB, code bug, etc.)
            logger.error(f"Error creating activity log: {str(e)}", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while creating the activity log.",
                    "errors": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @swagger_auto_schema(
        operation_description="Get all activity logs related to a video using video ID.",
        manual_parameters=[
            openapi.Parameter('video_id', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True, description='Video ID'),
        ],
        responses={200: "List of activity logs", 400: "Validation error", 404: "Video not found", 500: "Server error"},
        pagination_class=None
    )
    @action(detail=False, methods=['get'], url_path='activity/logs')
    def get_activity_logs(self, request):
        """
        GET /api/v1/videos/activity/logs?video_id=ID 
        """
        try:
            serializer = ActivityLogRequestSerializer(data=request.query_params)

            # VALIDATION FAILS
            if not serializer.is_valid():
                return JsonResponse(
                    {"error": serializer.errors},
                    status=400
                )

            # FETCH DATA (Serializer handles database logic)
            payload = serializer.get_data()

            # If serializer returns empty projects or logs
            if payload.get("total_logs", 0) == 0:
                return JsonResponse(
                    {
                        "message": "No activity logs found for this video_id",
                        "video_id": payload.get("video_id")
                    },
                    status=404
                )

            return JsonResponse(payload, safe=False, status=200)

        except Project.DoesNotExist:
            return JsonResponse(  
                {"error": "Video or associated project not found"},
                status=404
            )

        except Exception as e:
            # LOG THIS (for debugging)
            print("ERROR in get_activity_logs:", str(e))

            return JsonResponse(
                {"error": "An unexpected error occurred", "details": str(e)},
                status=500
            )


    @swagger_auto_schema(
        method="post",
        operation_description=(
            "Break an object track into two at a given frame. "
            "The original object is split into two active objects. "
            "All operations are performed atomically."
        ),
        request_body=BreakObjectSerializer,
        responses={
            200: "Object break operation completed successfully",
            400: "Validation error",
            500: "Internal server error",
        },
    )
    @action(detail=True, methods=["post"], url_path="objects/break")
    def break_object(self, request, pk=None):
        """
        POST /api/v1/videos/{project_id}/objects/break/
        """
        try:
            serializer = BreakObjectSerializer(
                data=request.data,
                context={"project_id": pk}
            )
            serializer.is_valid(raise_exception=True)
            result = serializer.save()

            return Response(
                {
                    "status": "success",
                    "message": "Object break operation completed successfully",
                    "data": result,
                },
                status=status.HTTP_200_OK,
            )

        except serializers.ValidationError as ve:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid input data",
                    "errors": ve.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as e:
            logger.error(f"Error during break operation: {str(e)}", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while breaking the object.",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        method="put",
        operation_description="Swap object_1 with object_2 inside VideoData and ObjectTrack.",
        request_body=SwapObjectSerializer,
        responses={200: "Swap successful", 400: "Validation error"}
    )
    @action(detail=True, methods=["put"], url_path="swap-objects")
    def swap_objects(self, request, pk=None):
        try:
            serializer = SwapObjectSerializer(
                data=request.data,
                context={"video_id": pk}
            )
            serializer.is_valid(raise_exception=True)
            result = serializer.swap_data()
            return JsonResponse(result, status=200)

        except serializers.ValidationError as ve:
            return JsonResponse({"validation_error": ve.detail}, status=400)

        except Exception as e:
            logger.error(f"Swap Error: {str(e)}")
            return JsonResponse({"error": "Swap failed", "details": str(e)}, status=500)



    @swagger_auto_schema(
        method="post",
        operation_description="Delete (nullify) an active object from video_data within a given frame range. Operation is allowed only if the object is active.",
        request_body=DeleteObjectSerializer,
        responses={200: "Object delete operation completed successfully", 400: "Validation error", 500: "Internal server error"},
    )
    @action(detail=True, methods=["post"], url_path="objects/delete")
    def delete_object(self, request, pk=None):
        """
        POST /api/v1/videos/{project_id}/objects/delete/
        """
        try:
            serializer = DeleteObjectSerializer(
                data=request.data,
                context={"project_id": pk}
            )
            serializer.is_valid(raise_exception=True)
            result = serializer.save()

            return Response({"status": "success", "message": "Object deleted successfully", "data": result,}, status=status.HTTP_200_OK,)

        except serializers.ValidationError as ve:
            return Response({"status": "error", "message": "Delete operation not allowed", "errors": ve.detail,}, status=status.HTTP_400_BAD_REQUEST,)

        except Exception as e:
            logger.error(f"Error during delete object operation: {str(e)}", exc_info=True)
            return Response({"status": "error", "message": "Something went wrong while deleting the object", "errors": str(e),}, status=status.HTTP_500_INTERNAL_SERVER_ERROR,)
