"""Start the Freenet Link Downloader."""

import sys

from fld.app import main


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        from fld.smoke_test import run

        raise SystemExit(run(sys.argv[2]))
    if "--smoke-test" in sys.argv:
        raise SystemExit("Usage: FLD.exe --smoke-test REPORT.json")
    main()
