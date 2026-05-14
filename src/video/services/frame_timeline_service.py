# services/frame_timeline_service.py

import logging
from collections import defaultdict

from ..models import FrameObject

logger = logging.getLogger(__name__)


class FrameTimelineService:

    @staticmethod
    def fetch(
        project_id: int,
        start: int,
        end: int,
    ):

        try:

            queryset = (
                FrameObject.objects.filter(
                    frame__project_id=project_id,
                    frame__frame_no__gte=start,
                    frame__frame_no__lte=end,
                    is_active=True,
                )
                .values_list(
                    "frame__frame_no",
                    "object_id",
                    "coordinates",
                )
                .iterator(chunk_size=10000)
            )

            results = defaultdict(dict)

            for (
                frame_no,
                object_id,
                coordinates,
            ) in queryset:

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