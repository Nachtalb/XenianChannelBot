from threading import Lock

from mongoengine import Document, NULLIFY, ReferenceField, StringField, DictField

from xenian_channel.bot.models import ChannelSettings
from xenian_channel.bot.models.tg_user import TgUser

__all__ = ['UserState']


class UserState(Document):
    IDLE = 'idle'
    ADDING_CHANNEL = 'adding channel'
    REMOVING_CHANNEL = 'removing channel'
    CHANNEL_ACTIONS = 'channel actions'
    IMPORT_MESSAGES = 'import messages'
    IN_SETTINGS = 'in settings'
    CHANGE_DEFAULT_CAPTION = 'change default caption'
    CHANGE_DEFAULT_REACTION = 'change default reaction'
    CREATE_SINGLE_POST = 'create single post'
    SCHEDULE_ADDED_MESSAGES_WHEN = 'schedule added messages when'
    SCHEDULE_ADDED_MESSAGES_DELAY = 'schedule added messages delay'
    SCHEDULE_ADDED_MESSAGES_BATCH = 'schedule added messages batch'
    SCHEDULE_ADDED_MESSAGES_CONFIRMATION = 'schedule added messages confirmation'
    SEND_LOCKED = 'send_locked'

    user = ReferenceField(TgUser)
    state = StringField(default=IDLE)

    current_channel = ReferenceField(ChannelSettings, reverse_delete_rule=NULLIFY)
    state_data = DictField(default={})

    save_lock = Lock()

    def __setattr__(self, key, value):
        super(UserState, self).__setattr__(key, value)
        if self._initialised and key == 'state':
            self.save()

    def __repr__(self):
        return f'{str(self.user)}, ' \
            f'state: {self.state}, ' \
            f'channel: {str(self.current_channel) if self.current_channel else "None"}'

    def save(self, *args, **kwargs):
        try:
            self.save_lock.acquire()
            super().save(*args, **kwargs)
        finally:
            self.save_lock.release()
