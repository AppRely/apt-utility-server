# services/frame_timeline_service.py

import logging
from collections import defaultdict
from collections import OrderedDict

from django.db.models import Case
from django.db.models import IntegerField
from django.db.models import Value
from django.db.models import When

from ..models import FrameObject

logger = logging.getLogger(__name__)


class FrameTimelineService:

    @staticmethod
    def fetch(
        project_id: int,
        start: int,
        end: int,
        object_ids: list = None,
    ):

        try:

            query = (
                FrameObject.objects.filter(
                    frame__project_id=project_id,
                    frame__frame_no__gte=start,
                    frame__frame_no__lte=end,
                    is_active=True,
                )
            )

            # =====================================
            # FILTER OBJECTS
            # =====================================

            if object_ids:

                query = query.filter(
                    object_id__in=object_ids
                )

                # =====================================
                # PRESERVE FRONTEND ORDER
                # =====================================

                preserved_order = Case(
                    *[
                        When(
                            object_id=obj_id,
                            then=Value(index),
                        )
                        for index, obj_id
                        in enumerate(object_ids)
                    ],
                    output_field=IntegerField(),
                )

                query = query.order_by(
                    "frame__frame_no",
                    preserved_order,
                )

            else:

                query = query.order_by(
                    "frame__frame_no",
                    "object_id",
                )

            # =====================================
            # FETCH REQUIRED FIELDS
            # =====================================

            queryset = (
                query
                .values_list(
                    "frame__frame_no",
                    "object_id",
                    "coordinates",
                )
                .iterator(chunk_size=10000)
            )

            # =====================================
            # PRESERVE OBJECT ORDER
            # =====================================

            results = defaultdict(
                OrderedDict
            )

            for (
                frame_no,
                object_id,
                coordinates,
            ) in queryset:

                results[str(frame_no)][str(object_id)] = (
                    coordinates[0]
                    if coordinates
                    else None
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