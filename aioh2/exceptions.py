__all__ = ['AIOH2Exception', 'SendException']


class AIOH2Exception(Exception):
    pass


class SendException(AIOH2Exception):
    def __init__(self, data):
        super().__init__()
        self.data = data
