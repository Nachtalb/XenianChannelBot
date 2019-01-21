from mongoengine import DictField, LongField, ReferenceField
from telegram import Message

from xenian_channel.bot.models.telegram import TelegramDocument
from xenian_channel.bot.models.tg_chat import TgChat
from xenian_channel.bot.models.tg_user import TgUser

__all__ = ['TgMessage']


class TgMessage(TelegramDocument):
    meta = {'collection': 'telegram_message'}

    class Meta:
        original = Message

    message_id = LongField(primary_key=True)

    chat = ReferenceField(TgChat)
    from_user = ReferenceField(TgUser)

    original_object = DictField()

    reactions = DictField()
