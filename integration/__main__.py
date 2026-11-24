import sys

from integration.engine import main

# The guard is required: on Windows multiprocessing "spawn" re-imports this module in every worker
# process, which must not start another engine.
if __name__ == "__main__":
    sys.exit(main())
