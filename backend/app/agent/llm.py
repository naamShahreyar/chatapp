from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.config import settings


class EchoChatModel(BaseChatModel):
    """Fake LLM for local dev and tests: no API key, no cost, predictable output."""

    @property
    def _llm_type(self) -> str:
        return "echo"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        history = [m for m in messages if m.type != "system"]
        text = (
            f"(echo) You said: {history[-1].content!r}. "
            f"I can see {len(history)} messages in this conversation."
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def get_llm() -> BaseChatModel:
    if settings.llm_provider == "fake":
        return EchoChatModel()
    if settings.llm_provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=settings.llm_model, google_api_key=settings.google_api_key)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")

