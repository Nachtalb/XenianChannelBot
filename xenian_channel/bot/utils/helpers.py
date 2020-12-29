import time


class Stopwatch:
    def __init__(self, name):
        self.name = name
        self.start = self.current_milli_time()
        self.last_full_duration = 0
        self.stamp('Start')

    def stamp(self, text):
        now = self.current_milli_time()
        full_duration = now - self.start
        duration = full_duration - self.last_full_duration
        self.last_full_duration = full_duration
        print('{} - {}: {}/{}'.format(self.name, text, duration, full_duration))

    def current_milli_time(self):
        return int(round(time.time() * 1000))
