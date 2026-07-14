import gzip
import logging
import math
import os
from urllib import response

import orjson
from django.conf import settings
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .services.activity_log_export_service import ActivityLogExportService

from .models import Project, VideoFrame
from .serializers import (
    ActivityLogExportSerializer,
    ActivityLogRequestSerializer,
    ActivityLogSerializer,
    BreakObjectSerializer,
    DeleteObjectSerializer,
    DeleteProjectSerializer,
    FrameInfoSerializer,
    FrameObjectRangeNoFallbackSerializer,
    FrameObjectRangeSerializer,
    LinkObjectSerializer,
    ListUniqueIdsSerializer,
    ObjectTrackDetailsSerializer,
    ProjectSerializer,
    ProjectUploadSerializer,
    RedoSerializer,
    SwapObjectSerializer,
    TrkExportSerializer,
    UndoSerializer,
    FrameConfusionRowSerializer,
    FrameTimelineSerializer,
    InterpolateTrajectorySerializer,
)

from wsgiref.util import FileWrapper
from django.http import StreamingHttpResponse
from rest_framework.decorators import action
import mimetypes
# Import Movie class from movies.py and Trk from TrkFile.py
from .services.frame_timeline_service import FrameTimelineService
import zlib
from .services.confusion_service import ConfusionTableService

from .services.unique_ids_service import UniqueIdsService
from .services.background_executor import executor
from .services.confusion_store_service import ConfusionStoreService
logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024  # 64KB optimal chunk size


def replace_nan_with_none(obj):
    """
    Recursively replace NaN values with None.
    Args:
        obj (Any): Input object (float, list, or nested structure).
    Returns:
        Any: Object with NaN values replaced by None.
    """

    if isinstance(obj, float) and (math.isnan(obj)):
        return None
    if isinstance(obj, list):
        return [replace_nan_with_none(x) for x in obj]
    return obj


class VideoViewSet(viewsets.ModelViewSet):
    """
    ViewSet responsible for video-related operations.

    This includes:
    - Video and project streaming (with HTTP Range support)
    - Frame extraction and preview generation
    - TRK tracking data access
    - Object operations (link, swap, break, delete)
    - Activity log (audit trail) management
    """

    queryset = Project.objects.all()
    serializer_class = ProjectSerializer

    parser_classes = (MultiPartParser, FormParser, JSONParser)
    permission_classes = [AllowAny]

    def _has_frame_data(self, video_id, frame_no):
        """Check if frame exists in VideoFrame."""
        return VideoFrame.objects.filter(project_id=video_id, frame_no=frame_no).exists()

    def _get_previous_valid_frame(self, video_id, frame_no):
        """Return nearest previous valid frame, else None."""
        row = VideoFrame.objects.filter(project_id=video_id, frame_no__lt=frame_no).order_by("-frame_no").first()
        return row.frame_no if row else None
    
    def _file_iterator(self, file_obj, start, length):
        file_obj.seek(start)
        remaining = length

        try:
            while remaining > 0:
                chunk = file_obj.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    break
                yield chunk
                remaining -= len(chunk)
        finally:
            file_obj.close()  # ensures file is closed

    def _stream_video_with_range(self, file_path, range_header):
        """
        Handle HTTP Range requests for partial content streaming.
        """
        file_size = os.path.getsize(file_path)
        try:
            # Parse Range header
            range_values = range_header.replace("bytes=", "").split("-")
            start = int(range_values[0]) if range_values[0] else 0
            end = int(range_values[1]) if len(range_values) > 1 and range_values[1] else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1

            file_obj = open(file_path, "rb")
            # file_obj.seek(start)
            content_type, _ = mimetypes.guess_type(file_path)
            content_type = content_type or "video/mp4"

            response = StreamingHttpResponse(
                self._file_iterator(file_obj, start, length),
                status=206,
                content_type=content_type,
            )

            # Required headers (DO NOT CHANGE – browser depends on these)
            response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
            response["Accept-Ranges"] = "bytes"
            response["Content-Length"] = str(length)
            response["Cache-Control"] = "public, max-age=3600"
            return response
        except Exception as e:
            logger.error("Error processing Range request: %s", str(e), exc_info=True)
            raise

    def _stream_full_video(self, file_path):
        """
        Optimized full video streaming (no memory load)
        """
        file_size = os.path.getsize(file_path)
        file_obj = open(file_path, "rb")
        content_type, _ = mimetypes.guess_type(file_path)
        content_type = content_type or "video/mp4"
        response = StreamingHttpResponse(
            self._file_iterator(file_obj, 0, file_size),
            content_type=content_type,
        )

        response["Content-Length"] = str(file_size)
        response["Accept-Ranges"] = "bytes"
        response["Cache-Control"] = "public, max-age=3600"
        # response["Connection"] = "keep-alive"   # ADD HERE
        return response

    @swagger_auto_schema(
        operation_description="Return object/coordinate data for a consecutive frame range (max 150 frames) from DB.",
        manual_parameters=[
            openapi.Parameter(
                "start",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description="Start frame id (inclusive)",
            ),
            openapi.Parameter(
                "end",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description="End frame id (inclusive, max span 150)",
            ),
        ],
        responses={
            200: "JSON payload for the requested frame range",
            400: "Validation error or frame out of range",
            404: "No valid previous frame found",
            500: "Server error",
        },
    )
    @action(detail=True, methods=["get"], url_path="frame-object-range")
    def frame_object_range(self, request, pk=None):
        """
        GET /api/v1/videos/{id}/frame-object-range?start=<start>&end=<end>
        Retrieve object tracking data for a contiguous range of frames.

        This endpoint returns object coordinates and metadata for all frames
        between the given start and end frame (inclusive). If frame data is
        missing within the requested range, the nearest previous valid frame
        is used as a fallback.

        Args:
            request (Request): Incoming HTTP request containing `start` and `end`
                query parameters.
            pk (int): Video identifier.

        Returns:
            Response: Object-wise tracking data for the requested frame range.
        """
        try:
            data = request.query_params.copy()
            data["video_id"] = pk

            serializer = FrameObjectRangeSerializer(data=data)
            serializer.is_valid(raise_exception=True)

            validated = serializer.validated_data
            video_id = validated["video_id"]
            start = validated["start"]
            end = validated["end"]

            max_row = VideoFrame.objects.filter(project_id=video_id).order_by("-frame_no").first()
            if max_row and end > max_row.frame_no:
                return Response(
                    {
                        "status": "error",
                        "message": "Requested frame range is out of bounds",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            existing_frames = set(
                VideoFrame.objects.filter(
                    project_id=video_id,
                    frame_no__gte=start,
                    frame_no__lte=end,
                ).values_list("frame_no", flat=True)
            )

            missing_frames = [f for f in range(start, end + 1) if f not in existing_frames]

            fallback_frames = []
            for frame in missing_frames:
                prev = self._get_previous_valid_frame(video_id, frame)
                if prev is not None:
                    fallback_frames.append(prev)

            if fallback_frames:
                serializer.extra_frames = fallback_frames

            payload = serializer.get_data()

            return Response(
                {
                    "status": "success",
                    "data": payload,
                },
                status=status.HTTP_200_OK,
            )

        except serializers.ValidationError as ve:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid query parameters",
                    "errors": ve.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception:
            logger.error("Error fetching frame object range", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while fetching frame data",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        operation_description="Get frame information by video ID and frame number from database. Returns all tracking data for the specified frame.",
        manual_parameters=[
            openapi.Parameter(
                "video",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description="Video ID (project_id)",
            ),
            openapi.Parameter(
                "frame",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description="Frame number",
            ),
        ],
        pagination_class=None,
        responses={
            200: "JSON with frame data and tracking information",
            400: "Validation error",
            404: "No valid previous frame found",
            500: "Server error",
        },
    )
    @action(detail=False, methods=["get"], url_path="frame")
    def get_frame_info(self, request):
        """
        GET /api/v1/frame?video=ID&frame=NUM
        Retrieve tracking data for a single frame from the database.

        This endpoint fetches all stored tracking information for a given
        video and frame number. If the requested frame is missing, the system
        automatically falls back to the nearest previous valid frame.

        Args:
            request (Request): Incoming HTTP request containing `video` and
                `frame` query parameters.

        Returns:
            Response: Tracking data for the resolved frame.
        """
        try:
            serializer = FrameInfoSerializer(data=request.query_params)
            serializer.is_valid(raise_exception=True)

            payload = serializer.get_data()

            return Response(
                {
                    "status": "success",
                    "data": payload,
                },
                status=status.HTTP_200_OK,
            )
        except serializers.ValidationError as ve:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid query parameters",
                    "errors": ve.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception:
            logger.error("Error fetching frame info", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while fetching frame data",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        operation_description="Upload project video + TRK data",
        request_body=ProjectUploadSerializer,
        responses={201: "Success", 400: "Validation error", 500: "Server error"},
    )
    @action(detail=False, methods=["post"], url_path="project-upload", parser_classes=[MultiPartParser, FormParser])
    def project_upload(self, request):
        """
        POST /videos/project-upload/

        Upload a project video along with its TRK tracking file.
        This endpoint validates the uploaded files, persists them to disk,
        and processes the TRK file to insert tracking data into the database.

        Args:
            request (Request): Incoming HTTP request with multipart form data.
        Returns:
            Response: Upload result including project ID and number of
            inserted tracking rows.
        """
        try:
            serializer = ProjectUploadSerializer(
                data=request.data,
                context={"request": request},
            )

            serializer.is_valid(raise_exception=True)

            result = serializer.save()

            return Response(
                {
                    "status": "success",
                    "message": "Files saved and TRK data inserted successfully",
                    "data": {
                        "project_id": result.get("project_id"),
                        "rows_inserted": result.get("rows_inserted", 0),
                    },
                },
                status=status.HTTP_201_CREATED,
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

        except Exception:
            logger.error("Error uploading project", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while uploading the project",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        operation_description="Get list of all in-progress projects with essential details",
        responses={200: ProjectSerializer(many=True), 500: "Server error"},
    )
    @action(detail=False, methods=["get"], url_path="project-list")
    def project_list(self, request):
        """
        GET /videos/project-list/

        Retrieve a list of projects with active or completed status.
        Returns projects that are currently in progress or completed,
        ordered by project identifier.

        Args:
            request (Request): Incoming HTTP request.
        Returns:
            Response: List of serialized project records.
        """
        try:
            projects = Project.objects.filter(
                Q(project_status="inprogress") | Q(project_status="completed"), status="Completed"
            ).order_by("project_id")

            serializer = ProjectSerializer(projects, many=True)
            return Response(
                {
                    "status": "success",
                    "data": serializer.data,
                },
                status=status.HTTP_200_OK,
            )

        except Exception:
            logger.error("Failed to fetch projects", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Failed to fetch projects",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    ########################
    # stream video logic
    ########################

    @swagger_auto_schema(
        operation_description="Stream the project video file with HTTP Range support.",
        responses={
            206: "Partial Content",
            200: "Full Content",
            404: "Not Found",
            500: "Server Error",
        },
    )
    @action(detail=True, methods=["get"], url_path="project-stream")
    def project_stream(self, request, pk=None):
        """
        GET /videos/{id}/project-stream/

        Stream a project video file with HTTP Range support.
        Supports partial content delivery to enable efficient seeking
        and playback in video players.

        Args:
            request (Request): Incoming HTTP request.
            pk (int): Project identifier.
        Returns:
            HttpResponse: Video stream response.
        """
        try:
            project = get_object_or_404(Project, pk=pk)

            video_folder = os.path.join(settings.MEDIA_ROOT, "video_folder")

            if not project.video_name:
                return Response(
                    {"status": "error", "message": "Video filename missing in project"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            file_path = os.path.join(video_folder, project.video_name)

            if not os.path.exists(file_path):
                return Response(
                    {"status": "error", "message": "Video file not found on server"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            range_header = request.headers.get("Range")
            if range_header:
                return self._stream_video_with_range(file_path, range_header)

            # Normal request
            return self._stream_full_video(file_path)

        except Exception:
            logger.error("Error streaming video file", exc_info=True)
            return Response(
                {"status": "error", "message": "Something went wrong while streaming the video"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    ########################
    # stream trk logic
    ########################
    @swagger_auto_schema(
        operation_description="Stream/download the raw TRK file content for the project.",
        responses={200: "TRK file", 404: "TRK file not found", 500: "Server error"},
    )
    @action(detail=True, methods=["get"], url_path="project-stream-trk")
    def project_stream_trk(self, request, pk=None):
        """
        GET /videos/{id}/project-stream-trk/

        Stream or download the raw TRK file associated with a project.
        The file is returned as an attachment for download or inspection.

        Args:
            request (Request): Incoming HTTP request.
            pk (int): Project identifier.
        Returns:
            FileResponse: TRK file stream.
        """
        logger.info(f"Streaming TRK for project {pk}")
        try:
            project = get_object_or_404(Project, pk=pk)

            if not project.trk_file_name:
                return Response(
                    {
                        "status": "error",
                        "message": "TRK file not uploaded for this project",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            track_folder = os.path.join(settings.MEDIA_ROOT, "track_folder")
            trk_path = os.path.join(track_folder, project.trk_file_name)

            if not os.path.exists(trk_path):
                return Response(
                    {
                        "status": "error",
                        "message": "TRK file not found on server",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            response = FileResponse(
                open(trk_path, "rb"),
                as_attachment=True,
                filename=os.path.basename(trk_path),
                content_type="application/octet-stream",
            )
            response["Cache-Control"] = "public, max-age=3600"
            return response

        except Exception:
            logger.error("Error streaming TRK file", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while streaming the TRK file",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        operation_description=(
            "Get all unique object IDs for project.\n\n"

            "Behavior:\n"
            "1. Without start_frame/end_frame:\n"
            "   - Returns all unique IDs\n"
            "   - Returns old API response\n\n"

            "2. With start_frame/end_frame:\n"
            "   - Filters objects within range\n"
            "   - Adds start/end coordinates"
        ),

        manual_parameters=[

            openapi.Parameter(
                "start_frame",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=False,
                description=(
                    "Optional start frame"
                ),
            ),

            openapi.Parameter(
                "end_frame",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=False,
                description=(
                    "Optional end frame"
                ),
            ),
        ],

        responses={
            200: "Success",
            400: "Validation error",
            500: "Server error",
        },
    )

    @action( detail=True, methods=["get"], url_path="unique-ids", )
    def get_unique_ids(self, request, pk=None, ):
        """
        GET /api/v1/videos/{project_id}/unique-ids/

        Examples
        --------

        OLD BEHAVIOR:
        /api/v1/videos/1/unique-ids/

        NEW RANGE BEHAVIOR:
        /api/v1/videos/1/unique-ids/?start_frame=100&end_frame=200
        """

        try:

            serializer = (
                ListUniqueIdsSerializer(
                    data=request.query_params,
                    context={
                        "project_id": pk,
                    },
                )
            )

            serializer.is_valid(
                raise_exception=True
            )

            payload = (
                UniqueIdsService.fetch(

                    project_id=pk,

                    start_frame=
                        serializer.validated_data.get(
                            "start_frame"
                        ),

                    end_frame=
                        serializer.validated_data.get(
                            "end_frame"
                        ),
                )
            )

            return Response(
                {
                    "status": "success",
                    "data": payload,
                },
                status=status.HTTP_200_OK,
            )

        except serializers.ValidationError as ve:
            return Response(
                {
                    "status": "error",
                    "message":
                        "Invalid input data",

                    "errors":
                        ve.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception:

            logger.error(
                "Error fetching unique object IDs",
                exc_info=True,
            )

            return Response(
                {
                    "status": "error",

                    "message": (
                        "Something went wrong while "
                        "fetching unique object IDs."
                    ),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        operation_description="Get start/end frame for a unique object and check if a frame lies inside the range.",
        manual_parameters=[
            openapi.Parameter(
                "frame", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True, description="Frame number to check"
            )
        ],
        responses={200: "Object details", 400: "Validation error", 500: "Server Error"},
    )
    @action(detail=True, methods=["get"], url_path="unique-ids/(?P<object_id>\\d+)")
    def get_unique_id_details(self, request, pk=None, object_id=None):
        """
        GET /api/v1/videos/{project_id}/unique-ids/{object_id}/?frame=NUM

        Retrieve start and end frame details for a specific object.
        This endpoint returns the start and end frame for the given object
        and checks whether the provided frame lies within that range.

        Args:
            request (Request): Incoming HTTP request containing `frame`
                query parameter.
            pk (int): Project identifier.
            object_id (int): Object identifier.
        Returns:
            Response: Object frame range and validation result.
        """
        try:
            serializer = ObjectTrackDetailsSerializer(
                data={
                    "object_id": object_id,
                    "frame": request.query_params.get("frame"),
                },
                context={"project_id": pk},
            )
            serializer.is_valid(raise_exception=True)

            payload = serializer.get_object_data()
            return Response(
                {
                    "status": "success",
                    "data": payload,
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

        except Exception:
            logger.error("Error fetching unique object details", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while fetching object details.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        method="put",
        operation_description="Merge object_2 into object_1.",
        request_body=LinkObjectSerializer,
        responses={
            200: "Objects merged successfully",
            400: "Validation error",
            500: "Internal server error",
        },
    )
    @action(detail=True, methods=["put"], url_path="link-objects")
    def link_objects(self, request, pk=None):
        """
        PUT /api/v1/videos/{video_id}/link-objects/

        Merge one object into another within a video.
        The second object is merged into the first, updating all related
        tracking data accordingly.

        Args:
            request (Request): Incoming HTTP request containing object
                merge data in the request body.
            pk (int): Video identifier.
        Returns:
            Response: Result of the merge operation.
        """
        try:
            serializer = LinkObjectSerializer(data=request.data, context={"video_id": pk})
            serializer.is_valid(raise_exception=True)

            result = serializer.merge_data()

            return Response(
                {
                    "status": "success",
                    "message": "Objects merged successfully",
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

        except Exception:
            logger.error("Error linking objects", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while linking objects.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        method="post",
        operation_description="Create an activity log entry. Each operation creates a new row in audit trail.",
        request_body=ActivityLogSerializer,
        responses={201: "Activity logged successfully", 400: "Validation error", 500: "Internal server error"},
    )
    @action(detail=False, methods=["post"], url_path="add-activity-log")
    def add_activity_log(self, request):
        """
        POST /api/v1/videos/{project_id}/add-activity-log/

        Create a new activity log entry for a project.
        Each call creates a single audit trail record describing an
        operation performed on the project.

        Args:
            request (Request): Incoming HTTP request containing activity
                log data in the request body.
            pk (int): Project identifier (from URL).
        Returns:
            Response: Created activity log entry.
        """
        try:
            # data = request.data.copy()
            # data["project_id"] = pk

            serializer = ActivityLogSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            serializer.save()

            return Response(
                {
                    "status": "success",
                    "message": "Activity log entry created",
                    "data": serializer.data,
                },
                status=status.HTTP_201_CREATED,
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
            logger.error(f"Error creating activity log: {str(e)}", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while creating the activity log.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        operation_description="Get all activity logs related to a video using video ID.",
        manual_parameters=[
            openapi.Parameter(
                "video_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True, description="Video ID"
            ),
        ],
        responses={200: "List of activity logs", 400: "Validation error", 500: "Server error"},
        pagination_class=None,
    )
    @action(detail=False, methods=["get"], url_path="activity/logs")
    def get_activity_logs(self, request):
        """
        GET /api/v1/videos/activity/logs?video_id=ID

        Retrieve activity logs associated with a video.
        Returns a chronological list of audit trail entries for the given
        video identifier.

        Args:
            request (Request): Incoming HTTP request containing `video_id`
                as a query parameter.

        Returns:
            Response: Activity log records.
        """
        try:
            serializer = ActivityLogRequestSerializer(data=request.query_params)
            serializer.is_valid(raise_exception=True)

            payload = serializer.get_data()

            return Response({"status": "success", "data": payload}, status=status.HTTP_200_OK)

        except serializers.ValidationError as ve:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid query parameters",
                    "errors": ve.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception:
            logger.error("Error fetching activity logs", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "An unexpected error occurred while fetching activity logs",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    #################################
    # Break api
    #################################
    @swagger_auto_schema(
        method="post",
        operation_description=(
            "Break an object track into two at a given frame. "
            "Supports both Break Before and Break After operations. "
            "The break mode can be specified using the optional "
            "'break_type' query parameter."
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

        Optional Query Parameter:
            break_type:
                - after (default)
                - before

        Examples:
            /objects/break/
            /objects/break/?break_type=after
            /objects/break/?break_type=before
        """

        try:
            break_type = request.query_params.get(
                "break_type",
                "after",
            ).lower()

            serializer = BreakObjectSerializer(
                data=request.data,
                context={
                    "project_id": pk,
                    "break_type": break_type,
                },
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
        responses={200: "Swap successful", 400: "Validation error", 500: "Swap failed"},
    )
    @action(detail=True, methods=["put"], url_path="swap-objects")
    def swap_objects(self, request, pk=None):
        """
        PUT /api/v1/videos/{video_id}/swap-objects/
        Swap two objects within a video.
        This operation exchanges all tracking data between the two specified
        objects.

        Args:
            object_id_1 (int): First object identifier.
            object_id_2 (int): Second object identifier.
            object_1_start_frame (int): Start frame of the first object.
            object_1_end_frame (int): End frame of the first object.
            object_2_start_frame (int): Start frame of the second object.
            object_2_end_frame (int): End frame of the second object.
            pk (int): Video identifier.

        Returns:
            Response: Result of the swap operation.
        """
        try:
            serializer = SwapObjectSerializer(data=request.data, context={"video_id": pk})
            serializer.is_valid(raise_exception=True)
            result = serializer.swap_data()
            return Response(
                {
                    "status": "success",
                    "message": "Objects swapped successfully",
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
            logger.error(f"Error during swap operation: {str(e)}", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while swapping objects.",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        method="post",
        operation_description="Delete (nullify) an active object from video_data within a given frame range. Operation is allowed only if the object is active.",
        request_body=DeleteObjectSerializer,
        responses={
            200: "Object delete operation completed successfully",
            400: "Validation error",
            500: "Internal server error",
        },
    )
    @action(detail=True, methods=["post"], url_path="objects/delete")
    def delete_object(self, request, pk=None):
        """
        POST /api/v1/videos/{project_id}/objects/delete/
        Delete an active object from video_data within a specified
        frame range. This operation is only permitted if the object is currently
        active.
        Args:
            request (Request): Incoming HTTP request containing delete parameters
                in the request body.
            pk (int): Project identifier.
        Returns:
            Response: Result of the delete operation.
        """
        try:
            serializer = DeleteObjectSerializer(data=request.data, context={"project_id": pk})
            serializer.is_valid(raise_exception=True)
            result = serializer.save()
            return Response(
                {
                    "status": "success",
                    "message": "Object deleted successfully",
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
            logger.error(f"Error during delete object operation: {str(e)}", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while deleting the object",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # =========================
    # UNDO
    # =========================
    @swagger_auto_schema(
        method="post",
        operation_description="Undo last applied operation for the project",
        request_body=UndoSerializer,
        responses={
            200: "Undo successful",
            400: "Validation error",
            500: "Internal server error",
        },
    )
    @action(detail=False, methods=["post"], url_path="undo")
    def undo(self, request):
        """
        POST /api/v1/videos/{project_id}/undo/
        """
        try:
            serializer = UndoSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            result = serializer.execute()

            return Response(
                {
                    "status": "success",
                    "message": "Undo operation completed successfully",
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
            logger.error(f"Error during undo operation: {str(e)}", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while undoing the operation",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # =========================
    # REDO
    # =========================
    @swagger_auto_schema(
        method="post",
        operation_description="Redo last undone operation for the project",
        request_body=RedoSerializer,
        responses={
            200: "Redo successful",
            400: "Validation error",
            500: "Internal server error",
        },
    )
    @action(detail=False, methods=["post"], url_path="redo")
    def redo(self, request):
        """
        POST /api/v1/videos/{project_id}/redo/
        """
        try:
            serializer = RedoSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            result = serializer.execute()

            return Response(
                {
                    "status": "success",
                    "message": "Redo operation completed successfully",
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
            logger.error(f"Error during redo operation: {str(e)}", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while redoing the operation",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    ##########################
    # Frame Object Range No Fallback
    ##########################
    @swagger_auto_schema(
        operation_description="Return object/coordinate data for a consecutive frame range (max 900 frames) from DB without fallback.",
        manual_parameters=[
            openapi.Parameter(
                "start",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description="Start frame id (inclusive)",
            ),
            openapi.Parameter(
                "end",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description="End frame id (inclusive, max span 900)",
            ),
        ],
        responses={
            200: "JSON payload for the requested frame range",
            400: "Validation error or frame out of range",
            404: "Project not found",
            500: "Server error",
        },
    )
    # =====================================
    #####################################
    # =====================================
    @action(detail=True, methods=["get"], url_path="frame-object-range-no-fallback")
    def frame_object_range_no_fallback(self, request, pk=None):
        """
        GET /api/v1/videos/{id}/frame-object-range-no-fallback?start=<start>&end=<end>
        Retrieve object tracking data for a contiguous range of frames without fallback.
        """
        try:
            data = request.query_params.copy()
            data["video_id"] = pk

            serializer = FrameObjectRangeNoFallbackSerializer(data=data)
            serializer.is_valid(raise_exception=True)

            payload = serializer.get_data()

            # Safe ORJSON serialization
            try:
                json_data = orjson.dumps(payload, option=orjson.OPT_SERIALIZE_NUMPY)
            except Exception as ser_err:
                logger.error(f"[ORJSON ERROR] {str(ser_err)}", exc_info=True)
                return Response(
                    {
                        "status": "error",
                        "message": "Serialization failed",
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            # Compression
            try:
                compressed_data = gzip.compress(json_data)
            except Exception as comp_err:
                logger.error(f"[GZIP ERROR] {str(comp_err)}", exc_info=True)
                return Response(
                    {
                        "status": "error",
                        "message": "Compression failed",
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            return Response(
                {
                    "status": "success",
                    "data": compressed_data.hex(),
                    "compressed": True,
                },
                status=status.HTTP_200_OK,
            )

        except serializers.ValidationError as ve:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid query parameters",
                    "errors": ve.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as e:
            logger.error(f"[UNEXPECTED ERROR] {str(e)}", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while fetching frame data",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        method="post",
        operation_description="Export updated TRK file (versioned).",
        request_body=TrkExportSerializer,
        responses={
            200: "TRK export successful",
            400: "Validation error",
            500: "Export failed",
        },
    )
    @action(detail=False, methods=["post"], url_path="export-trk")
    def export_trk(self, request):
        """
        POST /api/v1/videos/export-trk/
        """
        try:
            serializer = TrkExportSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            result = serializer.export()

            download_url = request.build_absolute_uri(
                f"/media/trk_exports/{result['project_id']}/project_{result['project_id']}_v{result['trk_version']}.trk"
            )

            return Response(
                {
                    "status": "success",
                    "message": "TRK exported successfully",
                    "data": {
                        "project_id": result["project_id"],
                        "trk_version": result["trk_version"],
                        "download_url": download_url,
                    },
                },
                status=status.HTTP_200_OK,
            )

        except serializers.ValidationError as ve:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid input",
                    "errors": ve.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as e:
            logger.error("TRK export failed", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Failed to export TRK",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        method="delete",
        operation_description="Delete a project, its database records, and all associated local files (video, trk, exports).",
        responses={
            200: openapi.Response(
                description="Project deleted successfully",
                examples={"application/json": {"status": "success", "message": "Project deleted successfully"}},
            ),
            400: "Validation error",
            404: "Project not found",
            500: "Internal server error",
        },
    )
    @action(detail=True, methods=["delete"], url_path="delete-project")
    def delete_project(self, request, pk=None):
        """
        DELETE /api/v1/videos/{id}/delete-project/
        """
        try:
            serializer = DeleteProjectSerializer(data={"project_id": pk})
            serializer.is_valid(raise_exception=True)
            success, message = serializer.execute()

            if success:
                return Response(
                    {
                        "status": "success",
                        "message": message,
                    },
                    status=status.HTTP_200_OK,
                )
            return Response(
                {
                    "status": "error",
                    "message": message,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        except serializers.ValidationError as ve:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid project ID",
                    "errors": ve.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception as e:
            logger.error(f"Error during project deletion: {str(e)}", exc_info=True)
            return Response(
                {
                    "status": "error",
                    "message": "Something went wrong while deleting the project",
                    "errors": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )



    # =====================================
    # CONFUSION / UNCERTAINTY TABLE API
    # =====================================
    @swagger_auto_schema(
        operation_description=(
            "Return stored confusion events."
        ),
        responses={
            200: "Confusion table fetched successfully",
            400: "Validation error",
            500: "Server error",
        },
    )

    @action(
        detail=True,
        methods=["get"],
        url_path="confusion-table",
    )

    def confusion_table(
        self,
        request,
        pk=None,
    ):

        try:

            start = request.GET.get("start")

            end = request.GET.get("end")

            if start is not None:
                start = int(start)

            if end is not None:
                end = int(end)

            # =====================================
            # VALIDATION
            # =====================================

            if (
                start is not None
                and start < 0
            ):
                return Response(
                    {
                        "status": "error",
                        "message":
                            "Start frame must be non-negative",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if (
                end is not None
                and end < 0
            ):
                return Response(
                    {
                        "status": "error",
                        "message":
                            "End frame must be non-negative",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if (
                start is not None
                and end is not None
                and start > end
            ):

                return Response(
                    {
                        "status": "error",
                        "message":
                            "start must be <= end",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if (
                start is not None
                and end is not None
                and end - start > 5000
            ):

                return Response(
                    {
                        "status": "error",
                        "message":
                            "Range cannot exceed 5000 frames",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            project = Project.objects.filter(
                project_id=pk
            ).first()
            if not project:

                return Response(
                    {
                        "status": "error",
                        "message":
                            "Invalid project id",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            recalculate = (
                request.GET.get(
                    "recalculate",
                    "false"
                ).lower() == "true"
            )
            
            # =====================================
            # STATUS CHECK
            # =====================================

            status_only = (
                request.GET.get(
                    "status",
                    "false"
                ).lower() == "true"
            )

            if status_only:

                return Response(
                    {
                        "status": "success",
                        "confusion_status": project.confusion_status,
                    },
                    status=status.HTTP_200_OK,
                )


            # =====================================
            # RECALCULATE
            # =====================================

            if recalculate:

                if project.confusion_status == "PROCESSING":

                    return Response(
                        {
                            "status": "processing",
                            "message":
                                "Confusion calculation is already in progress"
                        },
                        status=status.HTTP_200_OK,
                    )

                project.confusion_status = "PROCESSING"

                project.save(
                    update_fields=["confusion_status"]
                )

                executor.submit(
                    ConfusionStoreService.generate,
                    project_id=pk,
                )

                return Response(
                    {
                        "status": "success",
                        "message":
                            "Confusion recalculation started"
                    },
                    status=status.HTTP_200_OK,
                )

            # =====================================
            # TABLE OPENED WHILE PROCESSING
            # =====================================

            if project.confusion_status == "PROCESSING":

                return Response(
                    {
                        "status": "processing",
                        "message":
                            "Confusion calculation is in progress"
                    },
                    status=status.HTTP_200_OK,
                )
            # =====================================
            # FETCH
            # =====================================

            rows = (
                ConfusionTableService.fetch(
                    project_id=pk,
                    start_frame=start,
                    end_frame=end,
                    query_params=request.GET,
                )
            )

            # =====================================
            # RESPONSE
            # =====================================

            payload = {

                "video_id":pk,
                "start_frame":start,
                "end_frame":end,
                "total_rows":len(rows),

                "rows":
                    FrameConfusionRowSerializer(
                        rows,
                        many=True,
                    ).data,
            }

            return Response(
                {
                    "status": "success",
                    "data": payload,
                },
                status=status.HTTP_200_OK,
            )

        except Exception:

            logger.error(
                "Error fetching confusion table",
                exc_info=True,
            )

            return Response(
                {
                    "status": "error",
                    "message":
                        "Something went wrong while "
                        "fetching confusion table",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        
    # =====================================
    ## for Time line api 
    # =====================================
    @swagger_auto_schema(
        operation_description=(
            "Return compressed timeline data "
            "for selected objects in frame range."
        ),
        manual_parameters=[
            openapi.Parameter(
                "start",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description="Start frame",
            ),
            openapi.Parameter(
                "end",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description="End frame",
            ),
            openapi.Parameter(
                "object_ids",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=False,
                description=(
                    "Comma separated object ids. "
                    "Example: 4,2,11"
                ),
            ),
            openapi.Parameter(
                "debug",
                openapi.IN_QUERY,
                type=openapi.TYPE_BOOLEAN,
                required=False,
                description="Return readable JSON response",
            ),
        ],
        responses={
            200: "Timeline data fetched successfully",
            400: "Validation error",
            500: "Server error",
        },
    )
    @action(
        detail=True,
        methods=["get"],
        url_path="frame-timeline",
    )
    def frame_timeline(
        self,
        request,
        pk=None,
    ):

        try:

            data = request.query_params.copy()

            data["project_id"] = pk

            serializer = (
                FrameTimelineSerializer(
                    data=data
                )
            )

            serializer.is_valid(
                raise_exception=True
            )

            payload = serializer.get_data()

            # =====================================
            # DEBUG MODE
            # =====================================

            debug = request.GET.get(
                "debug",
                "false",
            ).lower() == "true"

            if debug:

                return Response(
                    {
                        "status": "success",
                        "data": payload,
                    },
                    status=status.HTTP_200_OK,
                )

            # =====================================
            # COMPRESSED RESPONSE
            # =====================================

            json_bytes = orjson.dumps(
                payload
            )

            compressed = zlib.compress(
                json_bytes,
                level=6,
            )

            return HttpResponse(
                compressed,
                content_type="application/octet-stream",
            )

        except serializers.ValidationError as ve:

            return Response(
                {
                    "status": "error",
                    "message":
                        "Invalid query parameters",
                    "errors": ve.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception:

            logger.error(
                "Error fetching frame timeline",
                exc_info=True,
            )

            return Response(
                {
                    "status": "error",
                    "message":
                        (
                            "Something went wrong "
                            "while fetching timeline data"
                        ),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # =====================================
    #  Interpolate Trajectory 
    # =====================================
    @swagger_auto_schema(
        method="post",
        request_body=InterpolateTrajectorySerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="interpolate-trajectory",
    )
    def interpolate_trajectory(
        self,
        request,
        pk=None,
    ):

        serializer = (
            InterpolateTrajectorySerializer(
                data=request.data,
                context={
                    "project_id": pk
                },
            )
        )

        serializer.is_valid(
            raise_exception=True
        )

        result = serializer.execute()

        return Response(
            {
                "status": "success",
                "data": result,
            },
            status=status.HTTP_200_OK,
        )
        
    #####################################
    # Activity Log Export API
    #####################################
    @swagger_auto_schema(
        operation_description="Export applied activity logs as a CSV file.",
        manual_parameters=[
            openapi.Parameter(
                "project_id",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=True,
                description="Project ID",
            ),
        ],
        responses={
            200: "CSV file",
            400: "Validation error",
            404: "No activity logs found",
            500: "Server error",
        },
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="activity/logs/export",
    )
    def export_activity_logs(self, request):
        """
        GET /api/v1/videos/activity/logs/export?project_id=<project_id>

        Export currently applied activity logs.
        """

        try:
            serializer = ActivityLogExportSerializer(
                data=request.query_params
            )

            serializer.is_valid(raise_exception=True)

            project_id = serializer.validated_data["project_id"]

            return ActivityLogExportService.export(project_id)

        except serializers.ValidationError as ve:
            return Response(
                {
                    "status": "error",
                    "message": "Invalid query parameters.",
                    "errors": ve.detail,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except ValueError as exc:
            return Response(
                {
                    "status": "error",
                    "message": str(exc),
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        except Exception:
            logger.error(
                "Error exporting activity logs.",
                exc_info=True,
            )

            return Response(
                {
                    "status": "error",
                    "message": (
                        "An unexpected error occurred while "
                        "exporting activity logs."
                    ),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )