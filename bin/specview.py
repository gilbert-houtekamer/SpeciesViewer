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
    logging.basicConfig(filename='svlog.log', level=logging.INFO)
    sview.loghandler = LoggingHandler()
    logging.getLogger().addHandler(sview.loghandler)
    logging.getLogger().addHandler(logging.StreamHandler())
    logger.info("specview started")
    # read our configuration
    svconfig = sview.model.SVConfig(Path(args.root))
    sview.ui.app.run(svconfig)
    logger.info("specview finished")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=str,
        default=os.environ.get("SVROOT", os.path.abspath(".")),
        help="Species Viewer distribution root",
    )
    main(parser.parse_args())
