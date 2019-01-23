import logging
from collections import namedtuple
from time import sleep
from typing import Callable, Dict
from warnings import warn

import emoji
from telegram import Bot, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, User
from telegram.error import BadRequest, TimedOut
from telegram.ext import CallbackQueryHandler, Job, MessageHandler, run_async
from telegram.parsemode import ParseMode

from xenian_channel.bot import job_queue
from xenian_channel.bot.models import ChannelSettings, TgChat, TgMessage, TgUser, UserState
from xenian_channel.bot.settings import ADMINS, LOG_LEVEL
from xenian_channel.bot.utils import TelegramProgressBar, get_self
from xenian_channel.bot.utils.magic_buttons import MagicButton
from .base import BaseCommand

__all__ = ['channel']

Permission = namedtuple('Permission', ['is_admin', 'post', 'delete', 'edit'])


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


class ChannelManager(BaseCommand):
    """A set of channel commands
    """

    name = 'Channel Manager'
    group = 'Channel Manager'

    ram_db_button_message_id = {}  # {user_id: Telegram Message Obj}

    def __init__(self):
        self.commands = [
            {'command': self.add_channel_start, 'command_name': 'addchannel', 'description': 'Add a channel'},
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

        self.tg_user = None
        self.tg_chat = None
        self.tg_message = None
        self.tg_state = None
        self._tg_current_channel = None

        super(ChannelManager, self).__init__()

    def on_call(self, bot: Bot, update: Update):
        super(ChannelManager, self).on_call(bot, update)
        self._tg_current_channel = None
        self.tg_state = None

        if self.user:
            data = dict(user=self.tg_user)
            self.tg_state = next(iter(UserState.objects(**data)), UserState(**data))
            if self.tg_state.current_channel:
                self.tg_state.current_channel._bot = self.bot

    @property
    def tg_current_channel(self):
        if self._tg_current_channel is None:
            self._tg_current_channel = self.tg_state.current_channel

        return self._tg_current_channel

    @tg_current_channel.setter
    def tg_current_channel(self, channel: ChannelSettings or None):
        self._tg_current_channel = channel
        self._tg_current_channel = channel
        self.tg_state.current_channel = self._tg_current_channel
        self.tg_state.cascade_save()
        self.tg_state.save()

    def get_username_or_link(self, chat: User or Chat or TgChat or TgUser or ChannelSettings):
        real_chat = chat
        if isinstance(chat, ChannelSettings):
            real_chat = chat.chat
        elif isinstance(chat, TgChat) or isinstance(chat, TgUser):
            real_chat = chat.to_object(self.bot)

        if hasattr(real_chat, 'name'):
            return real_chat.name
        elif real_chat.username:
            return f'@{real_chat.username}'
        else:
            return real_chat.link

    def create_or_update_button_message(self, *args, **kwargs) -> Message:
        is_button_message = ('reply_markup' in kwargs or any([isinstance(arg, InlineKeyboardMarkup) for arg in args]))

        create = False
        if 'create' in kwargs:
            create = kwargs.pop('create')
            if create and self.user.id in self.ram_db_button_message_id:
                try:
                    self.ram_db_button_message_id[self.user.id].delete()
                    del self.ram_db_button_message_id[self.user.id]
                except (BadRequest, KeyError):
                    pass

        message = None
        if not create and self.user.id in self.ram_db_button_message_id and is_button_message:
            try:
                message = self.ram_db_button_message_id[self.user.id].edit_text(*args, **kwargs)
            except BadRequest:
                pass

        if not message:
            message = self.message.reply_text(*args, **kwargs)

        if is_button_message:
            self.ram_db_button_message_id[self.user.id] = message
        return message

    def get_permission(self, chat: Chat):
        """Get usual permissions of bot from chat

        Args:
            chat (:obj:`telegram.chat.Chat`): Telegram Api Chat Object

        Returns:
            :obj:`Permission`: The channels Permission object
        """
        myself = get_self(self.bot)
        chat_member = self.bot.get_chat_member(chat.id, myself.id)

        return Permission(
            is_admin=chat_member.status == chat_member.ADMINISTRATOR,
            post=chat_member.can_post_messages,
            delete=chat_member.can_delete_messages,
            edit=chat_member.can_edit_messages,
        )

    # Miscellaneous
    @run_async
    def message_handler(self, *args, **kwargs):
        """Dispatch messages to correct function, defied by the users state
        """
        if self.update.channel_post:
            return

        if self.tg_state.state == self.tg_state.ADDING_CHANNEL:
            self.add_channel_from_message()
        elif self.tg_state.state == self.tg_state.CHANGE_DEFAULT_CAPTION:
            self.change_default_caption()
        elif self.tg_state.state == self.tg_state.CHANGE_DEFAULT_REACTION:
            self.change_default_reaction()
        elif self.tg_state.state == self.tg_state.CREATE_SINGLE_POST:
            self.add_message()

    @run_async
    def echo_state(self, *args, **kwargs):
        """Debug method to send the users his state
        """
        self.message.reply_text(f'{self.tg_state.state}')

    @run_async
    def reset_state(self, *args, **kwargs):
        """Debug method to send the users his state
        """
        split_text = self.message.text.split(' ', 1)

        is_admin = f'@{self.user.username}' in ADMINS
        if len(split_text) > 1 and is_admin:
            username = split_text[1].strip('@')
            user = TgUser.objects(username=username).first()
            if not user:
                self.message.reply_text(f'User @{username} could not be found')
                return

        if self.tg_state.state == self.tg_state.SEND_LOCKED and f'@{self.user.username}' not in ADMINS:
            return
        self.tg_state.state = self.tg_state.IDLE
        self.list_channels()

    @run_async
    def invalidate(self, *args, **kwargs):
        """Invalidate all open buttons
        """
        split_text = self.message.text.split(' ', 1)
        if len(split_text) > 1 and f'@{self.user.username}' in ADMINS:
            username = split_text[1].strip('@')
            user = TgUser.objects(username=username)
            if not user:
                self.message.reply_text(f'User @{username} could not be found')
                return

        if self.user.id in self.ram_db_button_message_id:
            del self.ram_db_button_message_id[self.user.id]

        MagicButton.invalidate_by_user_id(self.user.id)
        self.message.reply_text('Invalidated all buttons')

    def set_state_and_run(self, state: str, callback: Callable, *args, **kwargs) -> Callable:
        """Set the state and run the given function with bot, update, argxwxxxxxs and kwargs

        Args:
            state (:obj:`str`): State to which the user shall changed to
            callback (:obj:`Callable`): Callable to run before sending user the text

        Returns:
            :obj:`Callable`: Function which can be executes the given actions
        """

        @run_async
        def wrapper(*wargs, **wkwargs):
            self.tg_state.state = state
            callback(*wargs, **wkwargs)

        return wrapper

    def get_correct_send_message(self, message: Message):
        method = self.bot.send_message
        include_kwargs = {'text': message.text}

        if message.photo:
            method = self.bot.send_photo
            include_kwargs = {'photo': message.photo[-1], 'caption': message.caption}
        elif message.animation:
            method = self.bot.send_animation
            include_kwargs = {
                'animation': message.animation,
                'caption': message.caption,
                'duration': message.animation.duration,
                'width': message.animation.width,
                'height': message.animation.height,
                'thumb': message.animation.thumb.file_id if message.animation.thumb else None,
            }
        elif message.sticker:
            method = self.bot.send_sticker
            include_kwargs = {
                'sticker': message.sticker,
            }
        elif message.audio:
            method = self.bot.send_audio
            include_kwargs = {
                'audio': message.audio,
                'caption': message.caption,
                'duration': message.audio.duration,
                'performer': message.audio.performer,
                'title': message.audio.title,
                'thumb': message.audio.thumb.file_id if message.audio.thumb else None,
            }
        elif message.document:
            method = self.bot.send_document
            include_kwargs = {
                'document': message.document,
                'caption': message.caption,
                'filename': message.document.file_name,
                'thumb': message.document.thumb.file_id if message.document.thumb else None,
            }
        elif message.video:
            method = self.bot.send_video
            include_kwargs = {
                'video': message.video,
                'caption': message.caption,
                'duration': message.video.duration,
                'width': message.video.width,
                'height': message.video.height,
                'supports_streaming': True,
                'thumb': message.video.thumb.file_id if message.video.thumb else None,
            }
        elif message.video_note:
            method = self.bot.send_video_note
            include_kwargs = {
                'video_note': message.video_note,
                'duration': message.video_note.duration,
                'length': message.video_note.length,
                'thumb': message.video_note.thumb.file_id if message.video_note.thumb else None,
            }
        elif message.voice:
            method = self.bot.send_voice
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
    @run_async
    def add_channel_start(self, *args, **kwargs):
        """Add a channel to your channels
        """
        add_to_channel_instruction = (
            "*Adding a channel*"
            "\n"
            "\nTo add a channel follow these instructions"
            "\n"
            "\n1. Make sure @XenianChannelBot is and admin of your channel"
            "\n2. Forward me any message from that channel"
        )
        self.message.reply_text(text=add_to_channel_instruction, parse_mode=ParseMode.MARKDOWN)
        self.tg_state.state = self.tg_state.ADDING_CHANNEL

    @run_async
    def add_channel_from_message(self, *args, **kwargs):
        """Add a channel to your channels
        """
        channel_chat = self.message.forward_from_chat
        tg_channel_chat = next(iter(TgChat.objects(id=channel_chat.id)), TgChat(channel_chat))

        query = {
            'user': self.tg_user,
            'chat': tg_channel_chat
        }
        if not channel_chat:
            self.message.reply_text('You have to send me a message from the channel.')
            return
        elif ChannelSettings.objects(**query):
            self.message.reply_text('You have already added this channel.')
            return

        permission = self.get_permission(channel_chat)

        if not permission.is_admin:
            self.message.reply_text('I need to be an administrator in the channel.')
            return

        self.tg_current_channel = ChannelSettings(user=self.tg_user, chat=tg_channel_chat)
        self.tg_current_channel.save()
        self.tg_current_channel.cascade_save()

        tg_channel_chat.user = self.tg_user
        tg_channel_chat.save()

        self.message.reply_text('Channel was added.')
        self.tg_state.state = self.tg_state.IDLE
        self.list_channels()

    # Remove Channel
    @run_async
    def remove_channel_from_callback_query(self, *args, **kwargs):
        if self.tg_state.state != self.tg_state.REMOVING_CHANNEL:
            self.message.reply_text('Your remove request was cancelled due to starting another action')
            return

        self.tg_current_channel.delete()

        self.update.effective_message.reply_text('Channel was removed')
        self.list_channels()

    # List Channels
    @run_async
    def list_channels(self, *args, **kwargs):
        self.tg_current_channel = None
        self.tg_state.state = self.tg_state.IDLE

        channels = ChannelSettings.objects(user=self.tg_user)
        if not channels:
            self.message.reply_text('You do not have any channels configured use /addchannel to add one.')
            return

        buttons = [
            [
                MagicButton(text=f'@{channel.chat.username}' if channel.chat.username else channel.chat.titel,
                            user=self.user,
                            data={'channel': channel},
                            callback=self.channel_actions)
                for channel in channels[index:index + 2]
            ]
            for index in range(0, len(channels), 2)
        ]

        real_buttons = MagicButton.conver_buttons(buttons)

        self.create_or_update_button_message(text='What do you want to do?', reply_markup=real_buttons, create=True)

    @run_async
    def channel_actions(self, data: Dict = None, *args, **kwargs):
        if 'channel' in data:
            self.tg_current_channel = data['channel']
        elif self.tg_current_channel is None:
            self.message.reply_text('An error occured, please try again.')
            self.list_channels()
            return
        self.tg_state.state = self.tg_state.CHANNEL_ACTIONS

        buttons = [
            [
                MagicButton('Create Post',
                            callback=self.create_post_callback_query,
                            user=self.user)
            ],
            [
                MagicButton('Remove',
                            callback=self.set_state_and_run(self.tg_state.REMOVING_CHANNEL,
                                                            self.remove_channel_from_callback_query),
                            user=self.user,
                            yes_no=True,
                            no_callback=self.channel_actions),
                MagicButton('Settings',
                            user=self.user,
                            callback=self.settings_start)
            ],
            [
                MagicButton('Cancel', user=self.user, callback=self.list_channels)
            ]
        ]

        chat_name = self.get_username_or_link(self.tg_current_channel)
        self.create_or_update_button_message(text=f'Channel: {chat_name}\nWhat do you want to do?',
                                             reply_markup=MagicButton.conver_buttons(buttons))

    # Settings
    @run_async
    def settings_start(self, *args, **kwargs):
        self.tg_state.state = self.tg_state.IN_SETTINGS

        buttons = [
            [
                MagicButton(text='Change default caption',
                            user=self.user,
                            callback=self.change_caption_callback_query)
            ],
            [
                MagicButton(text='Change default reactions',
                            user=self.user,
                            callback=self.change_reaction_callback_query)
            ],
            [
                MagicButton('Back',
                            user=self.user,
                            callback=self.channel_actions)
            ]
        ]
        chat_name = self.get_username_or_link(self.tg_current_channel)
        self.create_or_update_button_message(text=f'Channel: {chat_name}\nWhat do you want to do?',
                                             reply_markup=MagicButton.conver_buttons(buttons))

    @run_async
    def change_caption_callback_query(self, *args, **kwargs):
        chat_name = self.get_username_or_link(self.tg_current_channel)

        self.create_or_update_button_message(
            f'Channel: {chat_name}\nYour default caption at the moment is:\n{self.tg_current_channel.caption or "Empty"}',
            reply_markup=MagicButton.conver_buttons([[
                MagicButton('Finished', callback=self.settings_start, user=self.user)
            ]]))
        self.tg_state.state = self.tg_state.CHANGE_DEFAULT_CAPTION

    @run_async
    def change_reaction_callback_query(self, *args, **kwargs):
        chat_name = self.get_username_or_link(self.tg_current_channel)

        reactions = self.tg_current_channel.reactions
        buttons = [
            [
                MagicButton(text=reaction,
                            user=self.user,
                            callback=lambda *_, **__: None)
                for reaction in reactions[index:index + 4]
            ]
            for index in range(0, len(reactions), 4)
        ]
        buttons.insert(0, [
            MagicButton('Finished', callback=self.settings_start, user=self.user)
        ])

        self.create_or_update_button_message(
            f'Channel: {chat_name}\nYour default reactions at the moment are\n{"" if reactions else "None"}',
            reply_markup=MagicButton.conver_buttons(buttons))
        self.tg_state.state = self.tg_state.CHANGE_DEFAULT_REACTION

    @run_async
    def change_default_caption(self, *args, **kwargs):
        if not self.message.text:
            self.message.reply_text('You have to send me some text or hit cancel.')
            return

        self.tg_current_channel.caption = self.message.text
        self.tg_current_channel.save()
        self.change_caption_callback_query()

    @run_async
    def change_default_reaction(self, *args, **kwargs):
        emojis = emoji.emoji_lis(self.message.text)
        reactions = [reaction['emoji'] for reaction in emojis]

        if not self.message.text or not emojis:
            self.message.reply_text('You have to send me some some reactions (Emoji).')
            return

        self.tg_current_channel.reactions = reactions
        self.tg_current_channel.save()
        self.change_reaction_callback_query()

    # Single Post
    @run_async
    def create_post_callback_query(self, recreate_message: bool = False, *args, **kwargs):
        self.tg_state.state = self.tg_state.CREATE_SINGLE_POST

        buttons = [
            [
                MagicButton('Preview', user=self.user, callback=self.send_post_callback_query,
                            callback_kwargs={'preview': True}),
                MagicButton('Clear Queue', user=self.user, callback=self.clear_queue_callback_query,
                            yes_no=True, no_callback=self.create_post_callback_query)
            ],
            [
                MagicButton('Send', user=self.user, callback=self.send_post_callback_query,
                            yes_no=True, no_callback=self.create_post_callback_query)

            ],
            [
                MagicButton('Cancel', user=self.user, callback=self.channel_actions)
            ]
        ]

        chat_name = self.get_username_or_link(self.tg_current_channel)
        added_amount = len(self.tg_current_channel.added_messages)
        self.create_or_update_button_message(
            text=f'Channel: {chat_name}\nSend me what should be sent to the channel: {added_amount} in queue',
            reply_markup=MagicButton.conver_buttons(buttons), create=recreate_message)

    @run_async
    def add_message(self, *args, **kwargs):
        if not (self.message.text or self.message.photo or self.message.video or self.message.audio or
                self.message.voice or self.message.document or self.message.animation or self.message.sticker or
                self.message.video_note):
            self.message.reply_text('This type of message is not supported.', reply_message_id=self.message.message_id)
            return

        self.tg_message.save()
        self.tg_current_channel.added_messages.append(self.tg_message)
        self.tg_current_channel.save()

        self.message.reply_text('Message was added sent the next one.', disable_notification=True)

        job = job_queue.run_once(
            lambda bot_, _job, **__: self.create_post_callback_query(recreate_message=True, *args, **kwargs),
            when=1
        )
        JobsQueue(user_id=self.user.id, job=job, type=JobsQueue.types.SEND_BUTTON_MESSAGE, replaceable=True)

    @run_async
    def send_post_callback_query(self, *args, **kwargs):
        preview = kwargs.get('preview', False)

        send_to = self.chat if preview else self.tg_current_channel.chat

        progress_bar = TelegramProgressBar(
            bot=self.bot,
            chat_id=self.chat.id,
            pre_message='Sending images ' + ('as preview' if preview else 'to chat') + ' [{current}/{total}]',
            se_message='This could take some time.',
        )
        self.tg_state.state = self.tg_state.SEND_LOCKED

        for index, stored_message in progress_bar.enumerate(list(self.tg_current_channel.added_messages)):
            try:
                real_message = stored_message.to_object(self.bot)
                method, include_kwargs = self.get_correct_send_message(real_message)

                buttons = []
                reaction_dict = dict((reaction, [])
                                     for reaction in stored_message.reactions or self.tg_current_channel.reactions)

                if preview:
                    buttons.extend([[
                        MagicButton('Delete', self.user, callback=self.remove_from_queue_callback_query,
                                    data={'message_id': stored_message.message_id}).convert()
                    ]])

                buttons.extend(self.get_reaction_buttons(reactions=reaction_dict, with_callback=not preview))

                include_kwargs['reply_markup'] = MagicButton.conver_buttons(buttons)

                if preview:
                    sleep(1 / 29)  # In private chat the flood limit is at 30 messages / second
                else:
                    sleep(1 / 29)  # In private chat the flood limit is at 30 messages / second
                    # sleep(60/19)  # In groups and channels the limit is at 20 messages / minute
                # Use 19 and 29 to ensure that a network errors or so causes to exceed the limit

                new_message = method(chat_id=send_to.id, **include_kwargs)
                if not preview:
                    new_tg_message = TgMessage(new_message, reactions=reaction_dict)
                    new_tg_message.save()

                    self.tg_current_channel.added_messages.remove(stored_message)
                    self.tg_current_channel.sent_messages.append(new_tg_message)
                    self.tg_current_channel.save()
            except TimedOut as e:
                warn(e)
            except (BaseException, Exception) as e:
                self.message.reply_text('An error occurred please contact an admin with /error')
                self.tg_state.state = self.tg_state.CREATE_SINGLE_POST
                self.create_post_callback_query(recreate_message=True, *args, **kwargs)
                raise e
        self.tg_state.state = self.tg_state.CREATE_SINGLE_POST

        self.create_post_callback_query(recreate_message=True, *args, **kwargs)

    def get_reaction_buttons(self, reactions: Dict, with_callback=False):
        return [
            [
                InlineKeyboardButton(text=f'{reaction} {len(reactions[reaction]) if reactions[reaction] else ""}',
                                     callback_data=f'reaction_button:{reaction}' if with_callback else 'nothing')
                for reaction in list(reactions)[index:index + 4]
            ]
            for index in range(0, len(reactions), 4)
        ]

    def reaction_button_handler(self, *args, **kwargs):
        reaction = self.update.callback_query.data.replace('reaction_button:', '')
        message = TgMessage.objects(message_id=self.message.message_id).first()

        if not message or reaction not in message.reactions:
            self.update.callback_query.answer('Something went wrong.')
            return

        if self.tg_user in message.reactions[reaction]:
            self.update.callback_query.answer()
            return

        for available_reaction, users in message.reactions.items():
            if self.tg_user in users:
                message.reactions[available_reaction].remove(self.tg_user)
        message.reactions[reaction].append(self.tg_user)
        message.save()

        buttons = InlineKeyboardMarkup(self.get_reaction_buttons(message.reactions, with_callback=True))
        self.message.edit_reply_markup(reply_markup=buttons)
        self.update.callback_query.answer(emoji.emojize('Thanks for voting :thumbs_up:'))

    @run_async
    def clear_queue_callback_query(self, *args, **kwargs):
        self.tg_current_channel.added_messages = []
        self.tg_current_channel.save()
        self.message.reply_text(text='Queue cleared')

        self.create_post_callback_query(recreate_message=True, *args, **kwargs)

    @run_async
    def remove_from_queue_callback_query(self, bot: Bot, update: Update, data: Dict, *args, **kwargs):
        message = data['message']

        self.message.delete()
        self.tg_current_channel.added_messages.remove(message)
        self.tg_current_channel.save()

        self.create_post_callback_query(bot, update, *args, **kwargs)


channel = ChannelManager()
