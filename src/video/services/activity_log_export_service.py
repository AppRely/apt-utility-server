import csv
import json

from django.http import HttpResponse
from django.utils import timezone

from ..models import ActivityLog


class ActivityLogExportService:
    """
    Service responsible for exporting applied activity logs as a CSV file.
    """

    @staticmethod
    def export(project_id):
        """
        Export applied activity logs for the given project.

        Args:
            project_id (int): Project ID.

        Returns:
            HttpResponse: CSV file response.

        Raises:
            ValueError: If no applied activity logs are found.
        """

        logs = (
            ActivityLog.objects
            .select_related("project_id")
            .filter(
                project_id_id=project_id,
                is_applied=True,
            )
            .order_by("-activity_updated_at")
        )

        if not logs.exists():
            raise ValueError(
                "No applied activity logs found for this project."
            )

        timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")

        filename = f"audit_trail_{project_id}_{timestamp}.csv"

        response = HttpResponse(content_type="text/csv")

        response["Content-Disposition"] = (
            f'attachment; filename="{filename}"'
        )

        writer = csv.writer(response)

        writer.writerow(
            [
                "Activity ID",
                "Project ID",
                "Project Name",
                "Video Name",
                "Operation",
                "Created At",
                "Updated At",
                "Objects Data",
            ]
        )

        for log in logs:
            writer.writerow(
                [
                    log.activity_id,
                    log.project_id_id,
                    log.project_id.project_name,
                    log.project_id.video_name,
                    log.operation,
                    timezone.localtime(
                        log.activity_created_at
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                    timezone.localtime(
                        log.activity_updated_at
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                    json.dumps(log.objects_data),
                ]
            )

        return response