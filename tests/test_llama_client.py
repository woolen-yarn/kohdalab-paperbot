import unittest
from unittest import mock

from rag_poc import llama_client


class LlamaClientTests(unittest.TestCase):
    def test_generate_uses_openai_compatible_chat_endpoint(self):
        response = {"choices": [{"message": {"content": "answer"}}]}
        with mock.patch.dict(llama_client.os.environ, {}, clear=True):
            with mock.patch.object(llama_client, "_post_json", return_value=response) as post:
                answer = llama_client.generate("question", "local-model", timeout=30)

        self.assertEqual(answer, "answer")
        kind, path, payload, timeout = post.call_args.args
        self.assertEqual((kind, path, timeout), ("chat", "/chat/completions", 30))
        self.assertEqual(payload["messages"], [{"role": "user", "content": "question"}])
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["reasoning_budget"], 0)
        self.assertFalse(payload["chat_template_kwargs"]["enable_thinking"])

    def test_generate_can_enable_a_bounded_reasoning_budget(self):
        response = {"choices": [{"message": {"content": "answer"}}]}
        with mock.patch.dict(
            llama_client.os.environ,
            {"LLAMA_REASONING_BUDGET": "256", "LLAMA_MAX_TOKENS": "768"},
            clear=True,
        ):
            with mock.patch.object(llama_client, "_post_json", return_value=response) as post:
                llama_client.generate("question", "reasoner")

        payload = post.call_args.args[2]
        self.assertEqual(payload["reasoning_budget"], 256)
        self.assertTrue(payload["chat_template_kwargs"]["enable_thinking"])

    def test_embed_many_orders_openai_embedding_rows_by_index(self):
        response = {
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]
        }
        with mock.patch.object(llama_client, "_post_json", return_value=response) as post:
            vectors = llama_client.embed_many(["title", "abstract"], "embed-model")

        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        post.assert_called_once_with(
            "embedding",
            "/embeddings",
            {
                "model": "embed-model",
                "input": [
                    f"{llama_client.DEFAULT_QUERY_INSTRUCTION}title",
                    f"{llama_client.DEFAULT_QUERY_INSTRUCTION}abstract",
                ],
                "encoding_format": "float",
            },
            180,
        )

    def test_embed_many_rejects_incomplete_batch(self):
        with mock.patch.object(
            llama_client,
            "_post_json",
            return_value={"data": [{"index": 0, "embedding": [1.0]}]},
        ):
            with self.assertRaises(llama_client.ModelServerError):
                llama_client.embed_many(["one", "two"], "embed-model")

    def test_embed_document_does_not_add_query_instruction(self):
        response = {"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        with mock.patch.object(llama_client, "_post_json", return_value=response) as post:
            vector = llama_client.embed_document("paper passage", "embed-model")

        self.assertEqual(vector, [1.0, 0.0])
        self.assertEqual(post.call_args.args[2]["input"], ["paper passage"])


if __name__ == "__main__":
    unittest.main()
