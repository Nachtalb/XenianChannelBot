from mongoengine import BooleanField, LongField, StringField
from telegram import User

from xenian_channel.bot.models.telegram import TelegramDocument

__all__ = ['TgUser']


class TgUser(TelegramDocument):
    meta = {'collection': 'telegram_user'}

    class Meta:
        original = User

    id = LongField(primary_key=True)

    first_name = StringField()
    is_bot = BooleanField()
    username = StringField()
    language_code = StringField()
