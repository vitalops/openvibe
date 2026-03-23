"""LoopGPT is a modular Auto-GPT framework"""

__version__ = "0.1.2"


from loopgpt.agent import Agent, empty_agent
from loopgpt.tools import *
from loopgpt.memory import *
from loopgpt.embeddings import *
from loopgpt.aifunc import aifunc

agent_from_config = Agent.from_config
from loopgpt.tools import from_config as tool_from_config
from loopgpt.memory import from_config as memory_from_config
from loopgpt.embeddings import from_config as embedding_provider_from_config

from loopgpt.utils.openai_key import check_openai_key
from dotenv import load_dotenv

import sys


load_dotenv()


def from_config(config):
    return globals()[config["type"] + "_from_config"](config)


def set_aifunc_args(model, embedding_provider):
    """Set the model and embedding provider that will be used by :class:`~loopgpt.aifunc.aifunc` to create agents when needed.

    :param model: The model to use.
    :type model: ~loopgpt.models.base.BaseModel
    :param embedding_provider: The embedding provider to use.
    :type embedding_provider: ~loopgpt.embeddings.base.BaseEmbeddingProvider
    """
    aifunc.model = model
    aifunc.embedding_provider = embedding_provider


if "pytest" not in sys.modules:
    check_openai_key()
