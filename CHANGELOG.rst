Changelog
=========

0.3.4 (unreleased)
------------------

- Put username and link of bot into start message
- Partially fix messages database issues. The ID of messages are only chat unique but they were used as primary keys.
    This means that data was lost by messages overriding themselves. This can't be fully fixed. But it will not
    happen in the future anymore.


0.3.3 (2019-02-13)
------------------

- Fix issue with opening wrong menu after importing messages
- Fix not checking message type before importing a message
- Fix crash on timed out error


0.3.2 (2019-02-09)
------------------

- Fix error when saving channel without changed fields


0.3.1 (2019-02-09)
------------------

- Fix title in readme


0.3.0 (2019-02-09)
------------------

- Find similar images among sent ones, to prevent sending the same image twice
- Add new option to import sent messages to prevent sending duplicates


0.2.1 (2019-02-07)
------------------

- Compare sent files across users
- Try to resolve DBRefs


0.2.0 (2019-02-03)
------------------

- Call add channel from the channel list menu
- Fix problem with channels having no username
- Implement MessageQueue from telegram python bot to prevent hitting the flood limit
- Support sending multiple queues in a one channel simultaneously
- Fix channel overlapping with other users
- Improve message texts
- Send the user a message if a queue was fully sent successfully
- Directly send preview of added message when adding messages
- Attempt to fix issue with duplicate posts
- Attempt to fix issue with sending posts
- Prevent user from uploading the same file twice

0.1.3 (2019-01-29)
------------------

- Make buttons persistent
- Refactor channel manager command class


0.1.2 (2019-01-28)
------------------

- Prevent race condition


0.1.1 (2019-01-28)
------------------

- Fix queued messages lost when at least one fails
- Fix typo


0.1.0 (2019-01-28)
------------------

- Almost complete rewrite, everything should almost be the same (frontend)
- Use MongoEngine instead of pymongo
- Add DEBUG setting to enable more functionality during development
- Send messages in background so user doesn't need to wait for the bot
- Be able to reset settings
- Change button text from cancel to back
- Read commit messages for full changelog


0.0.3 (2019-01-15)
------------------

- Various improvements (mainly performance and stability)
- Bug fixes
- Implement default reactions


0.0.2 (2019-01-14)
------------------

- Update ``settings.example.py``
- Fix typo


0.0.1 (2019-01-14)
------------------

- Copy source code from `@XenianChannelBot <https://github.com/Nachtalb/XenianChannelBot>`_ and strip it down
- Improve alias commands
- Show actual commands in /commands instead of the commands as code
- Add channel integration with ``/addchannel``,  ``/removechannel`` and ``/list``
