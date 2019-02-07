from setuptools import find_packages, setup

version = '0.2.2.dev0'

setup(name='XenianChannelBot',
      version=version,
      description="Channel Management Bot based on @XenianBot",
      long_description=f'{open("README.rst").read()}\n{open("CHANGELOG.rst").read()}',

      author='Nachtalb',
      url='https://github.com/Nachtalb/XenianChannelBot',
      license='GPL3',

      packages=find_packages(exclude=['ez_setup']),
      namespace_packages=['xenian_channel'],
      include_package_data=True,
      zip_safe=False,

      install_requires=[
          'emoji',
          'htmlmin',
          'mako',
          'mr.developer',
          'mongoengine',
          'python-telegram-bot',
          'image_match',
      ],

      entry_points={
          'console_scripts': [
              'bot = xenian_channel.bot.bot:main']
      })
