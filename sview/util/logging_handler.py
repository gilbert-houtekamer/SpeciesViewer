import logging


class LoggingHandler(logging.Handler):

    def __init__(self):
        super().__init__()
        self.messages = []
        self.listeners = []

    def clear_listeners(self):
        self.listeners.clear()

    def add_listener(self, listener):
        self.listeners.append(listener)

    def remove_listener(self, listener):
        self.listeners.remove(listener)

    def emit(self, record):
        msg = self.format(record)
        self.messages.append(msg)
        for listener in self.listeners:
            listener.emit(record, msg)
