from telegram import Bot, Update
from telegram.ext import MessageHandler, run_async

from xenian_channel.bot.models import TgMessage
from .base import BaseCommand

__all__ = ['database']


class Database(BaseCommand):
    """A set of database commands

    Attributes:
        users (:obj:`pymongo.collection.Collection`): Connection to the pymongo databased
    """

    name = 'Bot Helpers'

    def __init__(self):
        self.commands = [
            {
                'command': self.add_to_database_command,
                'title': 'Add to Database',
                'description': 'Adds user, message and chat to database',
                'handler': MessageHandler,
                'group': 1,
                'hidden': True,
            },
        ]
        super(Database, self).__init__()

    @run_async
    def add_to_database_command(self, bot: Bot, update: Update):
        """Add a telegram objects to the database if he is not already in it

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            update (:obj:`telegram.update.Update`): Telegram Api Update Object
        """
        message = TgMessage(update.effective_message)
        message.cascade_save()


database = Database()
