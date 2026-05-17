"""Railway-friendly Telegram polling worker for CoinPilotXAI."""

import logging

import bot


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bot.main()


if __name__ == "__main__":
    main()
