import argparse
import logging
import time

from api.config import Settings
from worker.service import Worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Process queued inspections and AI summaries")
    parser.add_argument(
        "--once", action="store_true", help="Process at most one queued job and exit"
    )
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    worker = Worker(Settings.from_env())
    try:
        while True:
            processed = worker.process_one()
            if arguments.once:
                return
            if not processed:
                time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Worker stopped")
    finally:
        worker.close()


if __name__ == "__main__":
    main()
