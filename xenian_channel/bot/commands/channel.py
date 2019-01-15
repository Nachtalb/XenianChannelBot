import logging
from collections import namedtuple
from functools import wraps
from time import sleep
from typing import Callable, Dict, Generator
from warnings import warn

import emoji
from telegram import Bot, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, User
from telegram.error import BadRequest, TimedOut
from telegram.ext import CallbackQueryHandler, Job, MessageHandler, run_async
from telegram.parsemode import ParseMode

from xenian_channel.bot import job_queue, mongodb_database
from xenian_channel.bot.commands import database
from xenian_channel.bot.settings import ADMINS, LOG_LEVEL
from xenian_channel.bot.utils import TelegramProgressBar, get_self, get_user_chat_link
from xenian_channel.bot.utils.magic_buttons import MagicButton
from .base import BaseCommand

__all__ = ['channel']

Permission = namedtuple('Permission', ['is_admin', 'post', 'delete', 'edit'])


def locked(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        update = next(iter([arg for arg in args if isinstance(arg, Update)]), None)
        if not update:
            update = kwargs.get('update')
        if not isinstance(update, Update):
            return

        user = update.effective_user
        if self.get_user_state(user) == self.states.SEND_LOCKED:
            update.effective_message.reply_text('Sending currently in progress, please stand by.')
            return

        return func(self, *args, **kwargs)

    return wrapper


class JobsQueue:
    all_jobs = []

    class types:
        SEND_BUTTON_MESSAGE = 'send_button_message'

    def __init__(self, user_id: int, job: Job, type: str, replaceable: bool = True):
        self.user_id = user_id
        self.job = job
        self.type = type
        self.replaceable = replaceable
        JobsQueue.all_jobs.append(self)

        self.replace()

    def replace(self):
        if not self.replaceable:
            return

        jobs = [job for job in JobsQueue.all_jobs if
                job.user_id == self.user_id and job.type == self.type and job != self]
        if not jobs:
            return

        for job in jobs:
            JobsQueue.all_jobs.remove(job)
            job.job.schedule_removal()


class Channel(BaseCommand):
    """A set of channel commands
    """

    name = 'Channel Manager'
    group = 'Channel Manager'

    ram_db_button_message_id = {}  # {user_id: Telegram Message Obj}

    class states:
        IDLE = 'idle'
        ADDING_CHANNEL = 'adding channel'
        REMOVING_CHANNEL = 'removing channel'
        CHANNEL_ACTIONS = 'channel actions'
        IN_SETTINGS = 'in settings'
        CHANGE_DEFAULT_CAPTION = 'change default caption'
        CHANGE_DEFAULT_REACTION = 'change default reaction'
        CREATE_SINGLE_POST = 'create single post'
        SEND_LOCKED = 'send_locked'

    def __init__(self):
        self.commands = [
            {'command': self.add_channel_start, 'command_name': 'addchannel', 'description': 'Add a channel'},
            {'command': self.remove_channel_start, 'command_name': 'removechannel', 'description': 'Remove a channel'},
            {'command': self.list_channels, 'command_name': 'list', 'description': 'List all channels'},
            {
                'command': self.invalidate,
                'command_name': 'invalidate',
                'description': 'Invalidate all buttons',
                'hidden': True
            },
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
            {
                'command': self.reaction_button_handler,
                'handler': CallbackQueryHandler,
                'options': {
                    'pattern': '^reaction_button:.*',
                },
            },
            {
                'command': self.message_handler,
                'description': 'Chooses the right thing to do with a message',
                'handler': MessageHandler,
                'hidden': True,
            },
        ]

        self.channel_admin = mongodb_database.channel_admin  # {user_id: Users ID, chat_id: Channels ID}
        self.channel_settings = mongodb_database.channel_settings  # {id: Channels ID, caption: Default caption, reactions: Default reaction}
        self.channel_file = mongodb_database.channel_file  # {chat_id: Channels ID, file_id: Files ID}
        self.user_state = mongodb_database.user_state  # {user_id: Users ID, state: State ID}
        self.queue = mongodb_database.queue  # {user_id: Users ID, chat_id: Channels ID, message: Message Object, scheduled: DateTime, preview: bool}
        self.files = mongodb_database.files  # {file: Telegram File Object, hash: generated hash value}

        super(Channel, self).__init__()

    def get_chat_username(self, chat: Chat or int, as_link: bool = False) -> str or None:
        chat_id = self.get_chat_id(chat)
        found_chat = database.chats.find_one({'id': chat_id})
        return get_user_chat_link(found_chat, as_link=as_link)

    def get_messages_from_queue(self, bot: Bot, user: User or int, chat: Chat or int, **query) -> Generator[
        Dict, None, None]:
        user_id, chat_id = self.get_user_id(user), self.get_chat_id(chat)
        search_query = {'chat_id': chat_id, 'user_id': user_id}
        search_query.update(query)

        messages = self.queue.find(search_query)
        for message in messages:
            result = message.copy()
            result['message'] = Message.de_json(message['message'], bot=bot)
            yield result

    def get_message_from_queue(self, bot: Bot, chat: Chat or int, **query) -> Dict:
        chat_id = self.get_chat_id(chat)
        search_query = {'chat_id': chat_id}
        search_query.update(query)

        message = self.queue.find_one(search_query)
        if message is None:
            return {}
        message['message'] = Message.de_json(message['message'], bot=bot)
        return message

    def delete_messages_from_queue(self, bot: Bot, user: User or int, chat: Chat or int, **query) -> int:
        user_id, chat_id = self.get_user_id(user), self.get_chat_id(chat)
        search_query = {'chat_id': chat_id, 'user_id': user_id}
        search_query.update(query)

        return self.queue.delete_many(search_query).deleted_count

    def add_message_to_queue(self, bot: Bot, user: User or int, chat: Chat or int, message: Message, **query):
        user_id, chat_id = self.get_user_id(user), self.get_chat_id(chat)
        search_query = {'chat_id': chat_id, 'user_id': user_id, 'message.message_id': message.message_id}

        query = query or {}
        query.setdefault('reactions', {})
        query.update({'message': message.to_dict()})

        self.queue.update(search_query, {'$set': query}, upsert=True)

    def create_or_update_button_message(self, update: Update, *args, **kwargs) -> Message:
        user = update.effective_user
        is_button_message = ('reply_markup' in kwargs or any([isinstance(arg, InlineKeyboardMarkup) for arg in args]))

        create = False
        if 'create' in kwargs:
            create = kwargs.pop('create')
            if create and user.id in self.ram_db_button_message_id:
                try:
                    self.ram_db_button_message_id[user.id].delete()
                    del self.ram_db_button_message_id[user.id]
                except (BadRequest, KeyError):
                    pass

        message = None
        if not create and user.id in self.ram_db_button_message_id and is_button_message:
            try:
                message = self.ram_db_button_message_id[user.id].edit_text(*args, **kwargs)
            except BadRequest:
                pass

        if not message:
            message = update.effective_message.reply_text(*args, **kwargs)

        if is_button_message:
            self.ram_db_button_message_id[user.id] = message
        return message

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
            yield from database.chats.find({'id': channel['chat_id']})

    def set_user_state(self, user: User or int, state: str, chat: Chat or int = None):
        """Set the current state of a user

        Args:
            user (:obj:`telegram.user.User` | :obj:`int`): The telegram user of which the state shall be set as a
                Telegram User object or the users ID
            state (:obj:`str`): The state the user should be in
            chat (:obj:`telegram.chat.Chat` | :obj:`int`): The current chat being worked on
        """
        data = {
            'user_id': self.get_user_id(user),
            'state': state,
            'chat_id': self.get_chat_id(chat) if chat else None
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
        return self.get_full_state(user)['state']

    def get_current_chat(self, user: User or int) -> int or None:
        """Get the current chat

        Args:
            user (:obj:`telegram.user.User` | :obj:`int`): The telegram user of which the state shall be retrieved as a
                Telegram User object or the users ID

        Returns:
            :obj:`int`: The current chat or None if not available
        """
        return self.get_full_state(user)['chat_id']

    def get_full_state(self, user: User or int) -> Dict:
        """Get the full state

        Args:
            user (:obj:`telegram.user.User` | :obj:`int`): The telegram user of which the state shall be retrieved as a
                Telegram User object or the users ID

        Returns:
            :obj:`Dict`: The full state
        """
        user_id = self.get_user_id(user)
        user_state_entry = self.user_state.find_one({'user_id': user_id})
        return user_state_entry or {'state': self.states.IDLE, 'chat_id': None}

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

    def get_channel_settings(self, user: User or int, chat: Chat or int):
        user_id, chat_id = self.get_user_id(user), self.get_chat_id(chat)
        query = {'user_id': user_id, 'chat_id': chat_id}
        settings = self.channel_settings.find_one(query)

        if not settings:
            self.set_channel_settings(user_id, chat_id)
            settings = self.channel_settings.find_one(query)

        return settings

    def set_channel_settings(self, user: User or int, chat: Chat or int, settings: Dict = None):
        user_id, chat_id = self.get_user_id(user), self.get_chat_id(chat)
        query = {'user_id': user_id, 'chat_id': chat_id}

        settings = settings or {}
        settings.update(query)

        settings.setdefault('caption', '')
        settings.setdefault('reactions', [])
        self.channel_settings.update(query, {'$set': settings}, upsert=True)

    # Miscellaneous
    @locked
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
        elif self.get_user_state(user) == self.states.CHANGE_DEFAULT_CAPTION:
            self.change_default_caption(bot, update)
        elif self.get_user_state(user) == self.states.CHANGE_DEFAULT_REACTION:
            self.change_default_reaction(bot, update)
        elif self.get_user_state(user) == self.states.CREATE_SINGLE_POST:
            self.add_message(bot, update)

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
        user, message = update.effective_user, update.effective_message
        split_text = message.text.split(' ', 1)

        is_admin = f'@{user.username}' in ADMINS
        if len(split_text) > 1 and is_admin:
            username = split_text[1].strip('@')
            user = database.users.find_one({'username': username})
            if not user:
                message.reply_text(f'User @{username} could not be found')
                return

        if self.get_user_state(user) == self.states.SEND_LOCKED and f'@{user.username}' not in ADMINS:
            return
        self.set_user_state(user, self.states.IDLE)

    @locked
    @run_async
    def invalidate(self, bot: Bot, update: Update, *args, **kwargs):
        """Invalidate all open buttons

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            update (:obj:`telegram.update.Update`): Telegram Api Update Object
        """
        user, message = update.effective_user, update.effective_message
        split_text = message.text.split(' ', 1)
        if len(split_text) > 1 and f'@{user.username}' in ADMINS:
            username = split_text[1].strip('@')
            user = database.users.find_one({'username': username})
            if not user:
                message.reply_text(f'User @{username} could not be found')
                return

        if user['id'] in self.ram_db_button_message_id:
            del self.ram_db_button_message_id[user['id']]

        MagicButton.invalidate_by_user_id(user['id'])
        update.effective_message.reply_text('Invalidated all buttons')

    @locked
    @run_async
    def custom_echo_callback_query(self, bot: Bot, update: Update, text: str, callback: Callable,
                                   send_telegram_data: bool = False, *args, **kwargs):
        """Echo something to the user after the given callback is run

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

    def set_state_and_run(self, user: User or int, state: str, callback: Callable, *args, **kwargs) -> Callable:
        """Set the state and run the given function with bot, update, args and kwargs

        Args:
            user (:obj:`telegram.user.User` or :obj:`int`): Telegram Api User Object or user id
            state (:obj:`str`): State to which the user shall changed to
            callback (:obj:`Callable`): Callable to run before sending user the text

        Returns:
            :obj:`Callable`: Function which can be executes the given actions
        """

        @locked
        @run_async
        def wrapper(*wargs, **wkwargs):
            self.set_user_state(user, state)
            callback(*wargs, **wkwargs)

        return wrapper

    def get_correct_send_message(self, bot: Bot, message_entry: Dict):
        message = message_entry['message']

        method = bot.send_message
        include_kwargs = {'text': message.text}

        if message.photo:
            method = bot.send_photo
            include_kwargs = {'photo': message.photo[-1], 'caption': message.caption}
        elif message.animation:
            method = bot.send_animation
            include_kwargs = {
                'animation': message.animation,
                'caption': message.caption,
                'duration': message.animation.duration,
                'width': message.animation.width,
                'height': message.animation.height,
                'thumb': message.animation.thumb.file_id,
            }
        elif message.sticker:
            method = bot.send_sticker
            include_kwargs = {
                'sticker': message.sticker,
            }
        elif message.audio:
            method = bot.send_audio
            include_kwargs = {
                'audio': message.audio,
                'caption': message.caption,
                'duration': message.audio.duration,
                'performer': message.audio.performer,
                'title': message.audio.title,
                'thumb': message.audio.thumb.file_id,
            }
        elif message.document:
            method = bot.send_document
            include_kwargs = {
                'document': message.document,
                'caption': message.caption,
                'filename': message.document.file_name,
                'thumb': message.document.thumb.file_id,
            }
        elif message.video:
            method = bot.send_video
            include_kwargs = {
                'video': message.video,
                'caption': message.caption,
                'duration': message.video.duration,
                'width': message.video.width,
                'height': message.video.height,
                'supports_streaming': True,
                'thumb': message.video.thumb.file_id,
            }
        elif message.video_note:
            method = bot.send_video_note
            include_kwargs = {
                'video_note': message.video_note,
                'duration': message.video_note.duration,
                'length': message.video_note.length,
                'thumb': message.video_note.thumb.file_id,
            }
        elif message.voice:
            method = bot.send_voice
            include_kwargs = {
                'voice': message.voice,
                'duration': message.voice.duration,
                'caption': message.caption,
            }

        try:
            return method, include_kwargs
        except Exception as e:
            print(e)
            pass

    # Adding Channels
    @locked
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

    @locked
    @run_async
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
            'chat_id': chat.id
        }
        self.channel_admin.update(admin_data, admin_data, upsert=True)

    # Remove Channel
    @locked
    @run_async
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
                            user=user,
                            data={'chat_id': channel['id']},
                            callback=self.remove_channel_from_callback_query,
                            yes_no=True,
                            no_callback=self.remove_channel_start)
                for channel in channels[index:index + 2]
            ]
            for index in range(0, len(channels), 2)
        ]
        buttons.append([
            MagicButton(text='Cancel', callback=self.custom_echo_callback_query,
                        user=user,
                        callback_kwargs={
                            'text': 'Removing channel was cancelled',
                            'callback': self.reset_state,
                            'send_telegram_data': True,
                        })
        ])
        real_buttons = MagicButton.conver_buttons(buttons)

        reply = 'Which of these channels do you want to remove.'
        self.create_or_update_button_message(update, text=reply, reply_markup=real_buttons)
        self.set_user_state(user, self.states.REMOVING_CHANNEL)

    @locked
    @run_async
    def remove_channel_from_callback_query(self, bot: Bot, update: Update, data: str, *args, **kwargs):
        user = update.effective_user
        if self.get_user_state(user) != self.states.REMOVING_CHANNEL:
            update.effective_message.reply_text('Your remove request was cancelled due to starting another action')
            return

        self.set_user_state(user, self.states.IDLE)
        if not self.remove_channel(user=user, chat=data):
            update.effective_message.reply_text('An error occurred please try again')
            return

        update.effective_message.reply_text('Channel was removed')

    def remove_channel(self, user: User or int, chat: Chat or int):
        chat_id = self.get_chat_id(chat)
        user_id = self.get_user_id(user)
        return self.channel_admin.delete_one({'chat_id': chat_id, 'user_id': user_id}).deleted_count

    # List Channels
    @locked
    @run_async
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
                            user=user,
                            data={'chat_id': channel['id']},
                            callback=self.channel_actions)
                for channel in channels[index:index + 2]
            ]
            for index in range(0, len(channels), 2)
        ]

        real_buttons = MagicButton.conver_buttons(buttons)

        self.create_or_update_button_message(update, text='What do you want to do?', reply_markup=real_buttons,
                                             create=True)

    @locked
    @run_async
    def channel_actions(self, bot: Bot, update: Update, data: Dict, *args, **kwargs):
        user, message = update.effective_user, update.effective_message
        self.set_user_state(user, self.states.CHANNEL_ACTIONS, data)
        buttons = [
            [
                MagicButton('Create Post',
                            callback=self.create_post_callback_query,
                            user=user,
                            data=data)
            ],
            [
                MagicButton('Remove',
                            callback=self.set_state_and_run(user, self.states.REMOVING_CHANNEL,
                                                            self.remove_channel_from_callback_query),
                            user=user,
                            data=data,
                            yes_no=True,
                            no_callback=self.channel_actions),
                MagicButton('Settings',
                            user=user,
                            callback=self.settings_start,
                            data=data)
            ],
            [
                MagicButton('Cancel', user=user, callback=self.list_channels)
            ]
        ]

        chat_name = self.get_chat_username(data)
        self.create_or_update_button_message(update, text=f'Channel: {chat_name}\nWhat do you want to do?',
                                             reply_markup=MagicButton.conver_buttons(buttons))

    # Settings
    @locked
    @run_async
    def settings_start(self, bot: Bot, update: Update, data: Dict, *args, **kwargs):
        user, message = update.effective_user, update.effective_message
        self.set_user_state(user, self.states.IN_SETTINGS, data)

        buttons = [
            [
                MagicButton(text='Change default caption',
                            user=user,
                            data=data,
                            callback=self.change_caption_callback_query)
            ],
            [
                MagicButton(text='Change default reactions',
                            user=user,
                            data=data,
                            callback=self.change_reaction_callback_query)
            ],
            [
                MagicButton('Back',
                            user=user,
                            callback=self.channel_actions,
                            data=data)
            ]
        ]
        chat_name = self.get_chat_username(data)
        self.create_or_update_button_message(update, text=f'Channel: {chat_name}\nWhat do you want to do?',
                                             reply_markup=MagicButton.conver_buttons(buttons))

    @locked
    @run_async
    def change_caption_callback_query(self, bot: Bot, update: Update, data: Dict, *args, **kwargs):
        user, message = update.effective_user, update.effective_message

        setting = self.get_channel_settings(user, data)
        chat_name = self.get_chat_username(data)
        self.create_or_update_button_message(
            update,
            f'Channel: {chat_name}\nYour default caption at the moment is:\n{setting["caption"] or "Empty"}',
            reply_markup=MagicButton.conver_buttons([[
                MagicButton('Finished', callback=self.settings_start, data=data, user=user)
            ]]))
        self.set_user_state(user, self.states.CHANGE_DEFAULT_CAPTION, chat=data)

    @locked
    @run_async
    def change_reaction_callback_query(self, bot: Bot, update: Update, data: Dict, *args, **kwargs):
        user, message = update.effective_user, update.effective_message

        setting = self.get_channel_settings(user, data)
        chat_name = self.get_chat_username(data)

        reactions = setting['reactions']
        buttons = [
            [
                MagicButton(text=reaction,
                            user=user,
                            callback=lambda *_, **__: None)
                for reaction in reactions[index:index + 4]
            ]
            for index in range(0, len(reactions), 4)
        ]
        buttons.insert(0, [
            MagicButton('Finished', callback=self.settings_start, data=data, user=user)
        ])

        self.create_or_update_button_message(
            update,
            f'Channel: {chat_name}\nYour default reactions at the moment are\n{"" if setting["reactions"] else "None"}',
            reply_markup=MagicButton.conver_buttons(buttons))
        self.set_user_state(user, self.states.CHANGE_DEFAULT_REACTION, chat=data)

    @locked
    @run_async
    def change_default_caption(self, bot: Bot, update: Update):
        user, message = update.effective_user, update.effective_message
        if not message.text:
            message.reply_text('You have to send me some text or hit cancel.')
            return

        current_chat = self.get_current_chat(user)
        if not current_chat:
            message.reply_text('An error occurred please hit cancel and try again')
            return

        self.set_channel_settings(user, current_chat, {'caption': message.text})
        self.change_caption_callback_query(bot=bot, update=update, data={'chat_id': current_chat})

    @locked
    @run_async
    def change_default_reaction(self, bot: Bot, update: Update):
        user, message = update.effective_user, update.effective_message
        emojis = emoji.emoji_lis(message.text)
        reactions = [reaction['emoji'] for reaction in emojis]

        if not message.text or not emojis:
            message.reply_text('You have to send me some some reactions (Emoji).')
            return

        current_chat = self.get_current_chat(user)
        if not current_chat:
            message.reply_text('An error occurred please hit cancel and try again')
            return

        self.set_channel_settings(user, current_chat, {'reactions': reactions})
        self.change_reaction_callback_query(bot=bot, update=update, data={'chat_id': current_chat})

    # Single Post
    @locked
    @run_async
    def create_post_callback_query(self, bot: Bot, update: Update, data: Dict, recreate_message: bool = False, *args,
                                   **kwargs):
        user, message = update.effective_user, update.effective_message
        self.set_user_state(user, self.states.CREATE_SINGLE_POST, data)

        buttons = [
            [
                MagicButton('Preview', user=user, callback=self.send_post_callback_query, data=data,
                            callback_kwargs={'preview': True}),
                MagicButton('Clear Queue', user=user, callback=self.clear_queue_callback_query, data=data,
                            yes_no=True, no_callback=self.create_post_callback_query)
            ],
            [
                MagicButton('Send', user=user, callback=self.send_post_callback_query, data=data,
                            yes_no=True, no_callback=self.create_post_callback_query)

            ],
            [
                MagicButton('Cancel', user=user, callback=self.channel_actions, data=data)
            ]
        ]

        in_store = list(self.get_messages_from_queue(bot=bot, user=user, chat=data, preview=True))
        chat_name = self.get_chat_username(data)
        self.create_or_update_button_message(
            update, text=f'Channel: {chat_name}\nSend me what should be sent to the channel: {len(in_store)} in queue',
            reply_markup=MagicButton.conver_buttons(buttons), create=recreate_message)

    @locked
    @run_async
    def add_message(self, bot: Bot, update: Update, *args, **kwargs):
        user, message = update.effective_user, update.effective_message
        chat_id = self.get_current_chat(user)

        settings = self.get_channel_settings(user, chat_id)

        if not (message.text or message.photo or message.video or message.audio or message.voice or message.document or
                message.animation or message.sticker or message.video_note):
            message.reply_text('This type of message is not supported.', reply_message_id=message.message_id)
            return

        self.add_message_to_queue(bot=bot, user=user, chat=chat_id, message=message, preview=True,
                                  reactions=dict((reaction, []) for reaction in settings['reactions']))
        message.reply_text('Message was added sent the next one.', reply_message_id=message.message_id)

        job = job_queue.run_once(
            lambda bot_, _job, **__: self.create_post_callback_query(
                bot_, update, data={'chat_id': chat_id}, recreate_message=True, *args, **kwargs),
            when=1
        )
        JobsQueue(user_id=user.id, job=job, type=JobsQueue.types.SEND_BUTTON_MESSAGE, replaceable=True)

    @locked
    @run_async
    def send_post_callback_query(self, bot: Bot, update: Update, data: Dict, *args, **kwargs):
        user, message = update.effective_user, update.effective_message
        chat_id = self.get_chat_id(data)
        self.set_user_state(user, self.states.CREATE_SINGLE_POST, data)

        settings = self.get_channel_settings(user, chat_id)
        caption = settings['caption']
        reactions = dict((reaction, []) for reaction in settings['reactions'])
        preview = kwargs.get('preview', False)

        send_to = message.chat_id if preview else chat_id

        messages = list(self.get_messages_from_queue(bot=bot, user=user, chat=chat_id, preview=True))
        not_sent = []

        progress_bar = TelegramProgressBar(
            bot=message.bot,
            chat_id=message.chat_id,
            pre_message='Sending images ' + ('as preview' if preview else 'to chat') + ' [{current}/{total}]',
            se_message='This could take some time.',
            step_size=2
        )
        self.set_user_state(user, self.states.SEND_LOCKED, chat=chat_id)
        for index, stored_message in progress_bar.enumerate(messages):
            stored_message['message'].caption = caption
            if stored_message.get('reactions') != reactions:
                stored_message['reactions'] = reactions

            method, include_kwargs = self.get_correct_send_message(bot=message.bot, message_entry=stored_message)

            try:
                buttons = []
                if preview:
                    buttons.extend([[
                        MagicButton('Delete', user, callback=self.remove_from_queue_callback_query,
                                    data=stored_message).convert()
                    ]])

                buttons.extend(self.get_reaction_buttons(reactions=reactions, with_callback=not preview))

                include_kwargs['reply_markup'] = MagicButton.conver_buttons(buttons)

                sleep(0.3)
                new_message = method(chat_id=send_to, **include_kwargs)
                self.add_message_to_queue(bot=bot, user=user, chat=chat_id,
                                          message=stored_message['message'] if preview else new_message,
                                          preview=preview,
                                          reactions=reactions)
            except (Exception, BaseException) as e:
                if not isinstance(e, TimedOut):
                    warn(e)
                not_sent.append((method, chat_id, include_kwargs))
        self.set_user_state(user, self.states.CREATE_SINGLE_POST, chat=chat_id)

        if not_sent:
            message.reply_text(f'{len(not_sent)} could not be sent yet, currently the server is the bottleneck')
        self.create_post_callback_query(bot, update, data, recreate_message=True, *args, **kwargs)

    def get_reaction_buttons(self, reactions: Dict, with_callback=False):
        return [
            [
                InlineKeyboardButton(text=f'{reaction} {len(reactions[reaction]) if reactions[reaction] else ""}',
                                     callback_data=f'reaction_button:{reaction}' if with_callback else 'nothing')
                for reaction in list(reactions)[index:index + 4]
            ]
            for index in range(0, len(reactions), 4)
        ]

    def reaction_button_handler(self, bot: Bot, update: Update):
        user, message, chat = update.effective_user, update.effective_message, update.effective_chat
        user_id, chat_id = self.get_user_id(user), self.get_chat_id(chat)
        reaction = update.callback_query.data.replace('reaction_button:', '')

        message_dict = self.get_message_from_queue(bot, chat=chat, **{'message.message_id': message.message_id})
        reactions = message_dict['reactions']

        if reaction not in reactions or user_id in reactions[reaction]:
            update.callback_query.answer()
            return

        for available_reaction in reactions:
            if user_id in reactions[available_reaction]:
                reactions[available_reaction].remove(user_id)
        reactions[reaction].append(user_id)

        self.queue.update({'chat_id': chat_id, 'message.message_id': message.message_id},
                          {'$set': {'reactions': reactions}})

        buttons = InlineKeyboardMarkup(self.get_reaction_buttons(reactions, with_callback=True))
        message.edit_reply_markup(reply_markup=buttons)
        update.callback_query.answer(emoji.emojize('Thanks for voting :thumbs_up:'))

    @locked
    @run_async
    def clear_queue_callback_query(self, bot: Bot, update: Update, data: Dict, *args, **kwargs):
        user, message = update.effective_user, update.effective_message
        chat_id = self.get_chat_id(data)
        self.set_user_state(user, self.states.CREATE_SINGLE_POST, data)

        deleted_count = self.delete_messages_from_queue(bot=bot, user=user, chat=chat_id, preview=True)
        message.reply_text(text=f'{deleted_count} removed.')

        self.create_post_callback_query(bot, update, data, recreate_message=True, *args, **kwargs)

    @run_async
    def remove_from_queue_callback_query(self, bot: Bot, update: Update, data: Dict, *args, **kwargs):
        user, message = update.effective_user, update.effective_message
        user_id, chat_id = self.get_user_id(user), self.get_current_chat(user)

        query = dict(bot=bot, user_id=user_id, chat=chat_id, **{'message.message_id': data['message'].message_id})
        old = self.get_message_from_queue(**query)

        if not old.get('preview'):
            message.edit_reply_markup()
        else:
            self.delete_messages_from_queue(**query)
            message.delete()

        self.create_post_callback_query(bot, update, {'chat_id': chat_id}, *args, **kwargs)
    # Multi Post


channel = Channel()
