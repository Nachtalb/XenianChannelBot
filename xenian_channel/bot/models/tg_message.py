from typing import Generator

from elasticsearch.exceptions import ConnectionError, NotFoundError
from mongoengine import BooleanField, DictField, LongField, ReferenceField
from telegram import Bot, File, Message
from urllib3.exceptions import NewConnectionError

from xenian_channel.bot import image_match_ses
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

    message_id = LongField()

    chat = ReferenceField(TgChat)
    from_user = ReferenceField(TgUser)

    original_object = DictField()

    reactions = DictField()

    is_current_message = BooleanField(default=False)

    def __new__(cls, *args, **kwargs):
        first_arg = next(iter(args), None)
        if first_arg is not None and isinstance(first_arg, Message):
            chat = TgChat.objects(id=first_arg.chat.id).first()
            obj = cls.objects(message_id=first_arg.message_id, chat=chat).first()
            if obj:
                obj.self_from_object(first_arg)
                return obj
        return super().__new__(cls)

    def __repr__(self):
        return f'{super().__repr__()} - {self.message_id}:{self.chat.id}'

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

    def is_any_type_of(self, *types: str) -> str or None:
        if isinstance(types, str):
            types = [types]

        for type in types:
            if self.original_object.get(type):
                return type

    def _get_file_for_image_search(self, bot: Bot) -> File or None:
        if (not bot and not self._bot) or self.is_any_type_of('photo', 'sticker') is None:
            return
        self._bot = bot or self._bot
        file_id = next(iter(self.file_ids), None)

        if not file_id:
            return

        return self._bot.get_file(file_id=file_id)

    def find_similar(self, chat_id: int = None, bot: Bot = None) -> list:
        file = self._get_file_for_image_search(bot)
        if not file:
            return []
        try:
            filter = None
            if chat_id:
                filter = {'term': {'metadata.chat_id': chat_id}}

            return image_match_ses.search_image(file.file_path, pre_filter=filter)
        except (ConnectionError, NewConnectionError, NotFoundError):
            return []

    def get_self_image_match(self, bot: Bot = None) -> dict or None:
        results = self.find_similar(bot)

        for result in results:
            if result['dist'] == 0.0:
                return result

    def add_to_image_match(self, chat_id: int = None, bot: Bot = None):
        metadata = None
        if chat_id:
            metadata = {'chat_id': chat_id}
        file = self._get_file_for_image_search(bot)
        if not file:
            return
        try:
            image_match_ses.add_image(file.file_path, metadata=metadata)
        except (ConnectionError, NewConnectionError, NotFoundError):
            return
