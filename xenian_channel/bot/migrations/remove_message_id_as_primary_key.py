import logging
from copy import deepcopy

from bson import ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection

from xenian_channel.bot import MONGODB_CONFIGURATION

mongodb_client = MongoClient(host=MONGODB_CONFIGURATION['host'], port=MONGODB_CONFIGURATION['port'])
mongodb_database = mongodb_client[MONGODB_CONFIGURATION['db_name']]

logger = logging.getLogger('Mongo Message _ID Migration')


class Message:
    def __init__(self, message):
        self.is_old = True
        self.migrated = False

        try:
            int(message['_id'])
        except:
            self.is_old = False

        if self.is_old:
            self.old_message = message
            self.old_id = message['_id']
            self.new_id = ObjectId()
            self.new_message = self.get_new_message()
        else:
            self.migrated = True
            self.old_message = None
            self.old_id = message['message_id']
            self.new_id = message['_id']
            self.new_message = message

    def get_new_message(self):
        message = deepcopy(self.old_message)
        message['_id'] = self.new_id
        message['message_id'] = self.old_id
        return message

    def migrate(self, collection: Collection):
        if not self.is_old:
            return

        collection.insert_one(self.new_message)
        collection.delete_one(self.old_message)
        self.migrated = True


class Migrator:
    mapping = {}
    message_col = mongodb_database.telegram_message
    channel_col = mongodb_database.channel_settings

    def __init__(self):
        self.messages = self.message_col.find()
        self.channels = list(self.channel_col.find())

    def __call__(self, *args, **kwargs):
        self.migrate()

    def migrate(self):
        for index, msg in enumerate(list(self.messages)[::-1]):
            message = Message(msg)
            message.migrate(self.message_col)

            self.migrate_channels(message)
            if index % 50 == 0:
                logger.info(f'Migrating [{index}]')
        self.save_channels()

    def migrate_channels(self, message: Message):
        def replace_id(channel, _id):
            if _id == message.old_id and channel['user'] == message.new_message.get('chat'):
                return message.new_id
            return _id

        for channel in self.channels:
            imp_msgs = channel.get('import_messages', [])
            sent_msgs = channel.get('sent_messages', [])
            add_msgs = channel.get('added_messages', [])

            channel['import_messages'] = list(map(lambda id_: replace_id(channel, id_), imp_msgs))
            channel['sent_messages'] = list(map(lambda id_: replace_id(channel, id_), sent_msgs))
            channel['added_messages'] = list(map(lambda id_: replace_id(channel, id_), add_msgs))

            queue_msgs = channel.get('queued_messages', {})
            imp_queue_msgs = channel.get('import_messages_queue', {})

            if isinstance(queue_msgs, list):
                queue_msgs = {}
                channel['queued_messages'] = {}
            if isinstance(imp_queue_msgs, list):
                imp_queue_msgs = {}
                channel['import_messages_queue'] = {}

            for key, messages in queue_msgs.items():
                channel['queued_messages'][key] = list(map(lambda id_: replace_id(channel, id_), messages))

            for key, messages in imp_queue_msgs.items():
                channel['import_messages_queue'][key] = list(map(lambda id_: replace_id(channel, id_), messages))

    def old_id_filter(self, item):
        return not str(item).isdigit()

    def save_channels(self):
        for channel in self.channels:
            channel['import_messages'] = list(filter(self.old_id_filter, channel['import_messages']))
            channel['sent_messages'] = list(filter(self.old_id_filter, channel['sent_messages']))
            channel['added_messages'] = list(filter(self.old_id_filter, channel['added_messages']))

            for key, messages in channel.get('queued_messages', {}).items():
                channel['queued_messages'][key] = list(
                    filter(self.old_id_filter, channel['queued_messages'][key]))

            for key, messages in channel.get('import_messages_queue', {}).items():
                channel['import_messages_queue'][key] = list(
                    filter(self.old_id_filter, channel['import_messages_queue'][key]))

            self.channel_col: Collection
            self.channel_col.update_one({'_id': channel['_id']}, {'$set': channel})
