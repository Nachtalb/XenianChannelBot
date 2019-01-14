import logging
import os

BASE_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), '../../'))

TELEGRAM_API_TOKEN = ''  # DevXenianChannelBot


ADMINS = ['@SOME_TELEGRAM_USERS', ]  # Users which can do admin tasks like /restart
SUPPORTER = ['@SOME_TELEGRAM_USERS', ]  # Users which to contact fo support

TEMPLATE_DIR = os.path.join(BASE_DIR, 'xenian_channel/bot/commands/templates')

# More information about polling and webhooks can be found here:
# https://github.com/python-telegram-bot/python-telegram-bot/wiki/Webhooks
MODE = {
    'active': 'polling',  # webook or polling, if webhook further configuration is required
    # 'webhook': {
    #     'listen': '127.0.0.1',  # what to listen to, normally localhost
    #     'port': 5000,  # What port to listen to, if you have multiple bots running they mustn't be the same
    #     'url_path': TELEGRAM_API_TOKEN,  # Use your API Token so no one can send fake requests
    #     'url': 'https://your_domain.tld/%s' % TELEGRAM_API_TOKEN,  # Your Public domain, with your token as path so
    #     # telegram knows where to send the request to
    # },
}

LOG_LEVEL = logging.DEBUG

MONGODB_CONFIGURATION = {
    'host': 'localhost',  # default: localhost
    'port': 27017,  # default: 27017
    'db_name': 'DevXenianChannelBot',
}
