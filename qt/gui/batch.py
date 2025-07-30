import logging
import configparser
from utils.batch_config import generate_svconfig_ini

def call_batch_module(config, page, logger, module_called, before_msg, after_msg):
    logger = logging.getLogger("speciesviewer")

    # Build fresh config file for batch operation
    generate_svconfig_ini(config)

    args = None
    module_config = configparser.ConfigParser()
    module_config.read("svconfig.ini")

    logger.info(before_msg)
    module_called(args, module_config)
    logger.info(after_msg)
