import logging
import re
from collections import namedtuple
from datetime import datetime, timedelta
from itertools import chain
from typing import Callable, Dict, Iterable, List, Tuple
from uuid import uuid4

import emoji
import parsedatetime
import pytimeparse
from pytz import timezone
from telegram import Bot, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, User
from telegram.error import BadRequest, TimedOut
from telegram.ext import CallbackQueryHandler, Job, MessageHandler, run_async
from telegram.parsemode import ParseMode

from xenian_channel.bot import job_queue
from xenian_channel.bot.models import Button, ChannelSettings, TgChat, TgMessage, TgUser, UserState
from xenian_channel.bot.settings import ADMINS, LOG_LEVEL
from xenian_channel.bot.utils import TelegramProgressBar, get_self
from xenian_channel.bot.utils.models import resolve_dbref
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

    sent_file_id_cache = {}  # {ChannelSettings obj: [file_id, ...]]}}

    def __init__(self):
        self.commands = [
            {'command': self.add_channel_command, 'command_name': 'addchannel', 'description': 'Add a channel'},
            {'command': self.list_channels_menu, 'command_name': 'list', 'description': 'List all channels'},
            {
                'command': self.echo_state_command,
                'command_name': 'state',
                'description': 'Debug - Show users current state',
                'hidden': not (LOG_LEVEL == logging.DEBUG)
            },
            {
                'command': self.reset_state_command,
                'command_name': 'reset',
                'description': 'Debug - Reset the users current state',
                'hidden': not (LOG_LEVEL == logging.DEBUG)
            },
            {
                'command': self.button_dispatcher,
                'handler': CallbackQueryHandler,
                'options': {
                    'pattern': '^button:.*',
                },
            },
            {
                'command': self.reaction_button_callback_query,
                'handler': CallbackQueryHandler,
                'options': {
                    'pattern': '^reaction_button:.*',
                },
            },
            {
                'command': self.message_handler_dispatcher,
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

        if self.user:
            data = dict(user=self.tg_user)
            self.tg_state = next(iter(UserState.objects(**data)), UserState(**data))
            if self.tg_state.current_channel:
                self.tg_state.current_channel._bot = self.bot

    def start_hook(self, bot: Bot):
        self.load_scheduled()

    @property
    def tg_current_channel(self) -> ChannelSettings:
        return self.tg_state.current_channel

    @tg_current_channel.setter
    def tg_current_channel(self, channel: ChannelSettings or None):
        self.tg_state.current_channel = channel
        self.tg_state.cascade_save()
        self.tg_state.save()

    # # # # # # # # # # # # # # # # # # #
    # START Helper                      #
    # # # # # # # # # # # # # # # # # # #

    def create_or_update_button_message(self, *args, **kwargs) -> Message:
        current_message = TgMessage.objects(chat=self.tg_chat, is_current_message=True).first()

        if not current_message or kwargs.get('create', False):
            new_message = self.message.reply_text(*args, **kwargs).result()
            if current_message:
                try:
                    self.bot.delete_message(chat_id=self.chat.id, message_id=current_message.message_id)
                except BadRequest:
                    pass
        else:
            if 'text' in kwargs:
                text = kwargs.pop('text')
            elif args and isinstance(args[0], str):
                text = args[0]
            else:
                text = current_message.original_object['text']

            new_message = self.bot.edit_message_text(text=text, chat_id=self.chat.id,
                                                     message_id=current_message.message_id, **kwargs)

        if current_message:
            current_message.is_current_message = False
            current_message.save()

        new_tg_message = TgMessage.from_object(new_message)
        new_tg_message.is_current_message = True
        new_tg_message.save()
        return new_tg_message

    def get_username_or_link(self, chat: User or Chat or TgChat or TgUser or ChannelSettings,
                             is_markdown: bool = False):
        real_chat = chat
        if isinstance(chat, ChannelSettings):
            real_chat = chat.chat.to_object(self.bot)
        elif isinstance(chat, TgChat) or isinstance(chat, TgUser):
            real_chat = chat.to_object(self.bot)

        if hasattr(real_chat, 'name'):
            chat_title = real_chat.name
        elif real_chat.username:
            chat_title = f'@{real_chat.username}'
        elif real_chat.title:
            chat_title = real_chat.title
        else:
            chat_title = real_chat.link

        if is_markdown:
            chat_title = re.sub(r'([\\`*_{}\[\]()#+-.!"\'])', r'\\\1', chat_title)
            chat_title = chat_title.replace('<', '&lt;').replace('>', '&gt;').replace('$', '&amp;')
            return chat_title
        else:
            return chat_title

    def get_channel_permissions_for_bot(self, chat: Chat):
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

    def get_correct_send_message(self, message: Message, bot: Bot = None):
        bot = bot or self.bot
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
                'thumb': message.animation.thumb.file_id if message.animation.thumb else None,
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
                'thumb': message.audio.thumb.file_id if message.audio.thumb else None,
            }
        elif message.document:
            method = bot.send_document
            include_kwargs = {
                'document': message.document,
                'caption': message.caption,
                'filename': message.document.file_name,
                'thumb': message.document.thumb.file_id if message.document.thumb else None,
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
                'thumb': message.video.thumb.file_id if message.video.thumb else None,
            }
        elif message.video_note:
            method = bot.send_video_note
            include_kwargs = {
                'video_note': message.video_note,
                'duration': message.video_note.duration,
                'length': message.video_note.length,
                'thumb': message.video_note.thumb.file_id if message.video_note.thumb else None,
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

    def prepare_send_message(self, message: TgMessage, is_preview: bool = False, bot: Bot = None,
                             channel_settings: ChannelSettings = None) -> Tuple[
        Callable, Dict, Dict]:
        bot = bot or self.bot
        real_message = message.to_object(bot)
        method, keywords = self.get_correct_send_message(real_message, bot=bot)
        channel_settings = channel_settings or self.tg_current_channel

        buttons = []

        if method == bot.send_message:
            keywords['text'] += f'\n\n{channel_settings.caption}'
        else:
            keywords['caption'] = channel_settings.caption

        if is_preview:
            buttons.extend([[
                self.create_button('Delete', callback=self.remove_from_queue_callback_query,
                                   data={'message_id': message.message_id})
            ]])

        reaction_dict = dict((reaction, []) for reaction in message.reactions or channel_settings.reactions)
        buttons.extend(self.get_reactions_tg_buttons(reactions=reaction_dict, with_callback=not is_preview))

        keywords['reply_markup'] = self.convert_buttons(buttons)

        return method, keywords, reaction_dict

    def get_reactions_tg_buttons(self, reactions: Dict, with_callback=False):
        return [
            [
                InlineKeyboardButton(text=f'{reaction} {len(reactions[reaction]) if reactions[reaction] else ""}',
                                     callback_data=f'reaction_button:{reaction}' if with_callback else 'nothing')
                for reaction in list(reactions)[index:index + 4]
            ]
            for index in range(0, len(reactions), 4)
        ]

    def get_all_file_ids_of_channel(self, channel_settings: ChannelSettings, force_reload: bool = False) -> Iterable[
        int]:
        yield from self.get_sent_file_id_of_chat(channel_settings.chat, force_reload)
        yield from self.get_queued_file_ids_of_channel(channel_settings)
        yield from self.get_added_file_ids_of_channel(channel_settings)

    def get_sent_file_id_of_chat(self, chat: TgChat, force_reload: bool = False) -> Iterable[int]:
        for channel in ChannelSettings.objects(chat=chat):
            if channel in self.sent_file_id_cache and not force_reload:
                yield from self.sent_file_id_cache[channel]
                continue

            for message in channel.sent_messages:
                message = resolve_dbref(TgMessage, message)
                if message is None:
                    continue
                yield from message.file_ids

    def get_queued_file_ids_of_channel(self, channel_settings: ChannelSettings) -> Iterable[int]:
        for queue in channel_settings.queued_messages.values():
            for message in queue:
                message = resolve_dbref(TgMessage, message)
                if message is None:
                    continue
                yield from message.file_ids

    def get_added_file_ids_of_channel(self, channel_settings: ChannelSettings) -> Iterable[int]:
        for message in channel_settings.added_messages:
            message = resolve_dbref(TgMessage, message)
            if message is None:
                continue
            yield from message.file_ids

    def get_similar_in_channel(self, min_similarity: float or int = None, message: TgMessage = None,
                               channel: TgChat = None, exclude_own: bool = False) -> list:
        message = message or self.tg_message
        channel = channel or self.tg_current_channel.chat
        min_similarity = min_similarity or 1

        results = []
        for entry in message.find_similar(self.bot):
            if 'chat_id' not in (entry['metadata'] or {}) or (exclude_own and entry['dist'] == 0.0):
                continue

            if entry['dist'] <= min_similarity and entry['metadata']['chat_id'] == channel.id:
                results.append(entry)
        return results

    def load_scheduled(self, channel: ChannelSettings = None, times: List[str] = None):
        times = times or []
        if channel:
            channels = [channel]
        else:
            channels = ChannelSettings.objects().all()

        for channel in channels:
            for time_str, posts in channel.scheduled_messages.items():
                time = datetime.fromtimestamp(int(time_str))
                if times and time_str not in times:
                    continue

                context = {
                    'channel': channel,
                    'time': time_str,
                }
                messages_str = ', '.join(map(lambda msg: str(msg.message_id), posts))
                print(f'Scheduling {messages_str} items in {channel.chat.id} at {time}')

                job_queue.run_once(self.send_scheduled_message, when=time, context=context)

    def send_scheduled_message(self, bot: Bot, job: Job, **kwargs):
        channel, time_str = list(job.context.values())
        time = datetime.fromtimestamp(int(time_str))
        messages = channel.scheduled_messages.get(time_str, [])[:]

        channel_link = self.get_username_or_link(channel.chat, is_markdown=True)
        if not messages:
            bot.send_message(chat_id=channel.user.id,
                             text=f'Scheduled messages for {channel_link} at `{time}`, could not be sent.',
                             parse_mode=ParseMode.MARKDOWN)
            return

        del channel.scheduled_messages[time_str]
        channel.save()

        for message in messages:
            method, include_kwargs, reaction_dict = self.prepare_send_message(message, is_preview=False, bot=bot,
                                                                              channel_settings=channel)

            try:
                new_message = method(chat_id=channel.chat.id, **include_kwargs)
                if not isinstance(new_message, Message):
                    new_message = new_message.result()
                new_tg_message = TgMessage(new_message, reactions=reaction_dict)
                new_tg_message.save()

                with channel.save_contextmanager():
                    if time in channel.scheduled_messages:
                        del channel.scheduled_messages[time]

                    if channel in self.sent_file_id_cache:
                        self.sent_file_id_cache[channel].extend(new_tg_message.file_ids)
                    else:
                        self.sent_file_id_cache[channel] = list(new_tg_message.file_ids)

                    channel.sent_messages.append(new_tg_message)
            except TimedOut:
                pass
            except (Exception, BaseException):
                try:
                    for message in filter(lambda msg: msg not in channel.sent_messages, messages):
                        method, include_kwargs, reaction_dict = self.prepare_send_message(
                            message, is_preview=False, bot=bot)
                        method(**include_kwargs)
                except (Exception, BaseException):
                    pass
                finally:
                    bot.send_message(chat_id=channel.user.id, reply_to_message_id=message.message_id,
                                     text=f'One of the scheduled (`{time}`) messages for {channel_link} could not be sent',
                                     parse_mode=ParseMode.MARKDOWN)
                    return
        bot.send_message(chat_id=channel.user.id, text=f'Messages scheduled for {channel_link} at `{time}` were sent.',
                         parse_mode=ParseMode.MARKDOWN)

    def str_to_utc_datetime(self, time_string) -> datetime:
        cal = parsedatetime.Calendar()
        datetime_obj, _ = cal.parseDT(datetimeString=time_string)
        return datetime_obj

    def utc_delta(self, start: datetime, end: datetime) -> timedelta:
        start = start.replace(tzinfo=timezone('UTC'))
        end = end.replace(tzinfo=timezone('UTC'))
        return end - start

    # # # # # # # # # # # # # # # # # # #
    # END Helper                        #
    # # # # # # # # # # # # # # # # # # #

    # # # # # # # # # # # # # # # # # # #
    # START Helper commands             #
    # # # # # # # # # # # # # # # # # # #

    @run_async
    def add_channel_command(self, **kwargs):
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
    def echo_state_command(self):
        """Debug method to send the users his state
        """
        self.message.reply_text(f'{self.tg_state.state}')

    @run_async
    def reset_state_command(self):
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

        for message in TgMessage.objects(chat=self.tg_chat, is_current_message=True):
            message.is_current_message = False
            message.save()

        self.tg_state.state = self.tg_state.IDLE
        self.list_channels_menu()

    # # # # # # # # # # # # # # # # # # #
    # END Helper commands               #
    # # # # # # # # # # # # # # # # # # #

    # # # # # # # # # # # # # # # # # # #
    # START Message Handlers            #
    # # # # # # # # # # # # # # # # # # #

    def message_handler_dispatcher(self):
        """Dispatch messages to correct function, defied by the users state
        """
        if self.update.channel_post:
            self.add_channel_post_message_handler()
            return

        if self.tg_state.state == self.tg_state.ADDING_CHANNEL:
            self.register_channel_message_handler()
        elif self.tg_state.state == self.tg_state.CHANGE_DEFAULT_CAPTION:
            self.change_caption_message_handler()
        elif self.tg_state.state == self.tg_state.CHANGE_DEFAULT_REACTION:
            self.change_reactions_message_handler()
        elif self.tg_state.state == self.tg_state.CREATE_SINGLE_POST:
            self.queue_message_message_handler()
        elif self.tg_state.state == self.tg_state.IMPORT_MESSAGES:
            self.add_message_to_import_queue_message_handler()
        elif self.tg_state.state == self.tg_state.SCHEDULE_ADDED_MESSAGES_WHEN and self.message.text:
            self.schedule_delay_menu(time_str=self.message.text)
        elif self.tg_state.state == self.tg_state.SCHEDULE_ADDED_MESSAGES_DELAY and self.message.text:
            self.schedule_batch_size_menu(delay_str=self.message.text)
        elif self.tg_state.state == self.tg_state.SCHEDULE_ADDED_MESSAGES_BATCH and self.message.text:
            self.schedule_confirmation_menu(amount=self.message.text)

    def add_channel_post_message_handler(self):
        channel = ChannelSettings.objects(chat=self.tg_message.chat).first()

        blacklist = channel.sent_messages + list(chain.from_iterable(map(dict.values, channel.queued_messages)))
        if self.tg_message in blacklist:
            return

        self.tg_message.save()
        channel.sent_messages.append(self.tg_message)
        channel.save()

    @run_async
    def register_channel_message_handler(self):
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

        permission = self.get_channel_permissions_for_bot(channel_chat)

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
        self.list_channels_menu()

    def queue_message_message_handler(self, *args, **kwargs):
        if not (self.message.text or self.message.photo or self.message.video or self.message.audio or
                self.message.voice or self.message.document or self.message.animation or self.message.sticker or
                self.message.video_note):
            self.message.reply_text('This type of message is not supported.', reply_message_id=self.message.message_id)
            return

        file_ids = self.get_all_file_ids_of_channel(self.tg_current_channel)
        similar_images = self.get_similar_in_channel()
        if [id for id in self.tg_message.file_ids if id in file_ids] \
                or [entry for entry in similar_images if entry['dist'] == 0.0]:
            self.message.reply_text('Message was already sent once or is queued.',
                                    reply_message_id=self.message.message_id)
        else:
            already_sent_temp = '\n{prefix} to {percentage}% already sent.'
            additional_buttons = []
            text = ''
            if [entry for entry in similar_images if entry['dist'] <= 0.1]:
                text = already_sent_temp.format(prefix=emoji.emojize(':stop_sign:'), percentage='90')
            elif [entry for entry in similar_images if entry['dist'] <= 0.3]:
                text = already_sent_temp.format(prefix=emoji.emojize(':warning:'), percentage='70')
            if text:
                additional_buttons.append([self.convert_button(self.create_button(text=text, prefix='nothing'))])

            self.tg_message.save()
            self.tg_current_channel.added_messages.append(self.tg_message)
            self.tg_current_channel.save()

            method, include_kwargs, reaction_dict = self.prepare_send_message(self.tg_message, is_preview=True)
            if additional_buttons:
                include_kwargs['reply_markup'].inline_keyboard.extend(additional_buttons)

            method(chat_id=self.chat.id, disable_notification=True, reply_message_id=self.message.message_id,
                   **include_kwargs)

        job = job_queue.run_once(
            lambda bot_, _job, **__: self.create_post_menu(recreate_message=True, *args, **kwargs),
            when=1
        )
        JobsQueue(user_id=self.user.id, job=job, type=JobsQueue.types.SEND_BUTTON_MESSAGE, replaceable=True)

    def add_message_to_import_queue_message_handler(self):
        if not (self.message.text or self.message.photo or self.message.video or self.message.audio or
                self.message.voice or self.message.document or self.message.animation or self.message.sticker or
                self.message.video_note):
            self.message.reply_text('This type of message is not supported.', reply_message_id=self.message.message_id)
            return

        if self.tg_message in self.tg_current_channel.sent_messages:
            self.message.reply_text('I know this message already', disable_notification=True,
                                    reply_message_id=self.message.message_id)
        else:
            self.tg_message.save()
            self.tg_current_channel.import_messages.append(self.tg_message)
            self.tg_current_channel.save()

        job = job_queue.run_once(
            lambda bot_, _job, **__: self.import_messages_menu(recreate_message=True),
            when=1
        )
        JobsQueue(user_id=self.user.id, job=job, type=JobsQueue.types.SEND_BUTTON_MESSAGE, replaceable=True)

    @run_async
    def change_caption_message_handler(self):
        if not self.message.text:
            self.message.reply_text('You have to send me some text or hit cancel.')
            return

        self.tg_current_channel.caption = self.message.text
        self.tg_current_channel.save()
        self.change_caption_menu()

    @run_async
    def change_reactions_message_handler(self):
        emojis = emoji.emoji_lis(self.message.text)
        reactions = [reaction['emoji'] for reaction in emojis]

        if not self.message.text or not emojis:
            self.message.reply_text('You have to send me some some reactions (Emoji).')
            return

        self.tg_current_channel.reactions = reactions
        self.tg_current_channel.save()
        self.change_reactions_menu()

    # # # # # # # # # # # # # # # # # # #
    # END Message Handlers              #
    # # # # # # # # # # # # # # # # # # #

    # # # # # # # # # # # # # # # # # # #
    # START Menu                        #
    # # # # # # # # # # # # # # # # # # #

    @run_async
    def list_channels_menu(self, **kwargs):
        self.tg_current_channel = None
        self.tg_state.state = self.tg_state.IDLE

        channels = ChannelSettings.objects(user=self.tg_user)
        if not channels:
            self.message.reply_text('You do not have any channels configured use /addchannel to add one.')
            return

        buttons = [
            [
                self.create_button(text=f'@{channel.chat.username}' if channel.chat.username else channel.chat.title,
                                   data={'channel_settings_id': channel.id}, callback=self.channel_actions_menu)
                for channel in channels[index:index + 2]
            ]
            for index in range(0, len(channels), 2)
        ]

        buttons.append([self.create_button(text='Add new channel', callback=self.add_channel_command)])

        real_buttons = self.convert_buttons(buttons)

        self.create_or_update_button_message(text='What do you want to do?', reply_markup=real_buttons, create=True)

    @run_async
    def channel_actions_menu(self, button: Button = None, recreate_message=False):
        if button and 'channel_settings_id' in button.data:
            self.tg_current_channel = ChannelSettings.objects(id=button.data['channel_settings_id']).first()
        elif self.tg_current_channel is None:
            self.message.reply_text('An error occured, please try again.')
            self.list_channels_menu()
            return

        self.tg_state.state = self.tg_state.CHANNEL_ACTIONS

        buttons = [
            [
                self.create_button('Create Port', callback=self.create_post_menu)
            ],
            [
                self.create_button('Remove', callback=self.remove_channel_from_callback_query,
                                   confirmation_requred=True, abort_callback=self.channel_actions_menu),
                self.create_button('Settings', callback=self.settings_menu),
            ],
            [
                self.create_button('Import messages', callback=self.import_messages_menu)
            ],
            [
                self.create_button('Scheduled', callback=self.send_scheduled_callback_query),
                self.create_button('Clear scheduled', callback=self.clear_scheduled_callback_query,
                                   confirmation_requred=True, abort_callback=self.channel_actions_menu)
            ],
            [
                self.create_button('Back', callback=self.list_channels_menu)
            ]
        ]

        chat_name = self.get_username_or_link(self.tg_current_channel)
        self.create_or_update_button_message(text=f'Channel: {chat_name}\nWhat do you want to do?',
                                             reply_markup=self.convert_buttons(buttons),
                                             create=recreate_message)

    @run_async
    def import_messages_menu(self, **kwargs):
        self.tg_state.state = self.tg_state.IMPORT_MESSAGES

        buttons = [
            [
                self.create_button('Finish', callback=self.import_sent_messages_callback_query,
                                   confirmation_requred=True, abort_callback=self.import_messages_menu)
            ],
            [
                self.create_button('Clear import queue', callback=self.clear_import_queue_callback_query,
                                   confirmation_requred=True, abort_callback=self.import_messages_menu)
            ],
            [
                self.create_button('Back', callback=self.channel_actions_menu)
            ]
        ]

        recreate_message = kwargs.get('recreate_message', False)
        chat_name = self.get_username_or_link(self.tg_current_channel, is_markdown=True)
        self.create_or_update_button_message(
            text=f'Channel: {chat_name}\nForward me messages from your channel or upload images to import them as '
            f'"sent messages". \nLike this I can check if a message has already been sent when you create a post.\n\n'
            f'When all messages has been sent, hit the "Finish" button. The back button will cancel the import.\n\n'
            f'Currently in the queue: `{len(self.tg_current_channel.import_messages)}`',
            reply_markup=self.convert_buttons(buttons), parse_mode=ParseMode.MARKDOWN, create=recreate_message)

    @run_async
    def create_post_menu(self, recreate_message: bool = False, **kwargs):
        self.tg_state.state = self.tg_state.CREATE_SINGLE_POST

        buttons = [
            [
                self.create_button('Preview', callback=self.send_post_callback_query, data={'preview': True}),
                self.create_button('Clear Queue', callback=self.clear_queue_callback_query, confirmation_requred=True,
                                   abort_callback=self.create_post_menu)
            ],
            [
                self.create_button('Send', callback=self.send_post_callback_query, confirmation_requred=True,
                                   abort_callback=self.create_post_menu),
                self.create_button('Schedule', callback=self.schedule_when_menu)
            ],
            [
                self.create_button('Back', callback=self.channel_actions_menu)
            ]
        ]

        chat_name = self.get_username_or_link(self.tg_current_channel, is_markdown=True)
        added_amount = len(self.tg_current_channel.added_messages)
        self.create_or_update_button_message(
            text=f'Channel: {chat_name}\nSend me messages to be sent to the channel\n'
            f'Currently `{added_amount}` are added.',
            reply_markup=self.convert_buttons(buttons), create=recreate_message, parse_mode=ParseMode.MARKDOWN)

    @run_async
    def schedule_when_menu(self, **kwargs):
        self.tg_state.state = self.tg_state.SCHEDULE_ADDED_MESSAGES_WHEN

        buttons = [
            [
                self.create_button('Now', callback=self.schedule_delay_menu, data={'time': 'now'}),
            ],
            [
                self.create_button('Morning [06:00]', callback=self.schedule_delay_menu, data={'time': 'morning'}),
                self.create_button('Noon [12:00]', callback=self.schedule_delay_menu, data={'time': 'noon'}),
            ],
            [
                self.create_button('Evening [18:00]', callback=self.schedule_delay_menu, data={'time': 'evening'}),
                self.create_button('Midnight [24:00]', callback=self.schedule_delay_menu, data={'time': 'midnight'}),
            ],
            [
                self.create_button('Back', callback=self.create_post_menu),
            ]
        ]

        chat_name = self.get_username_or_link(self.tg_current_channel, is_markdown=True)
        added_amount = len(self.tg_current_channel.added_messages)
        self.create_or_update_button_message(
            text=emoji.emojize(f'Channel: {chat_name} with `{added_amount}` posts in queue\nWhen do you want to start '
                               f'sending messages? Either hit a button or send me a date / time.\n\n'
                               f':ten_o’clock: Time is in UTC :ten_o’clock:'),
            reply_markup=self.convert_buttons(buttons), create=False, parse_mode=ParseMode.MARKDOWN)

    @run_async
    def schedule_delay_menu(self, button: Button = None, time_str: str = None, **kwargs):
        self.tg_state.state = self.tg_state.SCHEDULE_ADDED_MESSAGES_DELAY
        recreate = bool(time_str)

        time_str = (button.data.get('time') if button is not None else time_str) or time_str
        start_time = self.str_to_utc_datetime(time_str)
        delta = self.utc_delta(datetime.now(), start_time)
        if delta.total_seconds() < 0:
            start_time = start_time + timedelta(days=1)

        self.tg_state.state_data[self.tg_state.SCHEDULE_ADDED_MESSAGES_WHEN] = time_str
        self.tg_state.save()

        buttons = [
            [
                self.create_button('1h', callback=self.schedule_batch_size_menu, data={'delay': '1h'}),
                self.create_button('3h', callback=self.schedule_batch_size_menu, data={'delay': '3h'}),
                self.create_button('6h', callback=self.schedule_batch_size_menu, data={'delay': '6h'}),
            ],
            [
                self.create_button('5min', callback=self.schedule_batch_size_menu, data={'delay': '5min'}),
                self.create_button('10min', callback=self.schedule_batch_size_menu, data={'delay': '10min'}),
                self.create_button('15min', callback=self.schedule_batch_size_menu, data={'delay': '15min'}),
            ],
            [
                self.create_button('Back', callback=self.schedule_when_menu),
            ]
        ]

        chat_name = self.get_username_or_link(self.tg_current_channel, is_markdown=True)
        added_amount = len(self.tg_current_channel.added_messages)
        self.create_or_update_button_message(
            text=f'Channel: {chat_name} with `{added_amount}` posts in queue\n- Starttime: `{start_time}`\n\n'
            f'How big should the delay be between each batch? Again hit a button or tell me via text.',
            reply_markup=self.convert_buttons(buttons), create=recreate, parse_mode=ParseMode.MARKDOWN)

    @run_async
    def schedule_batch_size_menu(self, button: Button = None, delay_str: str = None, **kwargs):
        self.tg_state.state = self.tg_state.SCHEDULE_ADDED_MESSAGES_BATCH
        recreate = bool(delay_str)

        delay_str = (button.data.get('delay') if button is not None else delay_str) or delay_str
        self.tg_state.state_data[self.tg_state.SCHEDULE_ADDED_MESSAGES_DELAY] = delay_str
        self.tg_state.save()

        time_delta_str = str(timedelta(seconds=pytimeparse.parse(delay_str)))
        start_time = self.str_to_utc_datetime(self.tg_state.state_data[self.tg_state.SCHEDULE_ADDED_MESSAGES_WHEN])

        buttons = [
            [
                self.create_button('1 msg', callback=self.schedule_confirmation_menu, data={'amount': '1'}),
                self.create_button('5 msg', callback=self.schedule_confirmation_menu, data={'amount': '5'}),
                self.create_button('10 msg', callback=self.schedule_confirmation_menu, data={'amount': '10'}),
                self.create_button('20 msg', callback=self.schedule_confirmation_menu, data={'amount': '20'}),
            ],
            [
                self.create_button('Back', callback=self.schedule_when_menu),
            ]
        ]

        chat_name = self.get_username_or_link(self.tg_current_channel, is_markdown=True)
        added_amount = len(self.tg_current_channel.added_messages)
        self.create_or_update_button_message(
            text=f'Channel: {chat_name} with `{added_amount}` posts in queue\n- Starttime: `{start_time}`\n'
            f'- Delay: `{time_delta_str}`\n\nHow many should be sent per batch? Click on a button or tell me via text',
            reply_markup=self.convert_buttons(buttons),
            create=recreate, parse_mode=ParseMode.MARKDOWN)

    @run_async
    def schedule_confirmation_menu(self, button: Button = None, amount: str = None, **kwargs):
        self.tg_state.state = self.tg_state.SCHEDULE_ADDED_MESSAGES_CONFIRMATION
        recreate = bool(amount)

        amount = (button.data.get('amount') if button is not None else amount) or amount
        self.tg_state.state_data[self.tg_state.SCHEDULE_ADDED_MESSAGES_BATCH] = amount
        self.tg_state.save()

        time_delta_str = str(
            timedelta(seconds=pytimeparse.parse(self.tg_state.state_data[self.tg_state.SCHEDULE_ADDED_MESSAGES_DELAY])))
        start_time = self.str_to_utc_datetime(self.tg_state.state_data[self.tg_state.SCHEDULE_ADDED_MESSAGES_WHEN])

        buttons = [
            [
                self.create_button('Yes', callback=self.schedule_callback_query),
            ],
            [
                self.create_button('Back', callback=self.schedule_when_menu),
            ]
        ]

        chat_name = self.get_username_or_link(self.tg_current_channel, is_markdown=True)
        added_amount = len(self.tg_current_channel.added_messages)
        self.create_or_update_button_message(
            text=f'Channel: {chat_name} posts in queue {added_amount}\n- Starttime: `{start_time}`\n'
            f'- Delay: `{time_delta_str}`\n- Batch size: `{amount}`\n\nAre those options ok?',
            reply_markup=self.convert_buttons(buttons),
            create=recreate, parse_mode=ParseMode.MARKDOWN)

    @run_async
    def settings_menu(self, **kwargs):
        self.tg_state.state = self.tg_state.IN_SETTINGS

        buttons = [
            [
                self.create_button(text='Caption', callback=self.change_caption_menu),
                self.create_button(text='Reactions', callback=self.change_reactions_menu)
            ],
            [
                self.create_button(text='Reset', callback=self.reset_settings_callback_query, confirmation_requred=True,
                                   abort_callback=self.settings_menu),
            ],
            [
                self.create_button('Back', callback=self.channel_actions_menu)
            ]
        ]
        chat_name = self.get_username_or_link(self.tg_current_channel)
        self.create_or_update_button_message(text=f'Channel: {chat_name}\nWhat do you want to do?',
                                             reply_markup=self.convert_buttons(buttons))

    @run_async
    def change_caption_menu(self, **kwargs):
        chat_name = self.get_username_or_link(self.tg_current_channel)
        buttons = self.convert_buttons([[self.create_button('Finished', callback=self.settings_menu)]])

        self.create_or_update_button_message(
            f'Channel: {chat_name}\nYour default caption at the moment is:\n{self.tg_current_channel.caption or "Empty"}',
            reply_markup=buttons)
        self.tg_state.state = self.tg_state.CHANGE_DEFAULT_CAPTION

    @run_async
    def change_reactions_menu(self, **kwargs):
        chat_name = self.get_username_or_link(self.tg_current_channel)

        reactions = self.tg_current_channel.reactions
        buttons = [[
            self.create_button('Finished', callback=self.settings_menu)
        ]]
        buttons.extend([
            [
                self.create_button(text=reaction, prefix='nothing')
                for reaction in reactions[index:index + 4]
            ]
            for index in range(0, len(reactions), 4)
        ])

        self.create_or_update_button_message(
            f'Channel: {chat_name}\nYour default reactions at the moment are\n{"" if reactions else "None"}',
            reply_markup=self.convert_buttons(buttons))
        self.tg_state.state = self.tg_state.CHANGE_DEFAULT_REACTION

    # # # # # # # # # # # # # # # # # # #
    # ENDD Menu                         #
    # # # # # # # # # # # # # # # # # # #

    # # # # # # # # # # # # # # # # # # #
    # START Callback Query              #
    # # # # # # # # # # # # # # # # # # #

    @run_async
    def remove_channel_from_callback_query(self, **kwargs):
        self.tg_current_channel.delete()

        self.update.effective_message.reply_text('Channel was removed')
        self.list_channels_menu()

    @run_async
    def send_scheduled_callback_query(self, **kwargs):
        messages = self.tg_current_channel.scheduled_messages
        if not messages:
            self.message.reply_text('No messages scheduled')
        else:
            self.message.reply_text('Messages were scheduled at:\n{}'.format(
                '\n'.join(map(lambda item: f'`{datetime.fromtimestamp(int(item[0]))}:` {len(item[1])} messages',
                              messages.items()))
            ), parse_mode=ParseMode.MARKDOWN)
        self.channel_actions_menu(recreate_message=True)

    @run_async
    def clear_scheduled_callback_query(self, **kwargs):
        self.tg_current_channel.scheduled_messages = {}
        self.tg_current_channel.save()
        self.message.reply_text('Schedule was cleared')
        self.channel_actions_menu(recreate_message=True)

    @run_async
    def schedule_callback_query(self, **kwargs):
        when = self.tg_state.state_data.get(self.tg_state.SCHEDULE_ADDED_MESSAGES_WHEN)
        delay = self.tg_state.state_data.get(self.tg_state.SCHEDULE_ADDED_MESSAGES_DELAY)
        batch_size = self.tg_state.state_data.get(self.tg_state.SCHEDULE_ADDED_MESSAGES_BATCH)

        when = self.str_to_utc_datetime(when) or datetime.now()
        delay = timedelta(seconds=pytimeparse.parse(delay)) or timedelta(hours=1)
        batch_size = int(batch_size) if batch_size is not None and batch_size.isdigit() else 10

        messages = self.tg_current_channel.added_messages[:]
        self.tg_current_channel.added_messages = []

        temp_list = []
        times = {}
        hours_counter = 0
        for index, message in enumerate(messages):
            temp_list.append(message)

            if (index + 1) % batch_size == 0:
                time = when + (delay * hours_counter)
                time_str = str(int(time.timestamp()))
                times[time_str] = temp_list[:]
                self.tg_current_channel.scheduled_messages[time_str] = times[time_str]
                hours_counter += 1
                temp_list = []

        if temp_list:
            time = when + (delay * hours_counter)
            time_str = str(int(time.timestamp()))
            times[time_str] = temp_list[:]
            self.tg_current_channel.scheduled_messages[time_str] = times[time_str]

        self.tg_current_channel.save()

        self.load_scheduled(channel=self.tg_current_channel, times=list(times.keys()))
        self.message.reply_text('Messages were scheduled at:\n{}'.format(
            '\n'.join(map(lambda item: f'`{datetime.fromtimestamp(int(item[0]))}:` {len(item[1])} messages',
                          times.items()))
        ), parse_mode=ParseMode.MARKDOWN)
        self.create_post_menu(recreate_message=True)

    # Post section
    @run_async
    def send_post_callback_query(self, button: Button = None):
        preview = False
        if button:
            preview = button.data.get('preview', False)

        # Move items to queue
        self.tg_state.state = self.tg_state.SEND_LOCKED
        messages = list(filter(None, map(
            lambda msg: resolve_dbref(TgMessage, msg), self.tg_current_channel.added_messages)))

        uuid = None
        self.tg_current_channel.queued_messages = self.tg_current_channel.queued_messages or {}
        if not preview:
            uuid = str(uuid4())
            self.tg_current_channel.queued_messages[uuid] = messages
            self.tg_current_channel.added_messages = []
        else:
            self.tg_current_channel.added_messages = messages
        self.tg_current_channel.save()
        self.tg_state.state = self.tg_state.CREATE_SINGLE_POST

        # Actual sending mechanism
        send_to = self.chat if preview else self.tg_current_channel.chat

        progress_bar = TelegramProgressBar(
            bot=self.bot,
            chat_id=self.chat.id,
            pre_message='Sending images ' + ('as preview' if preview else 'to chat') + ' [{current}/{total}]',
            se_message='This could take some time.',
        )

        if not preview:
            self.create_post_menu(recreate_message=True)

        for index, stored_message in progress_bar.enumerate(messages[:]):
            try:
                method, include_kwargs, reaction_dict = self.prepare_send_message(stored_message, is_preview=preview)

                new_message = method(chat_id=send_to.id, **include_kwargs, isgroup=not preview)
                if not preview:
                    new_tg_message = TgMessage(new_message, reactions=reaction_dict)
                    new_tg_message.save()

                    if self.tg_current_channel in self.sent_file_id_cache:
                        self.sent_file_id_cache[self.tg_current_channel].extend(new_tg_message.file_ids)
                    else:
                        self.sent_file_id_cache[self.tg_current_channel] = list(new_tg_message.file_ids)

                    self.tg_current_channel.queued_messages[uuid].remove(stored_message)
                    self.tg_current_channel.sent_messages.append(new_tg_message)
            except TimedOut:
                pass
            except (BaseException, Exception) as e:
                if not preview:
                    # Move queued messages back to added messages if an error occurs
                    if self.tg_current_channel.added_messages is None:
                        self.tg_current_channel.added_messages = []

                    self.tg_current_channel.added_messages += self.tg_current_channel.queued_messages[uuid]
                    del self.tg_current_channel.queued_messages[uuid]
                    self.tg_current_channel.save()

                self.message.reply_text('An error occurred please contact an admin with /error')
                self.tg_state.state = self.tg_state.CREATE_SINGLE_POST
                self.create_post_menu(recreate_message=True)
                raise e

        self.tg_current_channel.save()
        if preview:
            self.create_post_menu(recreate_message=True)
        else:
            del self.tg_current_channel.queued_messages[uuid]
            self.message.reply_text('All queued messages sent')

    @run_async
    def clear_queue_callback_query(self, **kwargs):
        self.tg_current_channel.added_messages = []
        self.tg_current_channel.save()
        self.message.reply_text(text='Queue cleared')

        self.create_post_menu(recreate_message=True)

    @run_async
    def clear_import_queue_callback_query(self, **kwargs):
        self.tg_current_channel.import_messages = []
        self.tg_current_channel.save()
        self.message.reply_text(text='Queue cleared')

        self.create_post_menu(recreate_message=True)

    @run_async
    def import_sent_messages_callback_query(self, **kwargs):
        uuid = str(uuid4())
        self.tg_current_channel.import_messages_queue[uuid] = self.tg_current_channel.import_messages
        self.tg_current_channel.import_messages = []
        self.tg_current_channel.save()

        progress_bar = TelegramProgressBar(
            bot=self.bot,
            chat_id=self.chat.id,
            pre_message='Importing messages [{current}/{total}]',
            se_message='This could take some time.',
        )

        for message in progress_bar(self.tg_current_channel.import_messages_queue[uuid][:]):
            try:
                message.add_to_image_match(self.bot, metadata={'chat_id': self.tg_current_channel.chat.id})
                self.tg_current_channel.import_messages_queue[uuid].remove(message)
                self.tg_current_channel.save()
            except (BaseException, Exception) as error:
                self.tg_current_channel.import_messages = self.tg_current_channel.import_messages_queue[uuid][:]
                del self.tg_current_channel.import_messages_queue[uuid]
                self.tg_current_channel.save()

                self.message.reply_text('An error occurred while importing the messages. Try again or contact an admin')
                self.import_messages_menu(recreate_message=True)
                raise error

        self.import_messages_menu(recreate_message=True)

    @run_async
    def remove_from_queue_callback_query(self, button: Button):
        message = TgMessage.objects(message_id=button.data['message_id'], chat=self.tg_chat).first()
        if message:
            reply = ''
            if message in self.tg_current_channel.added_messages:
                self.tg_current_channel.added_messages.remove(message)
                self.tg_current_channel.save()
                reply = 'Message was removed'

            self.message.delete()
            self.update.callback_query.answer(reply)
        else:
            self.update.callback_query.answer('Could not remove message, contact /support')
        self.create_post_menu(recreate_message=True)

    def reaction_button_callback_query(self):
        reaction = self.update.callback_query.data.replace('reaction_button:', '')
        message = TgMessage.objects(message_id=self.message.message_id, chat=self.tg_chat).first()

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

        buttons = InlineKeyboardMarkup(self.get_reactions_tg_buttons(message.reactions, with_callback=True))
        self.message.edit_reply_markup(reply_markup=buttons)
        self.update.callback_query.answer(emoji.emojize('Thanks for voting :thumbs_up:'))

    # Settings Section
    @run_async
    def reset_settings_callback_query(self, **kwargs):
        self.tg_current_channel.caption = ''
        self.tg_current_channel.reactions = []
        self.tg_current_channel.save()

        self.message.reply_text('Settings were reset')
        self.settings_menu()

    # # # # # # # # # # # # # # # # # # #
    # END Callback Query                #
    # # # # # # # # # # # # # # # # # # #


channel = ChannelManager()
