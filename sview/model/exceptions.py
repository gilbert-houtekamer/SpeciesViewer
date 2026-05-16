
class SVModelException(Exception):
    def __init__(self, details):
        super().__init__('Species Viewer Exception: ' + details)


