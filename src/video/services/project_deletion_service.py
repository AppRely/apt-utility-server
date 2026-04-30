import logging
import os
import shutil

from django.conf import settings

from ..models import Project

logger = logging.getLogger(__name__)


class ProjectDeletionService:
    """
    Service to handle the complete deletion of a project.
    This includes:
    1. Deleting database records (via CASCADE).
    2. Deleting video files from disk.
    3. Deleting TRK files from disk.
    4. Deleting exported TRK versions from disk.
    """

    @classmethod
    def delete_project(cls, project_id: int):
        try:
            project = Project.objects.get(project_id=project_id)
        except Project.DoesNotExist:
            logger.error(f"[PROJECT-DELETE] Project {project_id} not found.")
            return False, "Project not found"

        # 1. Identify paths
        video_name = project.video_name
        trk_name = project.trk_file_name

        video_path = os.path.join(settings.MEDIA_ROOT, "video_folder", video_name) if video_name else None
        trk_path = os.path.join(settings.MEDIA_ROOT, "track_folder", trk_name) if trk_name else None
        export_dir = os.path.join(settings.MEDIA_ROOT, "trk_exports", str(project_id))

        # 2. Delete files from disk
        cls._delete_file(video_path, "Video")
        cls._delete_file(trk_path, "TRK")
        cls._delete_directory(export_dir, "Export")

        # 3. Delete from database (Triggers CASCADE)
        project.delete()
        logger.info(f"[PROJECT-DELETE] Project {project_id} and all related data deleted successfully.")

        return True, "Project deleted successfully"

    @staticmethod
    def _delete_file(path, label):
        if path and os.path.exists(path):
            try:
                os.remove(path)
                logger.info(f"[PROJECT-DELETE] {label} file deleted: {path}")
            except Exception as e:
                logger.error(f"[PROJECT-DELETE] Failed to delete {label} file {path}: {str(e)}")

    @staticmethod
    def _delete_directory(path, label):
        if path and os.path.exists(path):
            try:
                shutil.rmtree(path)
                logger.info(f"[PROJECT-DELETE] {label} directory deleted: {path}")
            except Exception as e:
                logger.error(f"[PROJECT-DELETE] Failed to delete {label} directory {path}: {str(e)}")
