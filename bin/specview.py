import argparse
import configparser
import os

import ui


def main(args):
    config = configparser.ConfigParser()
    config.read(args.config)

    ui.frontpage.run(config)


if __name__ == "__main__":
    # handle commandline archuments
    parser = argparse.ArgumentParser(description="Species Viewer.")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("CONFIG", "./svconfig.ini"),
        help="Species Viewer Configuration file",
    )
    args = parser.parse_args()

    main(args)
