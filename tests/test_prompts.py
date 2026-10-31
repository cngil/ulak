import unittest

from ulak_app import prompts


class PromptTests(unittest.TestCase):
    def test_templates_require_values_and_preserve_literal_data(self):
        with self.assertRaises(KeyError):
            prompts.render_prompt('brief', language='English')
        instructions = 'Keep $language and {braces} literal.'
        rendered = prompts.render_prompt('summary', language='Turkish', max_words=42,
            editorial_preferences=prompts.preferences(instructions))
        self.assertIn('in Turkish', rendered)
        self.assertIn('at most 42 words', rendered)
        self.assertIn(instructions, rendered)
        self.assertIn('{"stories":', prompts.render_prompt('ranking'))
        with self.assertRaises(ValueError):
            prompts.render_prompt('../config.local')

