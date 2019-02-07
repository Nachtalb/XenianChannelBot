from typing import Generator

from mongoengine import BooleanField, DictField, LongField, ReferenceField
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
    def file_ids(self) -> Generator[int, None, None]:
        if self._file_id is not None:
            yield from self._file_id
            return

        self._file_id = []
        message = self.original_object

        if not message:
            return

        for file_type in self.file_types:
            file = message.get(file_type, None)
            if isinstance(file, list):
                for file_dict in file:
                    self._file_id.append(file_dict['file_id'])
            elif isinstance(file, dict) and 'file_id' in file:
                self._file_id.append(file['file_id'])

        yield from self._file_id
