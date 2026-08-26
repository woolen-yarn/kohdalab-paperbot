import unittest
from unittest import mock

from rag_poc import ollama_client


class OllamaClientTests(unittest.TestCase):
    def test_generate_uses_grounded_generation_settings(self):
        with mock.patch.dict(ollama_client.os.environ, {}, clear=True):
            with mock.patch.object(
                ollama_client,
                "_post_json",
                return_value={"response": "answer"},
            ) as post:
                answer = ollama_client.generate("question", "gemma4:12b")

        self.assertEqual(answer, "answer")
        post.assert_called_once_with(
            "/api/generate",
            {
                "model": "gemma4:12b",
                "prompt": "question",
                "stream": False,
                "think": False,
                "keep_alive": "10m",
                "options": {"num_ctx": 8192, "temperature": 0.2},
            },
            timeout=240,
        )

    def test_generate_keeps_gpt_oss_reasoning_compatible(self):
        with mock.patch.dict(
            ollama_client.os.environ,
            {"OLLAMA_THINK": "false"},
            clear=True,
        ):
            payload = ollama_client._generation_payload("gpt-oss:20b")

        self.assertEqual(payload["think"], "low")

    def test_generate_falls_back_to_chat_with_same_settings(self):
        with mock.patch.dict(ollama_client.os.environ, {}, clear=True):
            with mock.patch.object(
                ollama_client,
                "_post_json",
                side_effect=[{"response": ""}, {"message": {"content": "chat answer"}}],
            ) as post:
                answer = ollama_client.generate("question", "gemma4:12b", timeout=30)

        self.assertEqual(answer, "chat answer")
        self.assertEqual(post.call_count, 2)
        chat_payload = post.call_args_list[1].args[1]
        self.assertEqual(chat_payload["messages"], [{"role": "user", "content": "question"}])
        self.assertEqual(chat_payload["options"], {"num_ctx": 8192, "temperature": 0.2})
        self.assertFalse(chat_payload["think"])

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
