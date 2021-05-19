from dmoj.utils.unicode import utf8text


class CompileError(Exception):
    def __init__(self, message):
        super().__init__(utf8text(message, 'replace'))


class InternalError(Exception):
    pass


class OutputLimitExceeded(Exception):
    def __init__(self, stream, limit, data=None):
        if data is None:
            super().__init__("exceeded %d-byte limit on %s stream" % (limit, stream))
        else:
            super().__init__(
                "exceeded %d-byte limit on %s stream.\nFirst %d bytes of data: %s "
                % (limit, stream, len(data), data.decode())
            )


class InvalidCommandException(Exception):
    def __init__(self, message=None):
        self.message = message
