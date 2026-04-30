from ..TrkFile import Trk


class TrkValidationService:
    @staticmethod
    def load_and_validate(trk_path):
        trk = Trk(trk_path)

        if trk.getframe(trk.T0) is None:
            raise ValueError("Upload failed — no TRK data extracted.")

        return trk
