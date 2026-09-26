import logging
import os

from voice_caller.agent import agent

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "WARNING").upper(),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
agent.serve()
