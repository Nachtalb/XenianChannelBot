import logging
from contextlib import contextmanager
from datetime import timedelta
from threading import Lock
from typing import Iterable

from mongoengine import DictField, Document, ListField, ReferenceField, StringField
from telegram import Bot
from telegram.ext import Job

from xenian_channel.bot.models.tg_chat import TgChat
from xenian_channel.bot.models.tg_message import TgMessage
from xenian_channel.bot.models.tg_user import TgUser

__all__ = ['ChannelSettings']


class ChannelSettings(Document):
    _logger = logging.getLogger('ChannelSettings')
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

    def add_messages_to_elasitcsearch(self, messages: Iterable[TgMessage] or TgMessage or Bot, job: Job = None):
        if isinstance(job, Job) and isinstance(messages, Bot):
            messages = job.context

        if isinstance(messages, TgMessage):
            messages = [messages]

        try:
            for message in messages:
                message.add_to_image_match(metadata={'chat_id': self.chat.id})
        except Exception as e:
            self._logger.warning(f'Could not add messages to elastic search: {messages}')
            self._logger.warning(e)

    def before_save(self):
        try:
            if hasattr(self, '_changed_fields') and 'sent_messages' in self._changed_fields:
                before = self._get_collection().find_one(({'_id': self.pk}))
                newly_sent = filter(lambda item: item.message_id not in before['sent_messages'], self.sent_messages)
                self.add_messages_to_elasitcsearch(newly_sent)
        except Exception as e:
            from xenian_channel.bot import job_queue
            self._logger.warning(e)
            try:
                job_queue.run_once(self.add_messages_to_elasitcsearch, context=newly_sent, when=timedelta(minutes=5))
            except NameError:
                self._logger.warning('Could not add messages to elastic search')

    def save(self, *args, **kwargs):
        with self.save_contextmanager():
            pass
