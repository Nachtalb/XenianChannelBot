import importlib
import logging
import os
import sys
from argparse import ArgumentParser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('MIGRATOR')


def migrate():
    parser = ArgumentParser('XenianChannelBot Migrator')
    parser.add_argument('migration', type=str)
    args = parser.parse_args()

    migrator_module_string = args.migration
    migrator_module_string, _ = os.path.splitext(migrator_module_string)

    package_reference = __name__.rsplit('.', 1)[0]
    try:
        module = importlib.import_module(f'{package_reference}.{migrator_module_string}')
    except ImportError:
        logger.fatal(f'Could not find migration {migrator_module_string}')
        sys.exit(1)

    migrator_class = getattr(module, 'Migrator', None)

    if migrator_class is None:
        logger.fatal(f'Could not find "Migrator" class in migration {migrator_module_string}')
        sys.exit(1)

    logger.info('Migrating ...')
    migrator = migrator_class()
    migrator()
    logger.info('Migration finished')
