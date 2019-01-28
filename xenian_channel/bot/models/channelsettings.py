from mongoengine import Document, ListField, ReferenceField, StringField
from threading import Lock

from xenian_channel.bot.models.tg_chat import TgChat
from xenian_channel.bot.models.tg_message import TgMessage
from xenian_channel.bot.models.tg_user import TgUser

__all__ = ['ChannelSettings']


class ChannelSettings(Document):
    chat = ReferenceField(TgChat)
    user = ReferenceField(TgUser)

    caption = StringField()
    reactions = ListField(StringField())

    sent_messages = ListField(ReferenceField(TgMessage))
    queued_messages = ListField(ReferenceField(TgMessage))
    added_messages = ListField(ReferenceField(TgMessage))

    save_lock = Lock()

    def __repr__(self):
        return f'{str(self.user)} - {str(self.chat)}'

    def save(self, *args, **kwargs):
        try:
            self.save_lock.acquire()
            super().save(*args, **kwargs)
        finally:
            self.save_lock.release()
