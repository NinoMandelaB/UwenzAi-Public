"""Tokenizer resolution: strict registry lookups, family fallbacks, and caching."""

import warnings
from unittest import mock

from django.test import SimpleTestCase

from capp.classes.ai import tokenizer_factory
from capp.classes.ai.tokenizer_factory import (
    MistralTokenizerWrapper,
    _tokenizer_cache,
    get_tokenizer_for_model,
)


class TokenizerFactoryTests(SimpleTestCase):
    def setUp(self):
        _tokenizer_cache.clear()
        self.addCleanup(_tokenizer_cache.clear)

    @staticmethod
    def _real_from_model():
        from mistral_common.tokens.tokenizers.mistral import MistralTokenizer

        return MistralTokenizer, MistralTokenizer.from_model

    def test_registered_model_resolves_strictly(self):
        MistralTokenizer, real = self._real_from_model()
        with mock.patch.object(MistralTokenizer, "from_model", side_effect=real) as m:
            tokenizer = get_tokenizer_for_model("mistral-small")
        m.assert_called_once_with("mistral-small", strict=True)
        self.assertIsInstance(tokenizer, MistralTokenizerWrapper)

    def test_unregistered_large_model_uses_v7_family(self):
        """Large 4 / Large 4.0 / latest alias must use the Large 2.1 (v7) tokenizer, not the 2023 v3 one."""
        MistralTokenizer, _ = self._real_from_model()
        expected = MistralTokenizerWrapper(MistralTokenizer.from_model("mistral-large-2411", strict=True))
        sample = (
            "Habari! Karibu UwenzAi — a reasonably long mixed-language sample so "
            "v3 and v7 token counts separate cleanly."
        )
        for model in ("mistral-large-4", "mistral-large-4-0", "mistral-large-latest"):
            with self.subTest(model=model):
                _tokenizer_cache.clear()
                tokenizer = get_tokenizer_for_model(model)
                self.assertIsInstance(tokenizer, MistralTokenizerWrapper)
                self.assertEqual(tokenizer.encode(sample), expected.encode(sample))

    def test_unregistered_model_never_uses_deprecated_non_strict(self):
        """The deprecated strict=False lookup returns wrong tokenizer families; it must never run."""
        MistralTokenizer, real = self._real_from_model()

        def fail_strict_for(model, strict=True):
            if model == "mistral-large-4":
                raise ValueError("Unrecognized model")
            return real(model, strict=strict)

        with mock.patch.object(MistralTokenizer, "from_model", side_effect=fail_strict_for) as m:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                tokenizer = get_tokenizer_for_model("mistral-large-4")
        self.assertIsInstance(tokenizer, MistralTokenizerWrapper)
        for call in m.call_args_list:
            self.assertNotIn(False, call.kwargs.values())
            self.assertNotEqual(False, call.args[-1] if call.args else True)
        self.assertFalse([w for w in caught if issubclass(w.category, FutureWarning) and "strict" in str(w.message)])

    def test_tokenizers_are_cached_per_model(self):
        first = get_tokenizer_for_model("mistral-large-4")
        second = get_tokenizer_for_model("mistral-large-4")
        self.assertIs(first, second)
        self.assertEqual(len(_tokenizer_cache), 1)

    def test_non_mistral_model_uses_tiktoken_map(self):
        self.assertEqual(get_tokenizer_for_model("gpt-4o-mini").name, "o200k_base")
        self.assertNotIn("gpt-4o-mini", _tokenizer_cache)

    def test_registry_failure_falls_back_to_tiktoken(self):
        MistralTokenizer, _ = self._real_from_model()
        with mock.patch.object(MistralTokenizer, "from_model", side_effect=ValueError("Unrecognized model")):
            tokenizer = get_tokenizer_for_model("mistral-large-4")
        self.assertEqual(tokenizer.name, "cl100k_base")
        # The failed resolution must not be cached as a Mistral wrapper.
        self.assertNotIn("mistral-large-4", _tokenizer_cache)

    def test_cache_is_keyed_by_registry_id_not_by_caller_string(self):
        """Arbitrary strings containing "mistral" (BYOK model names, the playground
        model field) must not create entries: each loaded tokenizer is ~60 MiB."""
        MistralTokenizer, real = self._real_from_model()
        loaded = []

        def counting(model, strict=True):
            tokenizer = real(model, strict=strict)
            loaded.append(model)
            return tokenizer

        with mock.patch.object(MistralTokenizer, "from_model", side_effect=counting):
            wrappers = {id(get_tokenizer_for_model(f"mistral-made-up-{i}")) for i in range(50)}
        self.assertEqual(len(wrappers), 1)
        self.assertEqual(loaded, ["mistral-small-2409"])
        self.assertEqual(list(_tokenizer_cache), ["mistral-small-2409"])

    def test_large_family_aliases_share_one_tokenizer(self):
        first = get_tokenizer_for_model("mistral-large-4")
        for alias in ("mistral-large-latest", "mistral-large-4-0", "MISTRAL-LARGE-5"):
            self.assertIs(get_tokenizer_for_model(alias), first)
        self.assertEqual(list(_tokenizer_cache), ["mistral-large-2411"])

    def test_cache_is_bounded_and_evicts_least_recently_used(self):
        registered = ["mistral-small-2409", "mistral-large-2411", "mistral-large-2407"]
        with mock.patch.object(tokenizer_factory, "_TOKENIZER_CACHE_MAX", 2):
            get_tokenizer_for_model(registered[0])
            get_tokenizer_for_model(registered[1])
            get_tokenizer_for_model(registered[0])  # refresh: registered[1] is now the oldest
            get_tokenizer_for_model(registered[2])
        self.assertEqual(list(_tokenizer_cache), [registered[0], registered[2]])
