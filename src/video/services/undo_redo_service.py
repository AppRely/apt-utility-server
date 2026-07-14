from django.db import transaction

from ..models import (
    ActivityLog,
    FrameObject,
    ObjectTrack,
    OperationSnapshot,
)
import logging

logger = logging.getLogger(__name__)


class UndoRedoService:
    """
    Operation-aware Undo / Redo engine.

    Key rule:
    - Undo/Redo is applied PER OPERATION
    - NOT blind snapshot replay
    """

    MODEL_MAP = {
        "FrameObject": FrameObject,
        "ObjectTrack": ObjectTrack,
    }

    # ------------------------------------------------
    # Generic snapshot applier (LOW LEVEL)
    # ------------------------------------------------
    @staticmethod
    def _get_pk_field(row: dict) -> str:
        for key in row:
            if key == "id" or key.endswith("_id"):
                return key
        raise KeyError("Primary key not found in snapshot row")

    @staticmethod
    def _apply_snapshot(state: dict) -> None:
        if not state:
            return

        for model_name, ops in state.items():
            Model = UndoRedoService.MODEL_MAP.get(model_name)
            if not Model:
                continue

            # DELETE
            if ops.get("deleted"):
                pk = UndoRedoService._get_pk_field(ops["deleted"][0])
                Model.objects.filter(**{f"{pk}__in": [r[pk] for r in ops["deleted"]]}).delete()

            # CREATE
            if ops.get("created"):
                Model.objects.bulk_create(
                    [Model(**r) for r in ops["created"]],
                    ignore_conflicts=True,
                )

            # UPDATE
            if ops.get("updated"):
                pk = UndoRedoService._get_pk_field(ops["updated"][0])
                for row in ops["updated"]:
                    pk_val = row[pk]
                    update_data = {k: v for k, v in row.items() if k != pk}
                    if update_data:
                        Model.objects.filter(**{pk: pk_val}).update(**update_data)

    # ------------------------------------------------
    # OPERATION-SPECIFIC UNDO / REDO
    # ------------------------------------------------
    @staticmethod
    def _undo_delete(snapshot):
        """
        Undo delete operation:
        - Restore FrameObject entries (recreate them)
        - Restore ObjectTrack status
        """
        before_state = snapshot.before_state

        # For delete operations, before_state contains the objects that were deleted
        # We need to RESTORE them, not delete them
        for model_name, ops in before_state.items():
            Model = UndoRedoService.MODEL_MAP.get(model_name)
            if not Model:
                continue

            # The "deleted" section contains what was removed during delete
            # We need to recreate these entries
            if ops.get("deleted"):
                if model_name == "FrameObject":
                    # Restore FrameObject entries by setting is_active=True
                    # The entries still exist in DB (soft delete), we just need to reactivate them
                    pk = UndoRedoService._get_pk_field(ops["deleted"][0])
                    pk_values = [row[pk] for row in ops["deleted"]]

                    Model.objects.filter(**{f"{pk}__in": pk_values}).update(is_active=True)
                elif model_name == "ObjectTrack":
                    # Restore ObjectTrack status
                    pk = UndoRedoService._get_pk_field(ops["deleted"][0])
                    for row in ops["deleted"]:
                        pk_val = row[pk]
                        update_data = {k: v for k, v in row.items() if k != pk}
                        if update_data:
                            Model.objects.filter(**{pk: pk_val}).update(**update_data)

    @staticmethod
    def _redo_delete(snapshot):
        """
        Redo delete operation:
        - Soft-delete FrameObject entries (set is_active=False)
        - Deactivate ObjectTrack status

        Note: after_state has objects in 'created' section because:
        - before_qs_map is empty
        - after_qs_map has soft-deleted objects
        - SnapshotBuilder puts them in 'created' (not in before_ids)
        """
        after_state = snapshot.after_state

        # For delete operations, after_state contains the soft-deleted state
        # Objects are in the 'created' section (not 'updated')
        for model_name, ops in after_state.items():
            Model = UndoRedoService.MODEL_MAP.get(model_name)
            if not Model:
                continue

            # The 'created' section contains the soft-deleted objects
            # We need to extract IDs and apply is_active=False
            if model_name == "FrameObject":
                # Get objects from 'created' section
                created_objects = ops.get("created", [])

                if created_objects:
                    # Soft-delete FrameObject entries by setting is_active=False
                    pk = UndoRedoService._get_pk_field(created_objects[0])
                    pk_values = [row[pk] for row in created_objects]

                    Model.objects.filter(**{f"{pk}__in": pk_values}).update(is_active=False)

            elif model_name == "ObjectTrack":
                # Deactivate ObjectTrack status using data from 'created' section
                created_objects = ops.get("created", [])

                if created_objects:
                    pk = UndoRedoService._get_pk_field(created_objects[0])
                    for row in created_objects:
                        pk_val = row[pk]
                        update_data = {k: v for k, v in row.items() if k != pk}
                        if update_data:
                            Model.objects.filter(**{pk: pk_val}).update(**update_data)

    @staticmethod
    def _undo_break(snapshot):
        """
        Undo break operation:
        1. Move frames back to original object_id
        2. Restore original ObjectTrack range
        3. Delete the newly created ObjectTrack
        """
        before_state = snapshot.before_state
        after_state = snapshot.after_state
        # --------------------------------------
        # Restore FrameObjects
        # ---------------------------------------
        frame_rows = before_state.get("FrameObject", {}).get("deleted", [])
        for row in frame_rows:
            pk = row["id"]
            FrameObject.objects.filter(id=pk).update(**{k: v for k, v in row.items() if k != "id"})

        # ---------------------------------------
        # Restore original ObjectTrack(s)
        # ---------------------------------------
        before_tracks = before_state.get(
            "ObjectTrack",
            {},
        ).get(
            "deleted",
            [],
        )

        before_ids = set()

        for row in before_tracks:

            before_ids.add(row["track_id"])

            ObjectTrack.objects.update_or_create(
                track_id=row["track_id"],
                defaults={
                    k: v
                    for k, v in row.items()
                    if k != "track_id"
                },
            )

        # ---------------------------------------
        # Delete newly created track(s)
        # ---------------------------------------
        after_tracks = after_state.get(
            "ObjectTrack",
            {},
        ).get(
            "created",
            [],
        )

        for row in after_tracks:

            if row["track_id"] not in before_ids:

                ObjectTrack.objects.filter(
                    track_id=row["track_id"]
                ).delete()

    @staticmethod
    def _redo_break(snapshot):
        """
        Redo break operation:
        1. Move frames to the new object_id
        2. Update old ObjectTrack range
        3. Recreate the new ObjectTrack
        """
        after_state = snapshot.after_state

        # ---------------------------------------
        # Restore FrameObjects
        # ---------------------------------------
        frame_rows = after_state.get(
            "FrameObject",
            {},
        ).get(
            "created",
            [],
        )

        for row in frame_rows:

            FrameObject.objects.filter(
                id=row["id"]
            ).update(
                **{
                    k: v
                    for k, v in row.items()
                    if k != "id"
                }
            )

        # ---------------------------------------
        # Restore ObjectTracks
        # ---------------------------------------
        track_rows = after_state.get(
            "ObjectTrack",
            {},
        ).get(
            "created",
            [],
        )

        for row in track_rows:

            ObjectTrack.objects.update_or_create(
                track_id=row["track_id"],
                defaults={
                    k: v
                    for k, v in row.items()
                    if k != "track_id"
                },
            )
    @staticmethod
    def _undo_link(snapshot):
        """
        Undo link operation:
        1. Reassign frames back to object_2
        2. Restore original ranges and status for both objects
        """
        before_state = snapshot.before_state

        # 1. Restore FrameObjects (Move back to original ID)
        fo_ops = before_state.get("FrameObject", {})
        if fo_ops.get("deleted"):
            Model = UndoRedoService.MODEL_MAP["FrameObject"]
            pk = UndoRedoService._get_pk_field(fo_ops["deleted"][0])

            # Group by object_id to perform bulk updates
            # In a link operation, all frames in before_state['deleted']
            # belong to the same original object (object_2)
            pk_values = [row[pk] for row in fo_ops["deleted"]]
            orig_obj_id = fo_ops["deleted"][0]["object_id"]

            Model.objects.filter(**{f"{pk}__in": pk_values}).update(object_id=orig_obj_id)

        # 2. Restore original ObjectTrack ranges and status
        ot_ops = before_state.get("ObjectTrack", {})
        if ot_ops.get("deleted"):
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(ot_ops["deleted"][0])
            for row in ot_ops["deleted"]:
                update_data = {k: v for k, v in row.items() if k != pk}
                Model.objects.filter(**{pk: row[pk]}).update(**update_data)

    @staticmethod
    def _redo_link(snapshot):
        """
        Redo link operation:
        1. Reassign frames to object_1
        2. Extend object_1 range and deactivate object_2
        """
        after_state = snapshot.after_state

        # 1. Reassign FrameObjects to object_1
        fo_ops = after_state.get("FrameObject", {})
        if fo_ops.get("created"):
            Model = UndoRedoService.MODEL_MAP["FrameObject"]
            pk = UndoRedoService._get_pk_field(fo_ops["created"][0])

            # Group by object_id to perform bulk updates
            # In a link redo, all frames in after_state['created']
            # belong to the merged object (object_1)
            pk_values = [row[pk] for row in fo_ops["created"]]
            merged_obj_id = fo_ops["created"][0]["object_id"]

            Model.objects.filter(**{f"{pk}__in": pk_values}).update(object_id=merged_obj_id)

        # 2. Update ObjectTrack ranges and status
        ot_ops = after_state.get("ObjectTrack", {})
        if ot_ops.get("created"):
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(ot_ops["created"][0])
            for row in ot_ops["created"]:
                update_data = {k: v for k, v in row.items() if k != pk}
                Model.objects.filter(**{pk: row[pk]}).update(**update_data)

    @staticmethod
    def _undo_swap(snapshot):
        """
        Undo swap operation:
        1. Restore FrameObject IDs to original values
        2. Restore original ObjectTrack notes
        """
        before_state = snapshot.before_state

        # 1. Restore FrameObjects (Move back to original IDs)
        fo_ops = before_state.get("FrameObject", {})
        if fo_ops.get("deleted"):
            Model = UndoRedoService.MODEL_MAP["FrameObject"]
            pk = UndoRedoService._get_pk_field(fo_ops["deleted"][0])

            # Group by object_id for bulk updates
            updates = {}
            for row in fo_ops["deleted"]:
                updates.setdefault(row["object_id"], []).append(row[pk])

            for obj_id, pks in updates.items():
                Model.objects.filter(**{f"{pk}__in": pks}).update(object_id=obj_id)

        # 2. Restore original ObjectTrack notes
        ot_ops = before_state.get("ObjectTrack", {})
        if ot_ops.get("deleted"):
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(ot_ops["deleted"][0])
            for row in ot_ops["deleted"]:
                update_data = {k: v for k, v in row.items() if k != pk}
                Model.objects.filter(**{pk: row[pk]}).update(**update_data)

    @staticmethod
    def _redo_swap(snapshot):
        """
        Redo swap operation:
        1. Re-apply swapped FrameObject IDs
        2. Re-apply swap notes in ObjectTrack
        """
        after_state = snapshot.after_state

        # 1. Reassign FrameObjects to swapped IDs
        fo_ops = after_state.get("FrameObject", {})
        if fo_ops.get("created"):
            Model = UndoRedoService.MODEL_MAP["FrameObject"]
            pk = UndoRedoService._get_pk_field(fo_ops["created"][0])

            # Group by object_id for bulk updates
            updates = {}
            for row in fo_ops["created"]:
                updates.setdefault(row["object_id"], []).append(row[pk])

            for obj_id, pks in updates.items():
                Model.objects.filter(**{f"{pk}__in": pks}).update(object_id=obj_id)

        # 2. Update ObjectTrack notes
        ot_ops = after_state.get("ObjectTrack", {})
        if ot_ops.get("created"):
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(ot_ops["created"][0])
            for row in ot_ops["created"]:
                update_data = {k: v for k, v in row.items() if k != pk}
                Model.objects.filter(**{pk: row[pk]}).update(**update_data)

    # ------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------
    @staticmethod
    def undo(project_id: int) -> dict:
        activity = (
            ActivityLog.objects.filter(project_id_id=project_id, is_applied=True).order_by("-activity_id").first()
        )

        if not activity:
            raise ValueError("Nothing to undo")

        snapshot = OperationSnapshot.objects.filter(activity=activity).first()

        if not snapshot:
            raise ValueError("Snapshot missing")

        with transaction.atomic():
            op = activity.operation

            if op == "delete":
                UndoRedoService._undo_delete(snapshot)
            # Keep break_object for activity rows recorded before break modes
            # were split into break_before and break_after.
            elif op in ("break_object", "break_before", "break_after"):
                UndoRedoService._undo_break(snapshot)
            elif op == "link":
                UndoRedoService._undo_link(snapshot)
            elif op == "swap":
                UndoRedoService._undo_swap(snapshot)
            elif activity.operation == "interpolate":
                UndoRedoService._undo_interpolate(snapshot)
            else:
                UndoRedoService._apply_snapshot(snapshot.before_state)

            activity.is_applied = False
            activity.save(update_fields=["is_applied"])

        return {
            "status": "success",
            "mode": "undo",
            "activity_id": activity.activity_id,
            "operation": activity.operation,
        }

    @staticmethod
    def redo(project_id: int) -> dict:
        activity = (
            ActivityLog.objects.filter(project_id_id=project_id, is_applied=False).order_by("activity_id").first()
        )

        if not activity:
            raise ValueError("Nothing to redo")

        snapshot = OperationSnapshot.objects.filter(activity=activity).first()

        if not snapshot:
            raise ValueError("Snapshot missing")

        with transaction.atomic():
            op = activity.operation

            if op == "delete":
                UndoRedoService._redo_delete(snapshot)
            # Keep break_object for activity rows recorded before break modes
            # were split into break_before and break_after.
            elif op in ("break_object", "break_before", "break_after"):
                UndoRedoService._redo_break(snapshot)
            elif op == "link":
                UndoRedoService._redo_link(snapshot)
            elif op == "swap":
                UndoRedoService._redo_swap(snapshot)
            elif activity.operation == "interpolate":
                UndoRedoService._redo_interpolate(snapshot)
            else:
                UndoRedoService._apply_snapshot(snapshot.after_state)

            activity.is_applied = True
            activity.save(update_fields=["is_applied"])

        return {
            "status": "success",
            "mode": "redo",
            "activity_id": activity.activity_id,
            "operation": activity.operation,
        }
        
    # ------------------------------------------------
    # interpolate
    # ------------------------------------------------
    @staticmethod
    def _undo_interpolate(snapshot):

        after_state = snapshot.after_state

        frame_ops = after_state.get(
            "FrameObject",
            {},
        )

        created_rows = frame_ops.get(
            "created",
            [],
        )

        if created_rows:

            pk = UndoRedoService._get_pk_field(
                created_rows[0]
            )

            pk_values = [
                row[pk]
                for row in created_rows
            ]

            FrameObject.objects.filter(
                **{
                    f"{pk}__in": pk_values
                }
            ).delete()

        track_ops = snapshot.before_state.get(
            "ObjectTrack",
            {},
        )

        rows = track_ops.get(
            "updated",
            [],
        )

        if rows:

            row = rows[0]

            ObjectTrack.objects.filter(
                track_id=row["track_id"]
            ).update(
                start_frame=row["start_frame"],
                end_frame=row["end_frame"],
                object_status=row["object_status"],
                operation_note=row["operation_note"],
            )


    @staticmethod
    def _redo_interpolate(snapshot):

        after_state = snapshot.after_state

        frame_ops = after_state.get(
            "FrameObject",
            {},
        )

        created_rows = frame_ops.get(
            "created",
            [],
        )

        if created_rows:

            pk = UndoRedoService._get_pk_field(
                created_rows[0]
            )

            for row in created_rows:

                pk_val = row[pk]

                data = {
                    k: v
                    for k, v in row.items()
                    if k != pk
                }

                FrameObject.objects.update_or_create(
                    **{pk: pk_val},
                    defaults=data,
                )

        track_ops = after_state.get(
            "ObjectTrack",
            {},
        )

        rows = track_ops.get(
            "updated",
            [],
        )

        if rows:

            row = rows[0]

            ObjectTrack.objects.filter(
                track_id=row["track_id"]
            ).update(
                start_frame=row["start_frame"],
                end_frame=row["end_frame"],
                object_status=row["object_status"],
                operation_note=row["operation_note"],
            )