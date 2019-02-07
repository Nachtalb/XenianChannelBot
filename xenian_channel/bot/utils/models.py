from typing import Type

from bson import DBRef
from mongoengine import Document


def resolve_dbref(document: Type[Document], dbref: dict or DBRef or Document) -> Document or None:
    if isinstance(dbref, dict) and '_ref' in dbref:
        dbref = dbref['_ref']
    elif isinstance(dbref, document):
        return dbref

    return document.objects(pk=dbref.id).first()
