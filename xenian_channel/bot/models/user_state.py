from threading import Lock

from mongoengine import Document, NULLIFY, ReferenceField, StringField

from xenian_channel.bot.models import ChannelSettings
from xenian_channel.bot.models.tg_user import TgUser

__all__ = ['UserState']


class UserState(Document):
    IDLE = 'idle'
    ADDING_CHANNEL = 'adding channel'
    REMOVING_CHANNEL = 'removing channel'
    CHANNEL_ACTIONS = 'channel actions'
    IN_SETTINGS = 'in settings'
    CHANGE_DEFAULT_CAPTION = 'change default caption'
    CHANGE_DEFAULT_REACTION = 'change default reaction'
    CREATE_SINGLE_POST = 'create single post'
    SEND_LOCKED = 'send_locked'

    user = ReferenceField(TgUser)
    state = StringField(default=IDLE)

    current_channel = ReferenceField(ChannelSettings, reverse_delete_rule=NULLIFY)

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
