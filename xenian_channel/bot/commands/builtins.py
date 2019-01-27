from typing import Dict, Iterable

from telegram import Bot, Chat
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler
from telegram.parsemode import ParseMode

# from xenian_channel.bot import mongodb_database
from xenian_channel.bot.settings import ADMINS, SUPPORTER
from xenian_channel.bot.utils import get_user_chat_link, render_template
from .base import BaseCommand

__all__ = ['builtins']


class Builtins(BaseCommand):
    """A set of base commands which every bot should have
    """

    Group = 'Bot Helpers'
    data_set_name = 'builtins'

    def __init__(self):
        self.commands = [
            {'command': self.start, 'description': 'Initialize the bot'},
            {'command': self.commands, 'description': 'Show all available commands', 'options': {'pass_args': True}},
            {'command': self.support, 'description': 'Contact bot maintainer for support of any kind'},
            {'command': self.register, 'description': 'Register the chat_id for admins and supporters', 'hidden': True},
            {'command_name': 'help', 'alias': 'commands'},
            {'command_name': 'contribute', 'alias': 'error'},
            {
                'command': self.callback_nothing,
                'description': 'Answer callback calls without doing anything. Can be used for preview buttons or '
                               'anything alike',
                'handler': CallbackQueryHandler,
                'options': {
                    'pattern': '^nothing$',
                }
            },
            {
                'command': self.contribute_error,
                'command_name': 'error',
                'description': 'Send the supporters and admins a request of any kind',
                'args': ['text'],
            },
        ]

        # self.admin_db = mongodb_database.admins
        # self.supporter_db = mongodb_database.supporter

        super(Builtins, self).__init__()

    def callback_nothing(self):
        self.update.callback_query.answer()

    def start(self):
        """Initialize the bot
        """
        self.message.reply_text(render_template('start.html.mako'), parse_mode=ParseMode.HTML)

    def commands(self, args):
        """Generate and show list of available commands

        Args:
            args (:obj:`list`, optional): List of sent arguments
        """
        direct_commands = {}
        indirect_commands = {}
        for command_class in BaseCommand.all_commands:
            group_name = command_class.group

            direct_commands.setdefault(group_name, [])
            indirect_commands.setdefault(group_name, [])

            # Direct commands (CommandHandler)
            for command in [cmd for cmd in command_class.commands
                            if cmd['handler'] == CommandHandler and not cmd['hidden']]:
                direct_commands[group_name].append({
                    'command': command['command_name'],
                    'args': command['args'],
                    'title': command['title'],
                    'description': command['description'],
                })
            if not direct_commands[group_name]:
                del direct_commands[group_name]

            # Indirect commands (MessageHandler)
            for command in [cmd for cmd in command_class.commands
                            if cmd['handler'] == MessageHandler and not cmd['hidden']]:
                indirect_commands[group_name].append({
                    'title': command['title'],
                    'description': command['description'],
                })

            if not indirect_commands[group_name]:
                del indirect_commands[group_name]
        if 'raw' in args:
            reply = render_template('commands_raw.html.mako', direct_commands=direct_commands)
        elif 'rst' in args:
            reply_direct = render_template('commands_rst_direct.mako', direct_commands=direct_commands)
            print(reply_direct)
            self.message.reply_text(reply_direct)

            if indirect_commands:
                reply_indirect = render_template('commands_rst_indirect.mako', indirect_commands=indirect_commands)
                print(reply_indirect)
                self.message.reply_text(reply_indirect)
            return
        else:
            reply = render_template('commands.html.mako',
                                    direct_commands=direct_commands,
                                    indirect_commands=indirect_commands)
        self.message.reply_text(reply, parse_mode=ParseMode.HTML)

    def support(self):
        """Contact bot maintainer for support of any kind
        """
        self.message.reply_text(
            'If you need any help do not hesitate to contact me via "/contribute YOUR_MESSAGE", if you have found an '
            'error please use "/error ERROR_DESCRIPTION".\n\nIf you like this bot you can give me rating here: '
            'https://telegram.me/storebot?start=xenianchannelbot'.format(SUPPORTER[0]))

    def contribute_error(self):
        """User can use /contribute or /error to let all supporters / admins know of something
        """
        split_text = self.message.text.split(' ', 1)
        command = split_text[0].lstrip('/')

        if len(split_text) < 2:
            self.message.reply_text(f'Please describe your request with "/{command} YOUR_DESCRIPTION"')
            return

        text = split_text[1]
        user = get_user_chat_link(self.message.from_user)
        message_text = f'{command.capitalize()} form {user}: {text}'

        self.write_to_chats(self.bot, self.admin_db.find(), message_text)
        self.write_to_chats(self.bot, self.supporter_db.find(), message_text)

        self.message.reply_text('I forwarded your request to the admins and supporters.')

    @staticmethod
    def write_to_chats(bot: Bot, chats: Iterable[Chat] or Iterable[Dict[str]], message: str):
        """Send a message to all given chats

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            chats (:obj:`Iterable[Chat]` | :obj:`Iterable[Dict[str]]`): A list of chats to write to
            message (:obj:`str`): The text to send
        """
        for chat in chats:
            id = None
            if isinstance(chat, Chat):
                id = chat.id
            elif isinstance(chat, dict):
                id = chat.get('id') or chat.get('chat_id')

            if not id:
                continue

            bot.send_message(chat_id=id, text=message)

    def register(self):
        """Register the chat_id for admins and supporters
        """
        data = {'chat_id': self.chat.id}

        reply = 'You were registered as an'

        if '@{}'.format(self.user.username) in ADMINS:
            self.admin_db.update(data, data, upsert=True)
            reply += '\n - Admin'

        if '@{}'.format(self.user.username) in SUPPORTER:
            self.supporter_db.update(data, data, upsert=True)
            reply += '\n - Supporter'

        if not '\n' in reply:
            reply = 'You are neither an admin nor a supporter.'

        self.message.reply_text(reply)


builtins = Builtins()
