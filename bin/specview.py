import argparse
import configparser
import os

import view.demoview


def main(args):
    config = configparser.ConfigParser()
    config.read(args.config)

    view.demoview.run()


if __name__ == "__main__":
    # handle commandline archuments
    parser = argparse.ArgumentParser(description="Wordpress Demo.")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("CONFIG", "./ghconfig.ini"),
        help="Configuration file",
    )
    args = parser.parse_args()

    main(args)
