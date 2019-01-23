import logging
from typing import Dict

from telegram import Bot, Update
from telegram.ext import CommandHandler, Filters, MessageHandler

from xenian_channel.bot.models import TgUser, TgChat, TgMessage
from xenian_channel.bot.settings import LOG_LEVEL

__all__ = ['BaseCommand']


class BaseCommand:
    """Base of any command class

    This is the base which should be used for any new command class. A command class is a class containing one or more
    commands. For this to work the created command class must be called afterwards at least once. Like this an entry is
    made in this classes :obj:`BaseCommand.all_commands` for the new command class. This has the advantage that you can
    store all the data for your commands together in one place.

    Your commands are also automatically added to the Telegram Updater, as well as listed in the output of the /commands
    command from the :class:`xenian.bot.commands.builtins.Builtins` commands (unless you have hidden on true for your
    command).

    Examples:
        >>> from telegram.ext import Filters
        >>> from xenian_channel.bot.commands.base import BaseCommand
        >>>
        >>> class MyCommands(BaseCommand):
        >>>     def __init__(self):
        >>>         self.commands = [
        >>>             {
        >>>                 'title': 'Echo yourself',
        >>>                 'description': 'Return messages that you send me',
        >>>                 'command': self.echo,
        >>>                 'handler': MessageHandler,
        >>>                 'options': {'filters': Filters.text},
        >>>                 'group': 0
        >>>             }
        >>>         ]
        >>>
        >>>         super(MyCommands, self).__init__()
        >>>
        >>>     def echo(self, bot, update):
        >>>         update.message.reply_text(update.message.text)

    Attributes:
        all_commands (:class:`list` of :obj:`class`): A list of all initialized command classes
        commands (:obj:`list` of :obj:`dict`): A list of dictionary with the following keys:
            - title (:class:`str`): Title of the command, (if not set name of the function will be taken)
            - description (:class:`str`): A short description for the command
            - command_name (:class:`str`): A name for the command (if not set, the functions name will be taken)
            - command : The command function
            - handler : Handler for the command like :class:`CommandHandler` or :class:`MessageHandler`.
                Default (:class:`CommandHandler`)
            - options (:class:`dict`): A dictionary of options for the command.
                Default {'callback': command, 'command': command_name}
            - hidden (:class:`bool`): If the command is shown in the overview of `/commands`
            - args (:class:`str`): If the command has arguments define them here as text like: "USERNAME PASSWORD"
            - group (:class:`int`): Which handler group the command should be in
        group (:class:`str`): The group name shown in the /commands message
    """
    all_commands = []
    commands = []
    group = 'Base Group'

    def __init__(self):
        """Initialize the command class

        Notes:
            super(BaseCommand, self).__init__() has to be run after the self.commands setup
        """
        self.bot = None
        self.update = None
        self.user = None
        self.message = None
        self.chat = None

        self.tg_chat = None
        self.tg_user = None
        self.tg_message = None

        BaseCommand.all_commands.append(self)

        self.normalize_commands()

    def on_call_wrapper(self, method: callable):
        def wrapper(bot: Bot, update: Update, *args, **kwargs):
            self.on_call(bot, update)
            method(bot, update, *args, **kwargs)

        return wrapper

    def on_call(self, bot: Bot, update: Update):
        self.bot = bot
        self.update = update

        self.user = update.effective_user
        self.message = update.effective_message
        self.chat = update.effective_chat

        self.tg_user = None
        self.tg_chat = None
        self.tg_message = None

        if self.user:
            self.tg_user = TgUser(self.user)
            self.tg_user.save()
        if self.chat:
            self.tg_chat = TgChat(self.chat)
            self.tg_chat.save()
        if self.message:
            self.tg_message = TgMessage.from_object(self.message)
            self.tg_message.save()

    def normalize_commands(self):
        """Normalize commands faults, add defaults and add them to :obj:`BaseCommand.all_commands`
        """
        updated_commands = []
        alias_commands = []
        for command in self.commands:
            if isinstance(command.get('alias', None), str):
                alias_commands.append(command)
                continue

            command = {
                'title': command.get('title', None) or command['command'].__name__.capitalize().replace('_', ' '),
                'description': command.get('description', ''),
                'command_name': command.get('command_name', command['command'].__name__),
                'command': self.on_call_wrapper(command['command']),
                'handler': command.get('handler', CommandHandler),
                'options': command.get('options', {}),
                'hidden': command.get('hidden', False),
                'args': command.get('args', []),
                'group': command.get('group', 0)
            }

            if command['handler'] == CommandHandler and command['options'].get('command', None) is None:
                command['options']['command'] = command['command_name']

            if command['handler'] == MessageHandler and command['options'].get('filters', None) is None:
                command['options']['filters'] = Filters.all

            # Set CallbackQueryHandler options if not yet set
            if command['options'].get('callback', None) is None:
                command['options']['callback'] = command['command']

            updated_commands.append(command)

        self.commands = updated_commands

        for alias_command in alias_commands:
            alias_name = alias_command['alias']

            real_command = self.get_command_by_name(alias_name)
            if not real_command:
                continue

            new_command = self.copy_command(real_command)
            new_command['options']['command'] = alias_command['command_name']
            for key, value in alias_command.items():
                if key in ['title', 'description', 'hidden', 'group', 'command_name']:
                    new_command[key] = value

            updated_commands.append(new_command)

        for command in updated_commands:
            try:
                int(command['group'])
            except ValueError:
                raise ValueError('Command group has to be an integer: command {}, given group {}'.format(
                    command['command_name'], command['group']
                ))

        self.commands = updated_commands

    def copy_command(self, command: Dict) -> Dict:
        """Copy command to a new dict

        Do not use a single Dict.copy because the dicts are multidimensional and .copy i only onedimensional.
        Do not use deepcopy because it copies functions to a new object which leads to errors

        Instead we use copy the dict with its normal copy function and then iterate over the dict and repeat this for
        every sub dict.

        Args:
            command (:obj:`Dict`): A command dict to copy

        Returns:
            :obj:`Dict`: The copied dict

        """
        new_command = command.copy()
        for key, value in command.items():
            if isinstance(value, Dict):
                new_command[key] = self.copy_command(value)
        return new_command

    def get_command_by_name(self, name: str) -> dict:
        """Returns a command form self.command with the given name

        Args:
            name (:obj:`str`): Name of the command

        Returns:
            (:obj:`dict` | :obj:`None`): The found command or :obj:`None` if no command was found
        """
        commands_found = list(filter(lambda command: command['command_name'] == name, self.commands))
        return commands_found[0] if commands_found else None

    def not_implemented(self, *args, **kwargs):
        if LOG_LEVEL <= logging.DEBUG:
            self.message.reply_text('This command was not implemented by the admin.',
                                    reply_to_message_id=self.message.message_id)
