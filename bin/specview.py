import argparse
import os
from pathlib import Path

import sview.model
import sview.ui


def main(args):
    # read our configuration
    svconfig = sview.model.SVConfig(Path(args.root))
    sview.ui.app.run(svconfig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=str,
        default=os.environ.get("SVROOT", os.path.abspath(".")),
        help="Species Viewer distribution root",
    )
    main(parser.parse_args())
