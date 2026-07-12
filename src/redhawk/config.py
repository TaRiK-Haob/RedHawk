"""Runtime configuration: loads .env and builds LLM clients.

Models are specialized per role to optimize cost/speed:
- recon agents (web-recon, host-recon) use a cheaper/faster model when
  DEEPSEEK_MODEL_RECON is set, else fall back to the main model.
- the coordinator and working-tier agents (web-pentest, exploit-dev) use the
  main model (DEEPSEEK_MODEL).

We use ``langchain-deepseek`` (ChatDeepSeek) instead of the generic ChatOpenAI
so that DeepSeek's ``reasoning_content`` field is preserved in streaming chunks.
This lets the TUI display the model's internal reasoning in thinking blocks.
"""

import os

from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

load_dotenv()

_BASE_URL = os.getenv("DEEPSEEK_API_BASE_URL", "https://api.deepseek.com")
_API_KEY = os.environ["DEEPSEEK_API_KEY"]
_MAIN_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def build_model() -> ChatDeepSeek:
    """Main model for the coordinator + working-tier agents."""
    return ChatDeepSeek(
        model=_MAIN_MODEL,
        base_url=_BASE_URL,
        api_key=_API_KEY,
        temperature=0.2,
    )


def build_recon_model() -> ChatDeepSeek:
    """Cheaper/faster model for recon agents.

    Override via DEEPSEEK_MODEL_RECON; otherwise falls back to the main model
    so a single-model .env still works.
    """
    return ChatDeepSeek(
        model=os.getenv("DEEPSEEK_MODEL_RECON", _MAIN_MODEL),
        base_url=_BASE_URL,
        api_key=_API_KEY,
        temperature=0.1,
    )
