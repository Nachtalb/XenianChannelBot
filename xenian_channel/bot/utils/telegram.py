from functools import wraps
from typing import Callable

from telegram import Bot, Update, User
from telegram.error import TimedOut, NetworkError

from . import MWT

__all__ = ['get_self', 'get_user_link']


@MWT(timeout=60 * 60)
def get_self(bot: Bot) -> User:
    """Get User object of this bot

    Args:
        bot (:obj:`Bot`): Telegram Api Bot Object

    Returns:
        :obj:`User`: The user object of this bot
    """
    return bot.get_me()


def get_user_link(user: User) -> str:
    """Get the link to a user

    Either the @Username or [First Name](tg://user?id=123456)

    Args:
        user (:obj:`telegram.user.User`): A Telegram User Object

    Returns:
        :obj:`str`: The link to a user
    """
    if user.username:
        return '@{}'.format(user.username)
    else:
        return '[{}](tg://user?id={})'.format(user.first_name, user.id)


def retry_command(retries: int = None, *args, notify_user=True, existing_update: Update = None,
                  **kwargs) -> Callable:
    """Decorater to retry a command if it raises :class:`telegram.error.TimedOut`

    Args:
        retries (:obj:`int`): How many times the command should be retried
        notify_user (:obj:`bool`): Try to notify user if TimedOut is still raised after given amount of retires
        existing_update (:obj:`telegram.update.Update`): Telegram Api Update Object if the decorated function is
            not a command

    Raises:
        (:class:`telegram.error.TimedOut`): If TimedOut is still raised after given amount of retires

    Returns:
        (:object:`Callable`): Wrapper function
    """
    func = None
    if isinstance(retries, Callable):
        func = retries
        retries = 3
    retries = retries or 3

    def wrapper(*args, **kwargs):
        error = None
        for try_ in range(retries):
            error = None
            print(f'Try {try_}')
            try:
                return func(*args, **kwargs)
            except (TimedOut, NetworkError) as e:
                if isinstance(e, TimedOut) or (isinstance(e, NetworkError) and 'The write operation timed out' in e.message):
                    error = e
        else:
            if notify_user and existing_update or (len(args) > 1 and getattr(args[1], 'message', None)):
                update = existing_update or args[1]
                update.message.reply_text(text='Command failed at some point after multiple retries. '
                                               'Try again later or contact an admin /support.',
                                          reply_to_message_id=update.message.message_id)
            if error:
                raise error

    if func:
        return wrapper

    return wraps(wrapper)