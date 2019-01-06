import os
import re
from collections import OrderedDict
from copy import deepcopy
from typing import Callable

import requests
from pybooru import Danbooru as PyDanbooru, Moebooru as PyMoebooru
from pybooru.resources import SITE_LIST
from requests.exceptions import MissingSchema
from requests_html import HTMLSession
from telegram import Bot, ChatAction, InputFile, InputMediaPhoto, Update
from telegram.error import BadRequest, TimedOut
from telegram.ext import run_async

from xenian.bot import mongodb_database
from xenian.bot.settings import ANIME_SERVICES
from xenian.bot.utils import TelegramProgressBar, download_file_from_url_and_upload
from . import BaseCommand

__all__ = ['animedatabases']

SITE_LIST['safebooru'] = {'url': 'https://safebooru.donmai.us'}


class DanbooruService:
    FREE_LEVEL = 20
    GOLD_LEVEL = 30
    PLATINUM_LEVEL = 31
    BUILDER = 32
    JANITOR = 35
    MODERATOR = 40
    ADMIN = 50

    LEVEL_RESTRICTIONS = {
        'tag_limit': {
            FREE_LEVEL: 2,
            GOLD_LEVEL: 6,
            PLATINUM_LEVEL: 12,
            BUILDER: 32,
            JANITOR: 35,
            MODERATOR: 40,
            ADMIN: 50,
        },
        'censored_tags': {
            FREE_LEVEL: True,
            GOLD_LEVEL: False,
            PLATINUM_LEVEL: False,
            BUILDER: False,
            JANITOR: False,
            MODERATOR: False,
            ADMIN: False,
        }
    }

    type = 'danbooru'

    def __init__(self, name: str, url: str, api: str = None, username: str = None, password: str = None) -> None:
        self.name = name
        self.url = url.lstrip('/') if url is not None else None
        self.api = api
        self.username = username
        self.password = password

        self.client = None
        self.session = None
        self.user_level = None
        self.tag_limit = None
        self.censored_tags = None

        self.init_client()
        self.init_session()

    def init_client(self):
        if self.api:
            if not self.username:
                raise ValueError('Danbooru API Services need a Username when API key is given.')
            self.client = PyDanbooru(site_name=self.name, site_url=self.url, api_key=self.api, username=self.username)
        else:
            self.client = PyDanbooru(site_name=self.name, site_url=self.url)

        self.user_level = self.get_user_level()
        self.tag_limit = self.LEVEL_RESTRICTIONS['tag_limit'][self.user_level]
        self.censored_tags = self.LEVEL_RESTRICTIONS['censored_tags'][self.user_level]

        if not self.url:
            self.url = self.client.site_url.lstrip('/')

    def init_session(self):
        if self.username and self.password and self.url:
            self.session = HTMLSession()
            login_page = self.session.get(f'{self.url.lstrip("/")}/session/new')
            form = login_page.html.find('.simple_form')[0]

            login_data = {
                'name': self.username,
                'password': self.password,
                'remember': '1',
            }
            for input in form.find('input'):
                value = input.attrs.get('value', None)
                name = input.attrs.get('name', None)
                if name:
                    login_data.setdefault(name, value)

            self.session.post(f'{self.url.lstrip("/")}/session', login_data)

    def get_user_level(self):
        user_level = 20
        if self.username:
            user = self.client.user_list(name_matches=self.client.username)
            user_level = user[0]['level']
        return user_level


class MoebooruService:
    type = 'moebooru'

    def __init__(self, name: str, url: str, username: str = None, password: str = None,
                 hashed_string: str = None) -> None:
        self.name = name
        self.url = url.lstrip('/') if url is not None else None
        self.username = username
        self.password = password
        self.hashed_string = hashed_string

        self.client = None
        self.init_client()

    def init_client(self):
        if self.username and self.password:
            self.client = PyMoebooru(site_name=self.name, site_url=self.url, hash_string=self.hashed_string,
                                     username=self.username, password=self.password)
            return

        self.client = PyDanbooru(site_name=self.name, site_url=self.url)
        if not self.url:
            self.url = self.client.site_url.lstrip('/')


class AnimeDatabases(BaseCommand):
    """The class for all danbooru related commands
    """
    group = 'Anime'

    def __init__(self):
        self.files = mongodb_database.files

        self.services = {}
        self.init_services()

        super(AnimeDatabases, self).__init__()

    def init_services(self):
        """Initialize services
        """
        for service in ANIME_SERVICES:
            name = service['name']
            service_information = deepcopy(service)
            del service_information['type']
            if service['type'] == 'danbooru':
                self.services[name] = DanbooruService(**service_information)
            if service['type'] == 'moebooru':
                self.services[name] = MoebooruService(**service_information)

            self.commands.append({
                'title': name.capitalize(),
                'description': f'Search on {name}',
                'command': self.search_wrapper(name),
                'command_name': name,
                'options': {'pass_args': True},
                'args': ['tag1', 'tag2...', 'page=PAGE_NUM', 'limit=LIMIT', 'group=SIZE']
            })

    def search_wrapper(self, service_name: str) -> Callable:
        """Wrapper to set the service for the search command

        Args:
            service_name (:obj:`str`): Name for the service

        Returns:
            (:obj:`Callable`): Search method for the telegram command

        Raises:
            (:class:`NotImplementedError`): Search function for given service does not exist
        """
        service = self.services[service_name]

        method_name = f'{service.type}_search'
        method = getattr(self, method_name, None)

        if not method:
            raise NotImplementedError(f'Search function ({method_name}) for service {service_name} does not exist.')

        def search(*args, **kwargs):
            method(service=service, *args, **kwargs)

        return search

    def moebooru_search(self, bot: Bot, update: Update, service: DanbooruService, args: list = None):
        pass

    @run_async
    def danbooru_search(self, bot: Bot, update: Update, service: DanbooruService, args: list = None):
        """Search on Danbooru API Sites

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            update (:obj:`telegram.update.Update`): Telegram Api Update Object
            service (:obj:`DanbooruService`): Initialized :obj:`DanbooruService` for the various api calls
            args (:obj:`list`, optional): List of search terms and options
        """
        message = update.message
        text = ' '.join(args)

        text, page = self.extract_option_from_string('page', text, int)
        text, limit = self.extract_option_from_string('limit', text, int)
        text, group_size = self.extract_option_from_string('group', text, int)

        if group_size and group_size > 10:
            message.reply_text('Max group size is 10', reply_to_message_id=message.message_id)
            return

        query = {
            'page': page or 0,
            'limit': limit or 10
        }

        if ',' in text:
            terms = text.split(',')
        else:
            terms = text.split(' ')
        terms = self.filter_terms(terms)

        if len([term for term in terms if ':' not in term]) > service.tag_limit:  # Do not count qualifiers like "order:score"
            message.reply_text(f'Only {service.tag_limit} tags can be used.', reply_to_message_id=message.message_id)
            return

        if service.censored_tags:
            message.reply_text('Some tags may be censored', reply_to_message_id=message.message_id)

        query['tags'] = ' '.join(terms)

        self.danbooru_post_list_send_media_group(bot, update, service, query, group_size=group_size)

    def filter_terms(self, terms: list) -> list:
        """Ensure terms for the danbooru tag search are valid

        Args:
            terms (:obj:`list`): List of not yet validated strings

        Returns:
                :obj:`list`: List with the given strings validated
        """
        black_list = re.compile('[^\w_\- +~*:]+')
        terms = map(lambda term: black_list.sub('', term), terms)
        terms = map(lambda term: term.strip(), terms)
        terms = map(lambda term: term.replace(' ', '_'), terms)
        terms = filter(lambda term: not black_list.match(term) and bool(term), terms)
        return list(OrderedDict.fromkeys(terms))

    def extract_option_from_string(self, name: str, text: str, type_: str or int = None) -> tuple:
        """Extract option from string

        Args:
            name (:obj:`str`): Name of the option
            text (:obj:`str`): Text itself
            type_ (:obj:`str` or :obj:`int`, optional): Type of option is it a string or an int, default is string

        Returns
            :obj:`tuple`: First item the text without the option, the second the value of the option
        """
        type_ = type_ or str
        options = {
            'name': name,
            'type': '\d' if type_ == int else '\w'
        }
        out = None

        page_pattern = re.compile('{name}[ =:]+{type}+'.format(**options), re.IGNORECASE)
        match = page_pattern.findall(text)
        if match:
            text = page_pattern.sub('', text)
            out = re.findall('\d+', match[0])[0]
            if type_ == int:
                out = int(re.findall('\d+', match[0])[0])

        return text, out

    def get_image(self, post_id: int, image_url: str = None):
        """Save image to file and save in db

        Args:
            post_id (:obj:`int`): Post od as identification
            image_url (:obj:`str`, optional): Url to image which should be saved

        Returns:
           ( :obj:`str`): Location of saved file
        """
        db_entry = self.files.find_one({'file_id': post_id})
        if db_entry:
            location = db_entry['location']
            if os.path.isfile(location):
                return location

            try:
                response = requests.head(location)
                if response.status_code == 200:
                    return location
            except MissingSchema:
                # This gets raised when a "location" is a local file but does not exist anymore
                pass

        if not image_url:
            return

        downloaded_image_location = download_file_from_url_and_upload(image_url)
        self.files.update({'file_id': post_id},
                          {'file_id': post_id, 'location': downloaded_image_location},
                          upsert=True)
        return downloaded_image_location

    def danbooru_post_list_send_media_group(self, bot: Bot, update: Update, service: DanbooruService, query: dict, group_size: bool = False):
        """Send Danbooru API Service queried images to user

        Args:
            bot (:obj:`telegram.bot.Bot`): Telegram Api Bot Object.
            update (:obj:`telegram.update.Update`): Telegram Api Update Object
            query (:obj:`dict`): Query with keywords for post_list see:
                https://pybooru.readthedocs.io/en/stable/api_danbooru.html#pybooru.api_danbooru.DanbooruApi_Mixin.post_list
            group_size (:obj:`bool`): If the found items shall be grouped to a media group
        """
        message = update.message

        if query.get('limit', 0) > 100:
            query['limit'] = 100

        posts = service.client.post_list(**query)

        if not posts:
            message.reply_text('Nothing found on page {page}'.format(**query))
            return

        progress_bar = TelegramProgressBar(
            bot=bot,
            chat_id=message.chat_id,
            pre_message=('Sending' if not group_size else 'Gathering') + ' files\n{current} / {total}',
            se_message='This could take some time.'
        )

        groups = {}
        current_group_index = 0
        error = False
        for index, post in progress_bar.enumerate(posts):
            image_url = post.get('large_file_url', None)
            post_url = '{domain}/posts/{post_id}'.format(domain=service.url, post_id=post['id'])
            post_id = post['id']
            caption = f'@XenianBot - {post_url}'

            image_url = self.get_image(post_id, image_url) or image_url

            if not image_url:
                if service.session or group_size:
                    response = service.session.get(post_url)
                    img_tag = response.html.find('#image-container > img')

                    if not img_tag:
                        error = True
                        continue
                    img_tag = img_tag[0]
                    image_url = self.get_image(post_id, img_tag.attrs['src'])
                else:
                    image_url = post.get('source', None)

            if not image_url:
                error = True
                continue

            if group_size:
                if image_url.endswith(('.webm', '.gif', '.mp4', '.swf', '.zip')):
                    error = True
                    continue
                if index % group_size == 0:
                    current_group_index += 1
                groups.setdefault(current_group_index, [])
                groups[current_group_index].append(InputMediaPhoto(image_url, caption))
                continue

            bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_PHOTO)
            try:
                file = None
                if os.path.isfile(image_url):
                    file = open(image_url, mode='rb')

                sent_media = None
                if image_url.endswith(('.png', '.jpg')):
                    sent_media = message.reply_photo(
                        photo=file or image_url,
                        caption=caption,
                        disable_notification=True,
                        reply_to_message_id=message.message_id,
                    )

                if file:
                    file.seek(0)

                message.chat.send_document(
                    document=file or image_url,
                    disable_notification=True,
                    caption=caption,
                    reply_to_message_id=sent_media.message_id if sent_media else None,
                )
            except (BadRequest, TimedOut):
                error = True
                continue

        for group_index, items in groups.items():
            for item in items:
                if os.path.isfile(item.media):
                    with open(item.media, 'rb') as file_:
                        item.media = InputFile(file_, attach=True)

            @self.retry_command(existing_update=update)
            def send_images_as_group():
                bot.send_media_group(
                    chat_id=message.chat_id,
                    media=items,
                    reply_to_message_id=message.message_id,
                    disable_notification=True
                )

        reply = ''
        if message.chat.type not in ['group', 'supergroup']:
            reply = 'Images has been sent'

        if error:
            reply += '\nNot all found images could be sent. Most of the times this is because an image is not ' \
                     'publicly available or because the filetype is not supported (zip, webm etc. when sending as a ' \
                     'group).'
        if reply:
            message.reply_text(reply.strip())


animedatabases = AnimeDatabases()
