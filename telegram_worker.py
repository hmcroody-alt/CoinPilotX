"""Railway-friendly Telegram polling worker for CoinPilotXAI."""

import logging

import bot


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Telegram worker starting polling with command and typed-question handlers.")
    bot.main()


if __name__ == "__main__":
    main()
