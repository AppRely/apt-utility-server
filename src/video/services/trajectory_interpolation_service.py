from django.db import connection, transaction

from ..models import (
    FrameObject,
    ObjectTrack,
    VideoFrame,
)

from .snapshot_builder import SnapshotBuilder
from .snapshot_logger import SnapshotLogger
from rest_framework.exceptions import ValidationError


class TrajectoryInterpolationService:

    MAX_GAP = 10

    @staticmethod
    def interpolate_coordinates(
        start_coordinates,
        end_coordinates,
        total_steps,
        current_step,
    ):

        if (
            len(start_coordinates)
            !=
            len(end_coordinates)
        ):
            raise ValidationError(
                {
                    "coordinates":
                    "Coordinate count mismatch"
                }
            )

        interpolated = []

        for start_point, end_point in zip(
            start_coordinates,
            end_coordinates,
        ):

            point = []

            for start_value, end_value in zip(
                start_point,
                end_point,
            ):

                value = (
                    start_value
                    +
                    (
                        (
                            end_value
                            -
                            start_value
                        )
                        *
                        current_step
                        /
                        total_steps
                    )
                )

                point.append(
                    round(value, 6)
                )

            interpolated.append(point)

        return interpolated

    @classmethod
    @transaction.atomic
    def interpolate(
        cls,
        *,
        project_id,
        source_object_id=None,
        source_end_frame=None,
        target_object_id=None,
        target_start_frame=None,
        object_id=None,
        start_frame=None,
        end_frame=None,
        update_track=True,
    ):
        if (
            object_id is not None
            and start_frame is not None
            and end_frame is not None
        ):

            rows = (
                FrameObject.objects
                .filter(
                    frame__project_id_id=project_id,
                    object_id=object_id,
                    is_active=True,
                    frame__frame_no__gte=start_frame,
                    frame__frame_no__lte=end_frame,
                )
                .select_related("frame")
                .order_by("frame__frame_no")
            )
            gaps = []

            previous_row = None

            for row in rows:

                if (
                    previous_row
                    and
                    row.frame.frame_no
                    - previous_row.frame.frame_no
                    > 1
                ):

                    gaps.append(
                        (
                            previous_row.frame.frame_no,
                            row.frame.frame_no,
                        )
                    )

                previous_row = row
            if not gaps:

                return {
                    "interpolation_required": False,
                    "message": "No missing frames found in range",
                    "frames_created": 0,
                }

            results = []

            for source_frame, target_frame in gaps:

                result = cls.interpolate(
                    project_id=project_id,
                    source_object_id=object_id,
                    source_end_frame=source_frame,
                    target_object_id=object_id,
                    target_start_frame=target_frame,
                    update_track=False,
                )

                results.append(result)

            return {
                "object_id": object_id,
                "gaps_found": len(gaps),
                "results": results,
            }
        
        gap = (
            target_start_frame
            -
            source_end_frame
            -
            1
        )

        if gap <= 0:
            raise ValidationError(
                {
                    "gap": "No missing frames found"
                }
            )

        # if gap > cls.MAX_GAP:
        #     raise ValueError(
        #         f"Gap exceeds {cls.MAX_GAP}"
        #     )

        source_row = (
            FrameObject.objects
            .select_related("frame")
            .get(
                frame__project_id_id=project_id,
                frame__frame_no=source_end_frame,
                object_id=source_object_id,
                is_active=True,
            )
        )

        target_row = (
            FrameObject.objects
            .select_related("frame")
            .get(
                frame__project_id_id=project_id,
                frame__frame_no=target_start_frame,
                object_id=target_object_id,
                is_active=True,
            )
        )

        # before_qs = FrameObject.objects.none()

        created_frame_ids = []
        source_track = ObjectTrack.objects.get(
            project_id_id=project_id,
            object_id=source_object_id,
        )
        # print(
        #     "[INTERPOLATE] DB Track BEFORE update:",
        #     {
        #         "track_id": source_track.track_id,
        #         "start_frame": source_track.start_frame,
        #         "end_frame": source_track.end_frame,
        #     }
        # )
        # =====================================
        # FREEZE BEFORE TRACK SNAPSHOT
        # =====================================

        before_track_snapshot = SnapshotBuilder.capture((
            ObjectTrack.objects.filter(track_id=source_track.track_id),
            ("track_id", "end_frame"),
        ))
        total_steps = (
            target_start_frame
            -
            source_end_frame
        )

        frame_numbers = range(source_end_frame + 1, target_start_frame)
        frames_by_number = dict(
            VideoFrame.objects.filter(
                project_id_id=project_id,
                frame_no__in=frame_numbers,
            ).values_list("frame_no", "id")
        )
        existing_frame_ids = set(
            FrameObject.objects.filter(
                frame_id__in=list(frames_by_number.values()),
                object_id=source_object_id,
                is_active=True,
            ).values_list("frame_id", flat=True)
        )
        rows_to_create = []
        for frame_no in frame_numbers:
            frame_id = frames_by_number.get(frame_no)
            if frame_id is None:
                # Preserve the original DoesNotExist behaviour for malformed
                # projects with a missing VideoFrame.
                frame_id = VideoFrame.objects.get(
                    project_id_id=project_id,
                    frame_no=frame_no,
                ).id
            if frame_id in existing_frame_ids:
                continue

            step = (
                frame_no
                -
                source_end_frame
            )

            coordinates = cls.interpolate_coordinates(
                source_row.coordinates,
                target_row.coordinates,
                total_steps,
                step,
            )

            rows_to_create.append(FrameObject(
                frame_id=frame_id,
                object_id=source_object_id,
                coordinates=coordinates,
                confidence=source_row.confidence,
                tag=source_row.tag,
                timestamp=source_row.timestamp,
                is_active=True,
                is_interpolated=True,
            ))

        if rows_to_create:
            # Modern Django backends return primary keys from bulk inserts.  A
            # row-by-row fallback retains snapshot correctness on backends
            # that cannot return them.
            if connection.features.can_return_rows_from_bulk_insert:
                FrameObject.objects.bulk_create(
                    rows_to_create,
                    batch_size=SnapshotBuilder.batch_size(),
                )
                created_frame_ids = [row.pk for row in rows_to_create]
            else:
                for row in rows_to_create:
                    row.save(force_insert=True)
                    created_frame_ids.append(row.pk)

        # =====================================
        # UPDATE TRACK
        # =====================================
        if update_track:

            if source_object_id == target_object_id:

                target_track = source_track

                source_track.end_frame = (
                    target_track.end_frame
                )

            else:

                source_track.end_frame = (
                    target_start_frame - 1
                )

            source_track.save(
                update_fields=["end_frame"]
            )        
        
        # print(
        #     "[INTERPOLATE] DB Track AFTER update:",
        #     {
        #         "track_id": db_track.track_id,
        #         "start_frame": db_track.start_frame,
        #           "end_frame": db_track.end_frame,
        # =====================================
        # FREEZE AFTER TRACK SNAPSHOT
        # =====================================

        after_track_snapshot = SnapshotBuilder.capture((
            ObjectTrack.objects.filter(track_id=source_track.track_id),
            ("track_id", "end_frame"),
        ))
        # print(
        #     "[INTERPOLATE] AFTER SNAPSHOT=%s",
        #     after_track_snapshot,
        # )
        # =====================================
        # FRAMEOBJECT SNAPSHOT
        # =====================================

        after_qs = FrameObject.objects.filter(
            pk__in=created_frame_ids
        )

        frame_snapshot = SnapshotBuilder.build(
            before_qs_map={},
            after_qs_map={
                "FrameObject": after_qs,
            },
        )

        # =====================================
        # OBJECTTRACK SNAPSHOT
        # =====================================



        before_state = {
            "ObjectTrack": {
                "created": [],
                "deleted": [],
                "updated": before_track_snapshot,
            }
        }

        after_state = {
            "FrameObject": frame_snapshot["FrameObject"],
            "ObjectTrack": {
                "created": [],
                "deleted": [],
                "updated": after_track_snapshot,
            },
        }

        # =====================================
        # AUDIT LOG
        # =====================================
        # print(
        #     "[INTERPOLATE] BEFORE_STATE=%s",
        #     before_state,
        # )

        # print(
        #     "[INTERPOLATE] AFTER_STATE=%s",
        #     after_state,
        # )

        SnapshotLogger.log(
            project_id=project_id,
            operation="interpolate",
            before_state=before_state,
            after_state=after_state,
            objects_data={
                "source_object_id": source_object_id,
                "source_end_frame": source_end_frame,
                "target_object_id": target_object_id,
                "target_start_frame": target_start_frame,
            },
        )
        return { "source_object_id": source_object_id, "target_object_id": target_object_id, "frames_created": len(created_frame_ids), "updated_end_frame": source_track.end_frame, }
