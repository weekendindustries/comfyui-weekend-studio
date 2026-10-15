"""The pure parts of the ComfyUI client: reading its socket messages and its refusals."""
import unittest

from weekend_studio.comfy_client import ComfyClient, prompt_error


class Events(unittest.TestCase):
    def setUp(self):
        self.c = ComfyClient("http://127.0.0.1:8188")

    def ev(self, t, **data):
        return self.c._event({"type": t, "data": data}, "p1")

    def test_messages_for_our_prompt(self):
        self.assertEqual(self.ev("progress", value=3, max=20, node="3", prompt_id="p1"),
                         {"type": "progress", "value": 3, "max": 20, "node": "3"})
        self.assertEqual(self.ev("executing", node="8", prompt_id="p1"), {"type": "executing", "node": "8"})
        self.assertEqual(self.ev("executing", node=None, prompt_id="p1"), {"type": "done"})
        self.assertEqual(self.ev("execution_success", prompt_id="p1"), {"type": "done"})
        self.assertEqual(self.ev("execution_interrupted", prompt_id="p1"), {"type": "interrupted"})
        err = self.ev("execution_error", prompt_id="p1", node_id="3", node_type="KSampler", exception_message="CUDA out of memory")
        self.assertEqual(err, {"type": "error", "message": "CUDA out of memory", "node": "3", "node_type": "KSampler"})

    def test_other_prompts_and_noise_are_ignored(self):
        self.assertIsNone(self.ev("progress", value=1, max=2, prompt_id="someone-else"))
        self.assertIsNone(self.ev("execution_success", prompt_id="someone-else"))
        self.assertIsNone(self.ev("status", status={"exec_info": {"queue_remaining": 1}}))
        self.assertIsNone(self.ev("executed", node="9", prompt_id="p1"))


class Refusals(unittest.TestCase):
    def test_node_errors_are_named(self):
        body = {"error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
                "node_errors": {"4": {"class_type": "CheckpointLoaderSimple",
                                      "errors": [{"message": "Value not in list", "details": "ckpt_name: 'gone.safetensors' not in [...]"}]}}}
        self.assertEqual(prompt_error(body), "Prompt outputs failed validation — CheckpointLoaderSimple: ckpt_name: 'gone.safetensors' not in [...]")
        self.assertEqual(prompt_error(None), "ComfyUI refused the workflow")
        self.assertEqual(prompt_error({"error": "no prompt"}), "no prompt")


if __name__ == "__main__":
    unittest.main()


class HistoryRace(unittest.IsolatedAsyncioTestCase):
    async def test_outputs_waits_for_comfyui_to_file_the_run(self):
        import weekend_studio.comfy_client as cc
        c = ComfyClient("http://127.0.0.1:8188")
        answers = [None, None, {"status": {"completed": False}, "outputs": {}},
                   {"status": {"completed": True}, "outputs": {"20": {"images": [{"filename": "a.png"}]}}}]

        async def history(_pid):
            return answers.pop(0) if answers else None
        c._history = history
        step, cc.HISTORY_WAIT_STEP = cc.HISTORY_WAIT_STEP, 0
        try:
            self.assertEqual(await c.outputs("p1"), {"20": {"images": [{"filename": "a.png"}]}})
        finally:
            cc.HISTORY_WAIT_STEP = step

    async def test_gives_up_quietly(self):
        import weekend_studio.comfy_client as cc
        c = ComfyClient("http://127.0.0.1:8188")

        async def history(_pid):
            return None
        c._history = history
        step, cc.HISTORY_WAIT_STEP = cc.HISTORY_WAIT_STEP, 0
        try:
            self.assertEqual(await c.outputs("p1"), {})
        finally:
            cc.HISTORY_WAIT_STEP = step
