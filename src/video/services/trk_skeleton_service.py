# src/video/services/trk_skeleton_service.py

class TrkSkeletonService:

    @staticmethod
    def extract(trk):
        try:
            params = trk.trkData.get("trkInfo", {}).get("params", {})
            return params.get("op_affinity_graph", [])
        except Exception:
            return []
