Changelog
=========

0.1.4 (unreleased)
------------------

- Call add channel from the channel list menu


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
