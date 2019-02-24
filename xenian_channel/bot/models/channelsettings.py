from contextlib import contextmanager
from threading import Lock

from elasticsearch.exceptions import ConnectionError, NotFoundError
from mongoengine import DictField, Document, ListField, ReferenceField, StringField
from urllib3.exceptions import NewConnectionError

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
    added_messages = ListField(ReferenceField(TgMessage))
    import_messages = ListField(ReferenceField(TgMessage))

    # should actually be DictField(ListField(ReferenceField(TgMessage))) but it has errors if used like so
    queued_messages = DictField(default={})
    import_messages_queue = DictField(default={})
    scheduled_messages = DictField(default={})

    save_lock = Lock()

    def __repr__(self):
        return f'{str(self.user)} - {str(self.chat)}'

    @contextmanager
    def save_contextmanager(self, *args, **kwargs):
        try:
            self.save_lock.acquire()
            yield
            self.before_save()
            super().save(*args, **kwargs)
        finally:
            self.save_lock.release()

    def before_save(self):
        try:
            if hasattr(self, '_changed_fields') and 'sent_messages' in self._changed_fields:
                before = self._get_collection().find_one(({'_id': self.pk}))
                newly_sent = filter(lambda item: item.message_id not in before['sent_messages'], self.sent_messages)
                for message in newly_sent:
                    message.add_to_image_match(metadata={'chat_id': self.chat.id})
        except (ConnectionError, NewConnectionError, NotFoundError):
            pass

    def save(self, *args, **kwargs):
        with self.save_contextmanager():
            pass
