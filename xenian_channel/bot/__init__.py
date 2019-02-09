from elasticsearch import Elasticsearch
from image_match.elasticsearch_driver import SignatureES

from xenian_channel.bot.settings import MONGODB_CONFIGURATION

job_queue = None

elastic_search = Elasticsearch()
image_match_ses = SignatureES(elastic_search)
