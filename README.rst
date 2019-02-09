Xenian Channel Bot
==================

`@XenianChannelBot <https://t.me/XenianChannelBot>`__ \|
`GitHub <https://github.com/Nachtalb/XenianChannelBot>`__

.. contents:: Table of Contents


What I do
---------

I am your personal assistant to help you with your channels. You may want to start with ``/addchannel``.
After you added your channels use ``/list`` to view and manage them.

If you need any help use ``/help`` or ask an admin with ``/support``.

Commands
--------

Direct Commands:
~~~~~~~~~~~~~~~~

Base Group
^^^^^^^^^^

-  ``/start`` - Initialize the bot
-  ``/commands`` - Show all available commands
-  ``/support`` - Contact bot maintainer for support of any kind
-  ``/error <text>`` - Send the supporters and admins a request of any kind
-  ``/help`` - Show all available commands
-  ``/contribute <text>`` - Send the supporters and admins a request of any kind

Channel Manager
^^^^^^^^^^^^^^^

-  ``/addchannel`` - Add a channel
-  ``/removechannel`` - Remove a channel
-  ``/list`` - List all channels
-  ``/state`` - Debug - Show users current state
-  ``/reset`` - Debug - Reset the users current state

Contributions
-------------

Bug report / Feature request
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you have found a bug or want a new feature, please file an issue on GitHub `Issues <https://github.com/Nachtalb/XenianChannelBot/issues>`__

Code Contribution / Pull Requests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Please use a line length of 120 characters and `Google Style Python Docstrings <http://sphinxcontrib-napoleon.readthedocs.io/en/latest/example_google.html>`__.

Development
~~~~~~~~~~~

For the project I choose `buildout <http://www.buildout.org/en/latest/contents.html>`__ instead of the default pip way.
I manly did this because it makes installation easier. I recommend to be in an virtualenv for any project, but this is
up to you. Now for the installation:

.. code:: bash

   ln -s development.cfg buildout.cfg
   python bootstrap.py
   bin/buildout

And everything should be installed. Now you can copy and configure your settings. For this you need an Telegram Bot API
Token > `@BotFather <https://t.me/BotFather>`__. The ``settings.py`` should be self explanatory.

.. code:: bash

   cp xenian_channel/bot/settings.example.py  xenian_channel/bot/settings.py

To run the bot simply run

.. code:: bash

   bin/bot

Command Concept
^^^^^^^^^^^^^^^

I am still working on how I want to make the commends to be used as easily as possible. At the moment this is how it works:

In the folder ``xenian_channel/bot/commands/`` you’ll find a ``__init__.py``, ``base.py`` and ``builtins.py``.
The ``base.py`` contains the base command, which is used for every other command. It has the following attributes:

all_commands
    This is a variable containing all the commands which you create with this class as Parent. If you override the
    ``__init__`` method you have to call super init otherwise, the command will not be added to this list. This list is
    used for adding the commands as handlers for telegram and for creating the commands list.
commands
    This is a list of dictionaries in which you can define commands. This list contains the following keys:

    title (optional)
        If no title given the name of the command function is taken (underscores replaced with space and the first word
        is capitalized)A string for a title for the command. This does not have to be the same as the ``command_name``.
        Your ``command_name`` could be eg. ``desc`` so the command would be ``/desc``, but the title would be
        ``Describe``. Like this, it is easier for the user to get the meaning of function from a command directly from
        the command list. - ``description`` (optional): Default is an empty string. As the name says, this is the
        description. It is shown on the command list. Describe what your command does in a few words.

    command_name (optional)
        Default is the name of the given command function. This is what the user has to run So for the start command it
        would be ``start``. If you do not define one yourself, the lowercase string of the name of your class is taken.

    command (mandatory)
        This is the function of the command. This has to be set.

    handler (optional)
        Default is the CommandHandler. This is the handler your command uses. This could be ``MessageHandler``,
        ``CommandHandler`` or any other handler.

    options (optional)
        By default the callback and command are set. If you add another argument you do not have to define callback and
        command in the CommandHandler again and callback in the MessageHandler. This is a dict of arguments given to the
        handler.

    hidden (optional)
        Default is False. If True the command is hidden from the command list.

    args (optional)
        If you have args, you can write them here. Eg. a command like this: ``/add_human Nick 20 male`` your text would
        be like ``NAME AGE GENDER``.

    alias (optional)
        Set alias to the name of an other command to automatically create an alias for it.


After you create your class, you have to call it at least once. It doesn’t matter where you call it from, but I like to
just call it directly after the code, as you can see in the builtins.py. And do not forget that the file with the
command must be loaded imported somewhere. I usually do this directly in the ``__init__.py``.


Copyright
---------

Thank you for using `@XenianChannelBot <https://t.me/XenianChannelBot>`__.

Made by `Nachtalb <https://github.com/Nachtalb>`_ | This extension licensed under the `GNU General Public License v3.0 <https://github.com/Nachtalb/XenianChannelBot/blob/master/LICENSE>`_.
