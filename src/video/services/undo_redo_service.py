from django.db import transaction

from ..models import (
    ActivityLog,
    FrameObject,
    ObjectTrack,
    OperationSnapshot,
)
from .snapshot_builder import SnapshotBuilder
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

    @staticmethod
    def _chunks(values):
        size = SnapshotBuilder.batch_size()
        for offset in range(0, len(values), size):
            yield values[offset:offset + size]

    @staticmethod
    def _get_snapshot_rows(state: dict, model_name: str) -> list:
        if not state:
            return []
        ops = state.get(model_name, {})
        return ops.get("deleted", []) + ops.get("created", []) + ops.get("updated", [])

    @staticmethod
    def _field_name(Model, key):
        """Translate a values() FK attname (for example frame_id) to its field."""
        for field in Model._meta.concrete_fields:
            if key in (field.name, field.attname):
                return field.name
        return key

    @classmethod
    def _bulk_update_rows(cls, Model, pk, rows):
        """Update heterogeneous snapshot rows without issuing one query per row."""
        by_field_set = {}
        for row in rows:
            keys = tuple(key for key in row if key != pk)
            if keys:
                by_field_set.setdefault(keys, []).append(row)

        for keys, group in by_field_set.items():
            fields = [cls._field_name(Model, key) for key in keys]
            for batch in cls._chunks(group):
                Model.objects.bulk_update(
                    [Model(**row) for row in batch],
                    fields,
                    batch_size=SnapshotBuilder.batch_size(),
                )

    @classmethod
    def _bulk_upsert_rows(cls, Model, pk, rows):
        """Batch equivalent of update_or_create for snapshot replay."""
        if not rows:
            return
        ids = [row[pk] for row in rows]
        existing_ids = set()
        for batch in cls._chunks(ids):
            existing_ids.update(Model.objects.filter(**{f"{pk}__in": batch}).values_list(pk, flat=True))
        existing = [row for row in rows if row[pk] in existing_ids]
        missing = [row for row in rows if row[pk] not in existing_ids]
        cls._bulk_update_rows(Model, pk, existing)
        for batch in cls._chunks(missing):
            Model.objects.bulk_create(
                [Model(**row) for row in batch],
                batch_size=SnapshotBuilder.batch_size(),
            )

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
                for batch in UndoRedoService._chunks([r[pk] for r in ops["deleted"]]):
                    Model.objects.filter(**{f"{pk}__in": batch}).delete()

            # CREATE
            if ops.get("created"):
                for batch in UndoRedoService._chunks(ops["created"]):
                    Model.objects.bulk_create(
                        [Model(**r) for r in batch],
                        ignore_conflicts=True,
                        batch_size=SnapshotBuilder.batch_size(),
                    )

            # UPDATE
            if ops.get("updated"):
                pk = UndoRedoService._get_pk_field(ops["updated"][0])
                UndoRedoService._bulk_update_rows(Model, pk, ops["updated"])

    # ------------------------------------------------
    # OPERATION-SPECIFIC UNDO / REDO
    # ------------------------------------------------
    #####################################
    #Delete
    #####################################
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

                    for batch in UndoRedoService._chunks(pk_values):
                        Model.objects.filter(**{f"{pk}__in": batch}).update(is_active=True)
                elif model_name == "ObjectTrack":
                    # Restore ObjectTrack status
                    pk = UndoRedoService._get_pk_field(ops["deleted"][0])
                    UndoRedoService._bulk_update_rows(Model, pk, ops["deleted"])

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

                    for batch in UndoRedoService._chunks(pk_values):
                        Model.objects.filter(**{f"{pk}__in": batch}).update(is_active=False)

            elif model_name == "ObjectTrack":
                # Deactivate ObjectTrack status using data from 'created' section
                created_objects = ops.get("created", [])

                if created_objects:
                    pk = UndoRedoService._get_pk_field(created_objects[0])
                    UndoRedoService._bulk_update_rows(Model, pk, created_objects)

    
    ##################################
    # Break
    ##################################
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
        if frame_rows:
            UndoRedoService._bulk_update_rows(FrameObject, "id", frame_rows)

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
        UndoRedoService._bulk_upsert_rows(ObjectTrack, "track_id", before_tracks)

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

        created_ids = [row["track_id"] for row in after_tracks if row["track_id"] not in before_ids]
        for batch in UndoRedoService._chunks(created_ids):
            ObjectTrack.objects.filter(track_id__in=batch).delete()

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

        if frame_rows:
            UndoRedoService._bulk_update_rows(FrameObject, "id", frame_rows)

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

        UndoRedoService._bulk_upsert_rows(ObjectTrack, "track_id", track_rows)

    ##################################
    # Clip
    ##################################
    @staticmethod
    def _undo_clip(snapshot):
        """Move clipped frames back and remove only the newly created track."""
        frame_rows = snapshot.before_state.get("FrameObject", {}).get("deleted", [])
        if frame_rows:
            UndoRedoService._bulk_update_rows(FrameObject, "id", frame_rows)

        new_tracks = snapshot.after_state.get("ObjectTrack", {}).get("created", [])
        track_ids = [row["track_id"] for row in new_tracks]
        for batch in UndoRedoService._chunks(track_ids):
            ObjectTrack.objects.filter(track_id__in=batch).delete()

    @staticmethod
    def _redo_clip(snapshot):
        """Reapply clipped frame IDs and recreate the clipped track."""
        frame_rows = snapshot.after_state.get("FrameObject", {}).get("created", [])
        if frame_rows:
            UndoRedoService._bulk_update_rows(FrameObject, "id", frame_rows)

        new_tracks = snapshot.after_state.get("ObjectTrack", {}).get("created", [])
        UndoRedoService._bulk_upsert_rows(ObjectTrack, "track_id", new_tracks)

    ##############################################################
    #link
    ##############################################################
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

            for batch in UndoRedoService._chunks(pk_values):
                Model.objects.filter(**{f"{pk}__in": batch}).update(object_id=orig_obj_id)

        # 2. Restore original ObjectTrack ranges and status
        ot_ops = before_state.get("ObjectTrack", {})
        if ot_ops.get("deleted"):
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(ot_ops["deleted"][0])
            UndoRedoService._bulk_update_rows(Model, pk, ot_ops["deleted"])

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

            for batch in UndoRedoService._chunks(pk_values):
                Model.objects.filter(**{f"{pk}__in": batch}).update(object_id=merged_obj_id)

        # 2. Update ObjectTrack ranges and status
        ot_ops = after_state.get("ObjectTrack", {})
        if ot_ops.get("created"):
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(ot_ops["created"][0])
            UndoRedoService._bulk_update_rows(Model, pk, ot_ops["created"])

    #######################################################
    #swap
    #######################################################
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
                for batch in UndoRedoService._chunks(pks):
                    Model.objects.filter(**{f"{pk}__in": batch}).update(object_id=obj_id)

        # 2. Restore original ObjectTrack notes
        ot_ops = before_state.get("ObjectTrack", {})
        if ot_ops.get("deleted"):
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(ot_ops["deleted"][0])
            UndoRedoService._bulk_update_rows(Model, pk, ot_ops["deleted"])

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
                for batch in UndoRedoService._chunks(pks):
                    Model.objects.filter(**{f"{pk}__in": batch}).update(object_id=obj_id)

        # 2. Update ObjectTrack notes
        ot_ops = after_state.get("ObjectTrack", {})
        if ot_ops.get("created"):
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(ot_ops["created"][0])
            UndoRedoService._bulk_update_rows(Model, pk, ot_ops["created"])

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

            if op in ("delete", "BULK_DELETE"):
                UndoRedoService._undo_delete(snapshot)
            # Keep break_object for activity rows recorded before break modes
            # were split into break_before and break_after.
            elif op in ("break_object", "break_before", "break_after"):
                UndoRedoService._undo_break(snapshot)
            elif op == "clip":
                UndoRedoService._undo_clip(snapshot)
            elif op == "link":
                UndoRedoService._undo_link(snapshot)
            elif op == "overlap":
                UndoRedoService._undo_overlap(snapshot)
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

            if op in ("delete", "BULK_DELETE"):
                UndoRedoService._redo_delete(snapshot)
            # Keep break_object for activity rows recorded before break modes
            # were split into break_before and break_after.
            elif op in ("break_object", "break_before", "break_after"):
                UndoRedoService._redo_break(snapshot)
            elif op == "clip":
                UndoRedoService._redo_clip(snapshot)
            elif op == "link":
                UndoRedoService._redo_link(snapshot)
            elif op == "overlap":
                UndoRedoService._redo_overlap(snapshot)
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

            for batch in UndoRedoService._chunks(pk_values):
                FrameObject.objects.filter(**{f"{pk}__in": batch}).delete()

        track_ops = snapshot.before_state.get(
            "ObjectTrack",
            {},
        )

        rows = track_ops.get(
            "updated",
            [],
        )

        if rows:

            UndoRedoService._bulk_update_rows(ObjectTrack, "track_id", rows)


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

            UndoRedoService._bulk_upsert_rows(FrameObject, pk, created_rows)

        track_ops = after_state.get(
            "ObjectTrack",
            {},
        )

        rows = track_ops.get(
            "updated",
            [],
        )

        if rows:

            UndoRedoService._bulk_update_rows(ObjectTrack, "track_id", rows)

    ########################################
    # Overlap
    ########################################

    @staticmethod
    def _undo_overlap(snapshot):
        """
        Undo overlap operation:
        1. Restore deleted FrameObjects (re-create ones that were deleted during merge)
        2. Move FrameObjects back to their original object_id before overlap
        3. Restore original ObjectTrack start_frame, end_frame, object_status, operation_note
        """
        before_fo = UndoRedoService._get_snapshot_rows(snapshot.before_state, "FrameObject")
        before_ot = UndoRedoService._get_snapshot_rows(snapshot.before_state, "ObjectTrack")

        if before_fo:
            Model = UndoRedoService.MODEL_MAP["FrameObject"]
            pk = UndoRedoService._get_pk_field(before_fo[0])

            # Find FrameObject rows that were physically deleted during merge
            all_ids = [row[pk] for row in before_fo]
            existing_ids = set()
            for batch in UndoRedoService._chunks(all_ids):
                existing_ids.update(
                    Model.objects.filter(**{f"{pk}__in": batch}).values_list(pk, flat=True)
                )

            missing_rows = [row for row in before_fo if row[pk] not in existing_ids]

            # Re-create missing FrameObjects
            if missing_rows:
                for batch in UndoRedoService._chunks(missing_rows):
                    Model.objects.bulk_create(
                        [Model(**r) for r in batch],
                        ignore_conflicts=True,
                        batch_size=SnapshotBuilder.batch_size(),
                    )

            # Group by original object_id and bulk update
            updates = {}
            for row in before_fo:
                updates.setdefault(row["object_id"], []).append(row[pk])

            for obj_id, pks in updates.items():
                for batch in UndoRedoService._chunks(pks):
                    Model.objects.filter(**{f"{pk}__in": batch}).update(object_id=obj_id)

        if before_ot:
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(before_ot[0])
            UndoRedoService._bulk_update_rows(Model, pk, before_ot)

    @staticmethod
    def _redo_overlap(snapshot):
        """
        Redo overlap operation:
        1. Delete FrameObjects that were merged/deleted during overlap
        2. Reassign active FrameObjects to their post-overlap object_id
        3. Restore post-overlap ObjectTrack state
        """
        before_fo = UndoRedoService._get_snapshot_rows(snapshot.before_state, "FrameObject")
        after_fo = UndoRedoService._get_snapshot_rows(snapshot.after_state, "FrameObject")
        after_ot = UndoRedoService._get_snapshot_rows(snapshot.after_state, "ObjectTrack")

        if before_fo and after_fo:
            Model = UndoRedoService.MODEL_MAP["FrameObject"]
            pk = UndoRedoService._get_pk_field(before_fo[0])

            before_ids = {row[pk] for row in before_fo}
            after_ids = {row[pk] for row in after_fo}

            # Delete rows that were deleted during overlap merge
            deleted_ids = list(before_ids - after_ids)
            if deleted_ids:
                for batch in UndoRedoService._chunks(deleted_ids):
                    Model.objects.filter(**{f"{pk}__in": batch}).delete()

            # Group by post-overlap object_id and bulk update
            updates = {}
            for row in after_fo:
                updates.setdefault(row["object_id"], []).append(row[pk])

            for obj_id, pks in updates.items():
                for batch in UndoRedoService._chunks(pks):
                    Model.objects.filter(**{f"{pk}__in": batch}).update(object_id=obj_id)

        if after_ot:
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(after_ot[0])
            UndoRedoService._bulk_update_rows(Model, pk, after_ot)
