"""Tests for LLM adapter factory and env-var fallback logic."""
from chatbot_jira.llm import llm_adapter_from_env


def test_llm_base_url_prefers_llm_over_ollama():
    adapter = llm_adapter_from_env({"LLM_BASE_URL": "http://llm.local/v1", "OLLAMA_BASE_URL": "http://ollama.local/v1"})
    assert adapter._base_url == "http://llm.local/v1"


def test_llm_base_url_falls_back_to_ollama():
    adapter = llm_adapter_from_env({"OLLAMA_BASE_URL": "http://ollama.local/v1"})
    assert adapter._base_url == "http://ollama.local/v1"


def test_llm_base_url_defaults_to_localhost():
    adapter = llm_adapter_from_env({})
    assert adapter._base_url == "http://localhost:11434/v1"


def test_llm_api_key_prefers_llm_over_ollama():
    adapter = llm_adapter_from_env({"LLM_API_KEY": "llm-key", "OLLAMA_API_KEY": "ollama-key"})
    assert adapter._api_key == "llm-key"


def test_llm_api_key_falls_back_to_ollama():
    adapter = llm_adapter_from_env({"OLLAMA_API_KEY": "ollama-key"})
    assert adapter._api_key == "ollama-key"


def test_llm_api_key_none_when_empty():
    adapter = llm_adapter_from_env({})
    assert adapter._api_key is None


def test_llm_model_default():
    adapter = llm_adapter_from_env({})
    assert adapter._model == "gemma4:31b"


def test_llm_model_override():
    adapter = llm_adapter_from_env({"LLM_MODEL": "llama3:8b"})
    assert adapter._model == "llama3:8b"


def test_llm_timeout_default():
    adapter = llm_adapter_from_env({})
    assert adapter._timeout == 60.0


def test_llm_timeout_custom():
    adapter = llm_adapter_from_env({"LLM_TIMEOUT_SECONDS": "120"})
    assert adapter._timeout == 120.0


def test_llm_timeout_invalid_falls_back():
    adapter = llm_adapter_from_env({"LLM_TIMEOUT_SECONDS": "not-a-number"})
    assert adapter._timeout == 60.0
