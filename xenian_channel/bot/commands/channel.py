import logging
from collections import namedtuple
from typing import Callable, Dict, Generator

from telegram import Bot, Chat, Update, User
from telegram.ext import CallbackQueryHandler, MessageHandler, run_async
from telegram.parsemode import ParseMode

from xenian_channel.bot import mongodb_database
from xenian_channel.bot.commands import database
from xenian_channel.bot.settings import LOG_LEVEL
from xenian_channel.bot.utils import get_self
from xenian_channel.bot.utils.magic_buttons import MagicButton
from .base import BaseCommand

__all__ = ['channel']

Permission = namedtuple('Permission', ['is_admin', 'post', 'delete', 'edit'])


class Channel(BaseCommand):
    """A set of channel commands
    """

    name = 'Channel Manager'
    group = 'Channel Manager'

    class states:
        IDLE = 'idle'
        ADDING_CHANNEL = 'adding channel'
        REMOVING_CHANNEL = 'removing channel'
        CHANNEL_ACTIONS = 'channel actions'

    def __init__(self):
        self.commands = [
            {'command': self.add_channel_start, 'command_name': 'addchannel', 'description': 'Add a channel'},
            {'command': self.remove_channel_start, 'command_name': 'removechannel', 'description': 'Remove a channel'},
            {'command': self.list_channels, 'command_name': 'list', 'description': 'List all channels'},
            {
                'command': self.echo_state,
                'command_name': 'state',
                'description': 'Debug - Show users current state',
                'hidden': not (LOG_LEVEL == logging.DEBUG)
            },
            {
                'command': self.reset_state,
                'command_name': 'reset',
                'description': 'Debug - Reset the users current state',
                'hidden': not (LOG_LEVEL == logging.DEBUG)
            },
            {
                'command': MagicButton.message_handler,
                'handler': CallbackQueryHandler,
                'options': {
                    'pattern': '^magic_button:.*',
                },
            },
            # {'command': self.remove_channel, 'title': 'Add a channel'},
            # {'command': self.create_post, 'title': 'Add a channel'},
            # {'command': self.create_posts, 'title': 'Add a channel'},
            # {'command': self.settings, 'title': 'Add a channel'},
            {
                'command': self.message_handler,
                'description': 'Chooses the right thing to do with a message',
                'handler': MessageHandler,
                'hidden': True,
            },
        ]

        self.channel_admin = mongodb_database.channel_admin  # {user_id: Users ID, channel_id: Channels ID}
        self.channel_settings = mongodb_database.channel_settings  # {id: Channels ID, caption: Default caption, reactions: Default reaction}
        self.channel_file = mongodb_database.channel_file  # {channel_id: Channels ID, file_id: Files ID}
        self.user_state = mongodb_database.user_state  # {user_id: Users ID, state: State ID}
        self.files = mongodb_database.files  # {file: Telegram File Object, hash: generated hash value}

        super(Channel, self).__init__()

    def get_user_id(self, user: User or int) -> int:
        """Get the users id

        Args:
            user (:obj:`telegram.user.User` | :obj:`int`): The telegram user as a Telegram object or the his id

        Returns:
            :obj:`str`: The users int
        """
        user_id = user
        if isinstance(user, User):
            user_id = user.id
        elif isinstance(user, Dict):
            user_id = user.get('user_id') or user.get('id')

        if not user_id:
            raise ValueError('user must not be empty')
        return user_id

    def get_chat_id(self, chat: Chat or int) -> int:
        """Get the Chat id

        Args:
            chat (:obj:`telegram.user.User` | :obj:`int`): The telegram Chat as a Telegram object or the his id

        Returns:
            :obj:`str`: The chat int
        """
        chat_id = chat
        if isinstance(chat, Chat):
            chat_id = chat.id
        elif isinstance(chat, Dict):
            chat_id = chat.get('chat_id') or chat.get('id')

        if not chat_id:
            raise ValueError('user must not be empty')
        return chat_id

    def get_channels_of_user(self, user: User) -> Generator[Dict, None, None]:
        """Get all channels of which the user can interact with

        Args:
            user (:obj:`telegram.user.User` | :obj:`int`): The telegram user as a Telegram object or his id

        Returns:
            :obj:`Generator[Dict, None, None]`: List of channels belonging to the user as a generator
        """
        channels = self.channel_admin.find({'user_id': self.get_user_id(user)})
        for channel in channels:
            yield from database.chats.find({'id': channel['channel_id']})

    def set_user_state(self, user: User or int, state: str):
        """Set the current state of a user

        Args:
            user (:obj:`telegram.user.User` | :obj:`int`): The telegram user of which the state shall be set as a
                Telegram User object or the users ID
            state (:obj:`str`): The state the user should be in
        """
        data = {
            'user_id': self.get_user_id(user),
            'state': state
        }
        self.user_state.update({'user_id': data['user_id']}, data, upsert=True)

    def get_user_state(self, user: User or int) -> str:
        """Get the current state a user is in

        Args:
            user (:obj:`telegram.user.User` | :obj:`int`): The telegram user of which the state shall be retrieved as a
                Telegram User object or the users ID

        Returns:
            :obj:`str`: The users current state as a string
        """

        user_id = user.id if isinstance(user, User) else user
        if not user_id:
            raise ValueError('user must not be empty')

        user_state_entry = self.user_state.find_one({'user_id': user_id})
        if not user_state_entry:
            return self.states.IDLE
        else:
            return user_state_entry['state']

    def get_permission(self, bot: Bot, chat: Chat):
        """Get usual permissions of bot from chat

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            chat (:obj:`telegram.chat.Chat`): Telegram Api Chat Object

        Returns:
            :obj:`Permission`: The channels Permission object
        """
        myself = get_self(bot)
        chat_member = bot.get_chat_member(chat.id, myself.id)

        return Permission(
            is_admin=chat_member.status == chat_member.ADMINISTRATOR,
            post=chat_member.can_post_messages,
            delete=chat_member.can_delete_messages,
            edit=chat_member.can_edit_messages,
        )

    # Miscellaneous
    @run_async
    def message_handler(self, bot: Bot, update: Update):
        """Dispatch messages to correct function, defied by the users state

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            update (:obj:`telegram.update.Update`): Telegram Api Update Object
        """
        message = update.effective_message
        if update.channel_post:
            return
        user = message.from_user

        if self.get_user_state(user) == self.states.ADDING_CHANNEL:
            self.add_channel_from_message(bot, update)

    @run_async
    def echo_state(self, bot: Bot, update: Update):
        """Debug method to send the users his state

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            update (:obj:`telegram.update.Update`): Telegram Api Update Object
        """
        update.message.reply_text(f'{self.get_user_state(update.message.from_user)}')

    @run_async
    def reset_state(self, bot: Bot, update: Update, *args, **kwargs):
        """Debug method to send the users his state

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            update (:obj:`telegram.update.Update`): Telegram Api Update Object
        """
        self.set_user_state(update.effective_user, self.states.IDLE)

    @run_async
    def custom_echo_callback_query(self, bot: Bot, update: Update, text: str, callback: Callable,
                                   send_telegram_data: bool = False, *args, **kwargs):
        """Debug method to send the users his state

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            update (:obj:`telegram.update.Update`): Telegram Api Update Object
            text (:obj:`str`): Text to send to user
            callback (:obj:`Callable`): Callable to run before sending user the text
            send_telegram_data (:obj:`bool`): If bot and update should be sent to the callback
        """
        data_to_send = {'bot': bot, 'update': update} if send_telegram_data else {}
        callback(**data_to_send)
        update.effective_message.reply_text(text)

    # Adding Channels
    @run_async
    def add_channel_start(self, bot: Bot, update: Update):
        """Add a channel to your channels

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            update (:obj:`telegram.update.Update`): Telegram Api Update Object
        """
        add_to_channel_instruction = (
            "*Adding a channel*"
            "\n"
            "\nTo add a channel follow these instructions"
            "\n"
            "\n1. Make sure @XenianChannelBot is and admin of your channel"
            "\n2. Forward me any message from that channel"
        )
        update.message.reply_text(text=add_to_channel_instruction, parse_mode=ParseMode.MARKDOWN)
        self.set_user_state(update.message.from_user, self.states.ADDING_CHANNEL)

    def add_channel_from_message(self, bot: Bot, update: Update):
        """Add a channel to your channels

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            update (:obj:`telegram.update.Update`): Telegram Api Update Object
        """
        user = update.message.from_user
        chat = update.message.forward_from_chat

        if not chat:
            update.message.reply_text('You have to send me a message from the channel.')
            return
        elif chat.id in [channel['id'] for channel in self.get_channels_of_user(user)]:
            update.message.reply_text('You have already added this channel.')
            return

        permission = self.get_permission(bot, chat)

        if not permission.is_admin:
            update.message.reply_text('I need to be an administrator in the channel.')
            return

        self.add_channel(chat, user)
        update.message.reply_text('Channel was added.')
        self.set_user_state(user, self.states.IDLE)

    def add_channel(self, chat: Chat, user: User):
        """Add the necessary data of a channel so that the user can work with it

        Args:
            chat (:obj:`telegram.chat.Chat`): Telegram Api Chat Object representing the channel
            user (:obj:`telegram.user.User`): Telegram Api User Object
        """
        database.upsert_chat(chat)

        admin_data = {
            'user_id': user.id,
            'channel_id': chat.id
        }
        self.channel_admin.update(admin_data, admin_data, upsert=True)

    # Remove Channel
    def remove_channel_start(self, bot: Bot, update: Update, *args, **kwargs):
        user = update.effective_user
        message = update.effective_message

        channels = list(self.get_channels_of_user(user))
        if not channels:
            message.reply_text('You do not have any channels configured use /addchannel to add one.')
            return

        buttons = [
            [
                MagicButton(text=f'@{channel["username"]}',
                            data=channel['id'],
                            callback=self.remove_channel_from_callback_query,
                            yes_no=True,
                            no_callback=self.remove_channel_start)
                for channel in channels[index:index + 2]
            ]
            for index in range(0, len(channels), 2)
        ]
        buttons.append([
            MagicButton(text='Cancel', callback=self.custom_echo_callback_query,
                        callback_kwargs={
                            'text': 'Removing channel was cancelled',
                            'callback': self.reset_state,
                            'send_telegram_data': True,
                        })
        ])
        real_buttons = MagicButton.conver_buttons(buttons)

        reply = 'Which of these channels do you want to remove.'
        message.reply_text(text=reply, reply_markup=real_buttons)
        self.set_user_state(user, self.states.REMOVING_CHANNEL)

    def remove_channel_from_callback_query(self, bot: Bot, update: Update, data: str, *args, **kwargs):
        user = update.effective_user
        if self.get_user_state(user) != self.states.REMOVING_CHANNEL:
            update.effective_message.reply_text('Your remove request was cancelled due to starting another action')
            return
        self.remove_channel(user=user, chat=data)
        self.set_user_state(user, self.states.IDLE)
        update.effective_message.reply_text('Channel was removed')

    def remove_channel(self, user: User or int, chat: Chat or int):
        chat_id = self.get_chat_id(chat)
        user_id = self.get_user_id(user)
        self.channel_admin.delete_one({'channel_id': chat_id, 'user_id': user_id})

    # List Channels
    def list_channels(self, bot: Bot, update: Update, *args, **kwargs):
        user, message = update.effective_user, update.effective_message
        self.set_user_state(user, self.states.IDLE)

        channels = list(self.get_channels_of_user(user))
        if not channels:
            message.reply_text('You do not have any channels configured use /addchannel to add one.')
            return

        buttons = [
            [
                MagicButton(text=f'@{channel["username"]}',
                            data={'chat_id': channel['id']},
                            callback=self.channel_actions)
                for channel in channels[index:index + 2]
            ]
            for index in range(0, len(channels), 2)
        ]

        real_buttons = MagicButton.conver_buttons(buttons)

        message.reply_text(text='What do you want to do?', reply_markup=real_buttons)

    def channel_actions(self, bot: Bot, update: Update, data: Dict, *args, **kwargs):
        user, message = update.effective_user, update.effective_message
        self.set_user_state(user, self.states.CHANNEL_ACTIONS)

        buttons = [
            [
                MagicButton('Remove', self.remove_channel_from_callback_query, data=data, yes_no=True,
                            no_callback=self.channel_actions)
            ],
            [
                MagicButton('Cancel', self.list_channels)
            ]
        ]

        message.reply_text(text='What do you want to do?', reply_markup=MagicButton.conver_buttons(buttons))

    # Settings
    # Single Post
    # Multi Post


channel = Channel()
