# from django.db import transaction

# from ..models import (
#     ActivityLog,
#     OperationSnapshot,
#     FrameObject,
#     ObjectTrack,
# )


# class UndoRedoService:
#     """
#     Centralized Undo / Redo engine.

#     Design principles:
#     - Snapshot-based (DB is the source of truth)
#     - Immutable snapshots
#     - Transaction-safe
#     - Model-agnostic (via MODEL_MAP)
#     - Low coupling with serializers/views
#     """

#     # ----------------------------------
#     # Register models allowed for undo/redo
#     # ----------------------------------
#     MODEL_MAP = {
#         "FrameObject": FrameObject,
#         "ObjectTrack": ObjectTrack,
#     }

#     # ----------------------------------
#     # Utilities
#     # ----------------------------------
#     @staticmethod
#     def _get_pk_field(row: dict) -> str:
#         """
#         Dynamically find primary key field from snapshot row.
#         Works for:
#         - id
#         - track_id
#         - *_id
#         """
#         for key in row.keys():
#             if key == "id" or key.endswith("_id"):
#                 return key
#         raise KeyError("Primary key field not found in snapshot row")

#     @staticmethod
#     def _apply_snapshot(state: dict) -> None:
#         """
#         Apply snapshot state safely.
#         Used internally by undo() and redo().

#         Snapshot format:
#         {
#             "FrameObject": {
#                 "created": [ {...}, {...} ],
#                 "updated": [ {...}, {...} ],
#                 "deleted": [ {...}, {...} ]
#             },
#             "ObjectTrack": { ... }
#         }
#         """
#         if not state:
#             return

#         for model_name, ops in state.items():
#             Model = UndoRedoService.MODEL_MAP.get(model_name)
#             if not Model:
#                 # Unknown model → safely ignore
#                 continue

#             # ------------------------
#             # DELETE
#             # ------------------------
#             if ops.get("deleted"):
#                 pk_field = UndoRedoService._get_pk_field(ops["deleted"][0])
#                 pk_values = [row[pk_field] for row in ops["deleted"]]

#                 Model.objects.filter(
#                     **{f"{pk_field}__in": pk_values}
#                 ).delete()

#             # ------------------------
#             # CREATE
#             # ------------------------
#             if ops.get("created"):
#                 Model.objects.bulk_create(
#                     [Model(**row) for row in ops["created"]],
#                     ignore_conflicts=True,
#                 )

#             # ------------------------
#             # UPDATE
#             # ------------------------
#             if ops.get("updated"):
#                 pk_field = UndoRedoService._get_pk_field(ops["updated"][0])

#                 for row in ops["updated"]:
#                     pk_value = row[pk_field]

#                     # IMPORTANT:
#                     # Never mutate snapshot rows
#                     update_data = {
#                         k: v for k, v in row.items()
#                         if k != pk_field
#                     }

#                     if update_data:
#                         Model.objects.filter(
#                             **{pk_field: pk_value}
#                         ).update(**update_data)

#     # ----------------------------------
#     # Public API
#     # ----------------------------------
#     @staticmethod
#     def undo(project_id: int) -> dict:
#         """
#         Undo the most recent applied activity for a project.
#         """
#         activity = (
#             ActivityLog.objects
#             .filter(project_id_id=project_id, is_applied=True)
#             .order_by("-activity_id")
#             .first()
#         )

#         if not activity:
#             raise ValueError("Nothing to undo")

#         snapshot = (
#             OperationSnapshot.objects
#             .filter(activity=activity)
#             .order_by("id")
#             .first()
#         )

#         if not snapshot:
#             raise ValueError("Snapshot missing for this activity")

#         with transaction.atomic():
#             UndoRedoService._apply_snapshot(snapshot.before_state)
#             activity.is_applied = False
#             activity.save(update_fields=["is_applied"])

#         return {
#             "status": "success",
#             "mode": "undo",
#             "activity_id": activity.activity_id,
#             "operation": activity.operation,
#         }

#     @staticmethod
#     def redo(project_id: int) -> dict:
#         """
#         Redo the earliest unapplied activity for a project.
#         """
#         activity = (
#             ActivityLog.objects
#             .filter(project_id_id=project_id, is_applied=False)
#             .order_by("activity_id")
#             .first()
#         )

#         if not activity:
#             raise ValueError("Nothing to redo")

#         snapshot = (
#             OperationSnapshot.objects
#             .filter(activity=activity)
#             .order_by("id")
#             .first()
#         )

#         if not snapshot:
#             raise ValueError("Snapshot missing for this activity")

#         with transaction.atomic():
#             UndoRedoService._apply_snapshot(snapshot.after_state)
#             activity.is_applied = True
#             activity.save(update_fields=["is_applied"])

#         return {
#             "status": "success",
#             "mode": "redo",
#             "activity_id": activity.activity_id,
#             "operation": activity.operation,
#         }
from django.db import transaction

from ..models import (
    ActivityLog,
    OperationSnapshot,
    FrameObject,
    ObjectTrack,
)


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
        for key in row.keys():
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
                Model.objects.filter(
                    **{f"{pk}__in": [r[pk] for r in ops["deleted"]]}
                ).delete()

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
                    
                    Model.objects.filter(
                        **{f"{pk}__in": pk_values}
                    ).update(is_active=True)
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
                    
                    Model.objects.filter(
                        **{f"{pk}__in": pk_values}
                    ).update(is_active=False)
                    
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

        # 1. Restore FrameObjects (Move back to original ID)
        # In 'break', before_state['FrameObject']['deleted'] contains the original state
        fo_ops = before_state.get("FrameObject", {})
        if fo_ops.get("deleted"):
            Model = UndoRedoService.MODEL_MAP["FrameObject"]
            pk = UndoRedoService._get_pk_field(fo_ops["deleted"][0])
            for row in fo_ops["deleted"]:
                # Update existing frames back to original object_id
                Model.objects.filter(**{pk: row[pk]}).update(object_id=row["object_id"])

        # 2. Restore original ObjectTrack range
        ot_ops_before = before_state.get("ObjectTrack", {})
        if ot_ops_before.get("deleted"):
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(ot_ops_before["deleted"][0])
            for row in ot_ops_before["deleted"]:
                update_data = {k: v for k, v in row.items() if k != pk}
                Model.objects.filter(**{pk: row[pk]}).update(**update_data)

        # 3. Delete the newly created ObjectTrack
        # The new track is in after_state['ObjectTrack']['created'] 
        # but NOT in before_state
        ot_ops_after = after_state.get("ObjectTrack", {})
        if ot_ops_after.get("created"):
            before_ids = {r["track_id"] for r in ot_ops_before.get("deleted", [])}
            new_tracks = [
                r["track_id"] for r in ot_ops_after["created"] 
                if r["track_id"] not in before_ids
            ]
            if new_tracks:
                ObjectTrack.objects.filter(track_id__in=new_tracks).delete()

    @staticmethod
    def _redo_break(snapshot):
        """
        Redo break operation:
        1. Move frames to the new object_id
        2. Update old ObjectTrack range
        3. Recreate the new ObjectTrack
        """
        after_state = snapshot.after_state

        # 1. Update FrameObjects to new ID
        fo_ops = after_state.get("FrameObject", {})
        if fo_ops.get("created"):
            Model = UndoRedoService.MODEL_MAP["FrameObject"]
            pk = UndoRedoService._get_pk_field(fo_ops["created"][0])
            for row in fo_ops["created"]:
                Model.objects.filter(**{pk: row[pk]}).update(object_id=row["object_id"])

        # 2. Update/Create ObjectTracks
        ot_ops = after_state.get("ObjectTrack", {})
        if ot_ops.get("created"):
            Model = UndoRedoService.MODEL_MAP["ObjectTrack"]
            pk = UndoRedoService._get_pk_field(ot_ops["created"][0])
            for row in ot_ops["created"]:
                # Use update_or_create or bulk_create with ignore_conflicts
                # Since we want to ensure the new one is created and old one is updated
                obj_data = {k: v for k, v in row.items() if k != pk}
                Model.objects.update_or_create(**{pk: row[pk]}, defaults=obj_data)

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
            ActivityLog.objects
            .filter(project_id_id=project_id, is_applied=True)
            .order_by("-activity_id")
            .first()
        )

        if not activity:
            raise ValueError("Nothing to undo")

        snapshot = (
            OperationSnapshot.objects
            .filter(activity=activity)
            .first()
        )

        if not snapshot:
            raise ValueError("Snapshot missing")

        with transaction.atomic():
            op = activity.operation

            if op == "delete":
                UndoRedoService._undo_delete(snapshot)
            elif op == "break_object":
                UndoRedoService._undo_break(snapshot)
            elif op == "link":
                UndoRedoService._undo_link(snapshot)
            elif op == "swap":
                UndoRedoService._undo_swap(snapshot)
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
            ActivityLog.objects
            .filter(project_id_id=project_id, is_applied=False)
            .order_by("activity_id")
            .first()
        )

        if not activity:
            raise ValueError("Nothing to redo")

        snapshot = (
            OperationSnapshot.objects
            .filter(activity=activity)
            .first()
        )

        if not snapshot:
            raise ValueError("Snapshot missing")

        with transaction.atomic():
            op = activity.operation

            if op == "delete":
                UndoRedoService._redo_delete(snapshot)
            elif op == "break_object":
                UndoRedoService._redo_break(snapshot)
            elif op == "link":
                UndoRedoService._redo_link(snapshot)
            elif op == "swap":
                UndoRedoService._redo_swap(snapshot)
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
