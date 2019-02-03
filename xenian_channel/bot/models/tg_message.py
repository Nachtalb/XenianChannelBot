from mongoengine import DictField, LongField, ReferenceField, BooleanField
from telegram import Message

from xenian_channel.bot.models.telegram import TelegramDocument
from xenian_channel.bot.models.tg_chat import TgChat
from xenian_channel.bot.models.tg_user import TgUser

__all__ = ['TgMessage']


class TgMessage(TelegramDocument):
    meta = {'collection': 'telegram_message'}
    file_types = [
        'audio',
        'sticker',
        'video',
        'animation',
        'photo',
        'document',
        'voice',
        'video_note',
    ]
    _file_id = None

    class Meta:
        original = Message

    message_id = LongField(primary_key=True)

    chat = ReferenceField(TgChat)
    from_user = ReferenceField(TgUser)

    original_object = DictField()

    reactions = DictField()

    is_current_message = BooleanField(default=False)

    def __repr__(self):
        return f'{super().__repr__()} - {self.message_id}'

    @property
    def file_id(self) -> int or None:
        if self._file_id:
            return self._file_id

        message = self.original_object

        if not message:
            return None

        for file_type in self.file_types:
            file = message.get(file_type, {})
            if file and 'file_id' in file:
                self._file_id = file['file_id']
                return self._file_id
