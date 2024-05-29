import argparse
import os
import flet
import sview.ui
import sview.model


def main(args):
    # read our configuration
    sview.ui.svconfig = sview.model.SVConfig.from_path(args.config)
    flet.app(target=sview.ui.run, view=flet.FLET_APP)


if __name__ == "__main__":
    # handle commandline archuments
    parser = argparse.ArgumentParser(description="Species Viewer.")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("CONFIG", os.path.abspath("./svconfig.ini")),
        help="Species Viewer Configuration file",
    )

    main(parser.parse_args())
