from mongoengine import BooleanField, LongField, StringField
from telegram import Chat

from xenian_channel.bot.models.fields.option_string_field import OptionStringField
from xenian_channel.bot.models.telegram import TelegramDocument

__all__ = ['TgChat']


class TgChat(TelegramDocument):
    meta = {'collection': 'telegram_chat'}

    class Meta:
        original = Chat
        load_self = 'get_chat'

    id = LongField(primary_key=True)

    type = OptionStringField(['private', 'channel', 'group', 'supergroup'])
    all_members_are_administrators = BooleanField(default=False)

    title = StringField(default='')
    first_name = StringField(default='')
    username = StringField(default='')

    def __str__(self):
        return self.username or self.title or self.id

    def __repr__(self):
        return f'{super().__repr__()} - {self.__str__()}'
