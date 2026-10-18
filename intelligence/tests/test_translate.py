"""Grok translation hook for the free-text parts of the words: cached on disk, falls back to English on failure."""
import json

import pytest

import providers as pv


class FakeHttp:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": json.loads(body)})
        status, payload = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return status, json.dumps(payload).encode()


def reply(text):
    return (200, {"choices": [{"message": {"role": "assistant", "content": text}}]})


class TestGrokTranslator:
    def test_calls_chat_completions_with_language_and_keeps_ids_dates_numbers_rule(self, tmp_path):
        http = FakeHttp(reply("ਅੱਗ ਦੀ ਜਾਂਚ ਕਰੋ F0012 2026-10-19 16 mm"))
        tr = pv.GrokTranslator("xai-key", model="grok-4.7", cache_path=tmp_path / "t.json", http=http)
        out = tr("Check the field for fire. F0012 2026-10-19 16 mm", "pa")
        assert out.startswith("ਅੱਗ")
        c = http.calls[0]
        assert c["url"] == "https://api.x.ai/v1/chat/completions"
        assert c["headers"]["Authorization"] == "Bearer xai-key"
        assert c["body"]["model"] == "grok-4.7"
        msgs = c["body"]["messages"]
        assert "Punjabi" in msgs[0]["content"] and "F0012" in msgs[0]["content"]        # system prompt names the language and the keep-as-is rule
        assert msgs[-1]["content"].endswith("16 mm")

    def test_cache_means_the_same_text_is_never_paid_twice_and_survives_restart(self, tmp_path):
        http = FakeHttp(reply("पहला"))
        tr = pv.GrokTranslator("k", cache_path=tmp_path / "t.json", http=http)
        assert tr("first", "hi") == "पहला"
        assert tr("first", "hi") == "पहला"
        assert len(http.calls) == 1
        again = pv.GrokTranslator("k", cache_path=tmp_path / "t.json", http=FakeHttp(reply("SHOULD NOT BE USED")))
        assert again("first", "hi") == "पहला"
        assert json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))["hi␟first"] == "पहला"

    def test_failure_falls_back_to_english_and_does_not_cache(self, tmp_path):
        http = FakeHttp((500, {"error": "boom"}))
        tr = pv.GrokTranslator("k", cache_path=tmp_path / "t.json", http=http)
        assert tr("Harvest before the rain", "pa") == "Harvest before the rain"
        assert not (tmp_path / "t.json").exists() or "pa␟Harvest before the rain" not in (tmp_path / "t.json").read_text(encoding="utf-8")

    def test_english_and_empty_text_short_circuit(self, tmp_path):
        http = FakeHttp(reply("x"))
        tr = pv.GrokTranslator("k", cache_path=tmp_path / "t.json", http=http)
        assert tr("plain", "en") == "plain" and tr("", "pa") == ""
        assert http.calls == []


class TestTranslatorFromEnv:
    def test_no_key_means_no_translator(self, tmp_path):
        assert pv.translator_from_env({}, cache_path=tmp_path / "t.json") is None

    def test_key_builds_a_translator_with_model_from_env(self, tmp_path):
        tr = pv.translator_from_env({"XAI_API_KEY": "k", "XAI_MODEL": "grok-4.7"}, cache_path=tmp_path / "t.json", http=FakeHttp(reply("ok")))
        assert isinstance(tr, pv.GrokTranslator) and tr.model == "grok-4.7"
