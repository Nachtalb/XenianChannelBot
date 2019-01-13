from telegram import Message
from telegram.ext import BaseFilter

from xenian_channel.bot.settings import ADMINS

__all__ = ['bot_admin', ]


class AdminFilter:
    """Various "is admin of" filters
    """

    class BotAdmin(BaseFilter):
        def filter(self, message: Message) -> bool:
            """Check if current user is admin of the bot
            """
            return '@' + message.from_user.username in ADMINS


bot_admin = AdminFilter.BotAdmin()
