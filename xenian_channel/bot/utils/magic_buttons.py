from typing import Callable, Dict, List
from uuid import uuid4

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update, User
from telegram.error import BadRequest
from telegram.ext import run_async

from xenian_channel.bot.utils.telegram import keep_message_args


class MagicButton:
    """Button used of the magic_buttons method of :class:`ChannelManager`

    Attributes:
         text (:obj:`str`): Text represented inside the button
         user (:obj:`telegram.user.User`): Telegram User obj
         callback (:obj:`Callable`): A method of self, which must accept at lest this arguments:
                - bot: :obj:`telegram.bot.Bot`
                - update: :obj:`telegram.update.Update`
                - data: :obj:`str` The data given in data
                - *args: :obj:`List` Additional data given in callback_args
                - **kwargs: :obj:`Dict` Additional data given in callback_kwargs
         callback_args (:obj:`List`, optional): List of additional arguments
         callback_kwargs (:obj:`Dict`, optional): Dict of additional keyword arguments
         data (:obj:`Dict`, optional): A dict which is forwarded to ``callback``
         url (:obj:`str`, optional): A url where the user should be sent to
         yes_no (:obj:`bool`, optional): If before calling the callback function a yes / no answer should be given
            Final data to the given to the Callback will be "original_data:yes | no"
         yes_no_text (:obj:`str`, optional): Text for yes / no question
         no_callback (:obj:`Callable`, optional): Same as "callback" but for the answer no
         no_callback_args (:obj:`List`, optional): List of additional arguments
         no_callback_kwargs (:obj:`Dict`, optional): Dict of additional keyword arguments
    """

    all_buttons = {}

    def __init__(self,
                 text: str,
                 user: User,
                 callback: Callable,
                 callback_args: List = None,
                 callback_kwargs: Dict = None,
                 data: Dict = None,
                 url: str = None,
                 yes_no: bool = False,
                 yes_no_text: str = None,
                 no_callback: Callable = None,
                 no_callback_args: List = None,
                 no_callback_kwargs: Dict = None,
                 ):
        from xenian_channel.bot import job_queue
        self.id = str(uuid4())
        MagicButton.all_buttons[self.id] = self
        job_queue.run_once(
            callback=self.del_self,
            when=86400,  # 24h
            name=f'Timout magic button: {self.id}')

        self.text = text
        self.user = user
        self.data = data or {}
        self.url = url
        self.yes_no = yes_no
        self.yes_no_text = yes_no_text or 'Are you sure?'

        self.callback = callback
        self.callback_args = callback_args or []
        self.callback_kwargs = callback_kwargs or {}

        self.no_callback = no_callback
        self.no_callback_args = no_callback_args or []
        self.no_callback_kwargs = no_callback_kwargs or {}

        if self.yes_no and not self.no_callback:
            raise AttributeError('When yes_no is given no_callback must be given too')

        if self.data is None and self.url is None:
            raise AttributeError('Either data or url must be given')

    def del_self(self, *args, **kwargs):
        if self.id in MagicButton.all_buttons:
            del MagicButton.all_buttons[self.id]

    def __str__(self):
        return str(self.to_dict())

    def to_dict(self):
        data = dict()

        for key in iter(self.__dict__):
            if key in ['id']:
                continue

            value = self.__dict__[key]
            if value is None:
                data[key] = ''
            else:
                data[key] = value

        return data

    def copy(self, **kwargs):
        data = self.to_dict()
        data.update(kwargs)
        return MagicButton(**data)

    @staticmethod
    def invalidate_by_user_id(user_id: int) -> List[str]:
        invalidated = []
        for key, button in MagicButton.all_buttons.copy().items():
            if user_id == button.user.id:
                del MagicButton.all_buttons[key]
                invalidated.append(key)
        return invalidated

    @staticmethod
    def invalidate_by_key(key: str) -> bool:
        if key in MagicButton.all_buttons:
            del MagicButton.all_buttons[key]
            return True
        return False

    def convert(self, button=None) -> InlineKeyboardButton:
        """Convert MagicButton into :obj:`telegram.inline.inlinekeyboardbutton.InlineKeyboardButton`

        Args:
            button (:obj:`MagicButton`, optional): A MagicButton or None if Non self is converted

        Returns:
            :obj:`telegram.inline.inlinekeyboardbutton.InlineKeyboardButton`: The converted button
        """
        button = button or self
        if isinstance(button, InlineKeyboardButton):
            return button

        if button.url:
            return InlineKeyboardButton(text=button.text, url=button.url)

        callback = f'magic_button:{button.id}'
        return InlineKeyboardButton(text=button.text, callback_data=callback)

    @staticmethod
    def conver_buttons(buttons: List[List]) -> InlineKeyboardMarkup:
        """Convert a two dimensional of MagicButtons to :obj:`telegram.inline.inlinekeyboardmarkup.InlineKeyboardMarkup`

        Args:
            buttons (:obj:`List[List[MagicButton]]`): MagicButtons in two dimensional list to convert

        Returns:
            :obj:`telegram.inline.inlinekeyboardmarkup.InlineKeyboardMarkup`: An object usable for reply_markup
        """
        real_buttons = []
        for row in buttons:
            new_row = []
            for button in row:
                new_row.append(button.convert() if not isinstance(button, InlineKeyboardButton) else button)
            real_buttons.append(new_row)
        return InlineKeyboardMarkup(real_buttons)

    @staticmethod
    @keep_message_args
    @run_async
    def message_handler(bot: Bot, update: Update, *args, **kwargs):
        callback_query = update.callback_query
        message = callback_query.message

        button_id = callback_query.data.split(':')[1]

        button = MagicButton.all_buttons.get(button_id)
        if not button:
            try:
                message.edit_text('Your request timed out, please retry.')
            except BadRequest:
                message.edit_reply_markup()
            return

        yes_no_answer = button.data.get('yes_no_answer') or 'yes' if not button.yes_no else False
        if yes_no_answer:
            if yes_no_answer == 'yes':
                method = button.callback
                custom_args = button.callback_args
                custom_kwargs = button.callback_kwargs
            else:
                method = button.no_callback
                custom_args = button.no_callback_args
                custom_kwargs = button.no_callback_kwargs

            original_data = button.data
            if button.data.get('original'):
                original_button = MagicButton.all_buttons.get(button.data['original'])
                original_data = original_button.data

            method(bot=Bot, update=update, data=original_data, *custom_args, **custom_kwargs)
            if button.data.get('yes_no_answer'):
                try:
                    message.delete()
                except BadRequest:
                    pass
            return

        yes_button = button.copy(text='Yes', yes_no=False, data={'original': button.id, 'yes_no_answer': 'yes'})
        no_button = button.copy(text='No', yes_no=False, data={'original': button.id, 'yes_no_answer': 'no'})

        real_buttons = MagicButton.conver_buttons([[yes_button, no_button]])
        message.edit_text(text=button.yes_no_text, reply_markup=real_buttons)
