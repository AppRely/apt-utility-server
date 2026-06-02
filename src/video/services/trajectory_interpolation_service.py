from django.db import transaction

from ..models import (
    FrameObject,
    ObjectTrack,
    VideoFrame,
)

from .snapshot_builder import SnapshotBuilder
from .snapshot_logger import SnapshotLogger


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
            raise ValueError(
                "Coordinate count mismatch"
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
        source_object_id,
        source_end_frame,
        target_object_id,
        target_start_frame,
    ):

        gap = (
            target_start_frame
            -
            source_end_frame
            -
            1
        )

        if gap <= 0:
            raise ValueError(
                "No missing frames found"
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

        before_qs = FrameObject.objects.none()

        created_frame_ids = []

        total_steps = (
            target_start_frame
            -
            source_end_frame
        )

        for frame_no in range(
            source_end_frame + 1,
            target_start_frame,
        ):

            frame = VideoFrame.objects.get(
                project_id_id=project_id,
                frame_no=frame_no,
            )

            exists = FrameObject.objects.filter(
                frame=frame,
                object_id=source_object_id,
            ).exists()

            if exists:
                continue

            step = (
                frame_no
                -
                source_end_frame
            )

            coordinates = (
                cls.interpolate_coordinates(
                    source_row.coordinates,
                    target_row.coordinates,
                    total_steps,
                    step,
                )
            )

            created = FrameObject.objects.create(
                frame=frame,
                object_id=source_object_id,
                coordinates=coordinates,
                confidence=source_row.confidence,
                tag=source_row.tag,
                timestamp=source_row.timestamp,
                is_active=True,
                is_interpolated=True,
            )

            created_frame_ids.append(
                created.id
            )

        after_qs = FrameObject.objects.filter(
            id__in=created_frame_ids
        )

        before_state = SnapshotBuilder.build(
            before_qs_map={
                "FrameObject": before_qs
            },
            after_qs_map={},
        )

        after_state = SnapshotBuilder.build(
            before_qs_map={},
            after_qs_map={
                "FrameObject": after_qs
            },
        )

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

        source_track = ObjectTrack.objects.get(
            project_id_id=project_id,
            object_id=source_object_id,
        )

        if source_object_id == target_object_id:

            target_track = ObjectTrack.objects.get(
                project_id_id=project_id,
                object_id=target_object_id,
            )

            source_track.end_frame = (
                target_track.end_frame
            )

        else:

            source_track.end_frame = (
                target_start_frame - 1
            )

        source_track.save(
            update_fields=[
                "end_frame"
            ]
        )

        return {
            "source_object_id":
                source_object_id,

            "target_object_id":
                target_object_id,

            "frames_created":
                len(created_frame_ids),

            "updated_end_frame":
                source_track.end_frame,
        }