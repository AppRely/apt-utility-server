from django.db import transaction
from ..models import ActivityLog, OperationSnapshot
# class SnapshotLogger:
#     """
#     WRITE-ONLY snapshot logger.
#     """

#     @staticmethod
#     def log(project_id, operation, before_state, after_state, objects_data=None):
#         activity = ActivityLog.objects.create(
#             project_id_id=project_id,
#             operation=operation,
#             objects_data=objects_data or {},
#             is_applied=True,
#         )

#         OperationSnapshot.objects.create(
#             activity=activity,
#             before_state=before_state,
#             after_state=after_state,
#         )

#         return activity

import json

class SnapshotLogger:
    @staticmethod
    def log(project_id, operation, before_state, after_state, objects_data=None):
        objects_data = json.loads(json.dumps(objects_data or {}))
        before_state = json.loads(json.dumps(before_state))
        after_state  = json.loads(json.dumps(after_state))

        with transaction.atomic():
            # 1. Clear Redo Stack (any unapplied activities for this project)
            ActivityLog.objects.filter(
                project_id_id=project_id, 
                is_applied=False
            ).delete()

            # 2. Create new activity
            activity = ActivityLog.objects.create(
                project_id_id=project_id,
                operation=operation,
                objects_data=objects_data,
                is_applied=True,
            )

            OperationSnapshot.objects.create(
                activity=activity,
                before_state=before_state,
                after_state=after_state,
            )

            # # 3. Limit Undo Stack to 5 levels
            # # Get all applied activities for this project, ordered by ID descending
            # applied_activities = ActivityLog.objects.filter(
            #     project_id_id=project_id,
            #     is_applied=True
            # ).order_by("-activity_id")

            # if applied_activities.count() > 5:
            #     # Keep the 5 most recent, delete the rest
            #     ids_to_keep = applied_activities.values_list("activity_id", flat=True)[:5]
            #     ActivityLog.objects.filter(
            #         project_id_id=project_id,
            #         is_applied=True
            #     ).exclude(activity_id__in=ids_to_keep).delete()

        return activity
