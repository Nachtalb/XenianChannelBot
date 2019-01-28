from mongoengine import Document, StringField, DictField


class Button(Document):
    text = StringField()
    callback = StringField()

    data = DictField()
    url = StringField()

    prefix = StringField()

    @property
    def callback_data(self):
        return f'{self.prefix}:{self.id}'
