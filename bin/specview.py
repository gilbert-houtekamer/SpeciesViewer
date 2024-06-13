import argparse
import os
import logging
from pathlib import Path
from sview.util import LoggingHandler

import sview.model
import sview.ui

logger = logging.getLogger(__name__)

def main(args):
    # initialise logging
    logging.basicConfig(level=logging.INFO)
    sview.loghandler = LoggingHandler()
    logging.getLogger().addHandler(sview.loghandler)
    logging.getLogger("flet_runtime").setLevel(logging.WARNING)
    logger.info("specview started")
    # read our configuration
    svconfig = sview.model.SVConfig(Path(args.root))
    if len(svconfig.repositories_names()) > 0:
        names = svconfig.repositories_names()
        name = names[0]
        repo = svconfig.get_repository(name)
        svconfig.set_current_repository(repo)
    sview.ui.app.run(svconfig)
    logger.info("specview finished")
    # loggers = [logging.getLogger(name) for name in logging.root.manager.loggerDict]

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=str,
        default=os.environ.get("SVROOT", os.path.abspath(".")),
        help="Species Viewer distribution root",
    )
    main(parser.parse_args())
