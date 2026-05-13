import logging
from collections import defaultdict

from ..models import (
    FrameObject,
    VideoFrame,
)

logger = logging.getLogger(__name__)


class FrameTimelineService:

    @staticmethod
    def fetch(project_id: int):

        try:

            # frame_id -> frame_no map
            frame_map = dict(
                VideoFrame.objects.filter(
                    project_id=project_id
                ).values_list(
                    "id",
                    "frame_no",
                )
            )

            queryset = (
                FrameObject.objects.filter(
                    frame__project_id=project_id,
                    is_active=True,
                )
                .values_list(
                    "frame_id",
                    "object_id",
                    "coordinates",
                )
                .iterator(chunk_size=50000)
            )

            results = defaultdict(dict)

            for frame_id, object_id, coordinates in queryset:

                frame_no = frame_map.get(frame_id)

                if frame_no is None:
                    continue

                results[str(frame_no)][str(object_id)] = (
                    coordinates[0]
                    if coordinates else None
                )

            return {
                "f": dict(results)
            }

        except Exception as e:

            logger.error(
                f"FrameTimelineService Error: {str(e)}",
                exc_info=True,
            )

            raise