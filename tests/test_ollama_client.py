import unittest
from unittest import mock

from rag_poc import ollama_client


class OllamaClientTests(unittest.TestCase):
    def test_embed_many_uses_one_batch_request(self):
        response = {"embeddings": [[1.0, 0.0], [0.0, 1.0]]}
        with mock.patch.object(ollama_client, "_post_json", return_value=response) as post:
            embeddings = ollama_client.embed_many(["title", "abstract"], "model")

        self.assertEqual(embeddings, response["embeddings"])
        post.assert_called_once_with(
            "/api/embed",
            {"model": "model", "input": ["title", "abstract"]},
            timeout=180,
        )

    def test_embed_preserves_single_text_interface(self):
        with mock.patch.object(
            ollama_client,
            "embed_many",
            return_value=[[0.2, 0.8]],
        ) as embed_many:
            embedding = ollama_client.embed("text", "model", timeout=30)

        self.assertEqual(embedding, [0.2, 0.8])
        embed_many.assert_called_once_with(["text"], "model", timeout=30)


if __name__ == "__main__":
    unittest.main()
