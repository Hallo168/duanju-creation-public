import unittest

from scripts.chatgpt_web_login_probe import (
    extract_verification_code,
    parse_credential_line,
    parse_mail_links,
)


class WebLoginProbeHelpersTest(unittest.TestCase):
    def test_parse_credential_line(self):
        credential = parse_credential_line("user@example.com----chat-pass----mail-key----mail-token")
        self.assertEqual(credential.email, "user@example.com")
        self.assertEqual(credential.chat_password, "chat-pass")
        self.assertEqual(credential.mail_key, "mail-key")
        self.assertEqual(credential.mail_token, "mail-token")

    def test_parse_mail_links_only_returns_chatgpt_messages(self):
        html = """
        <a href="/show_email/user----key/INBOX/8?email_type=ms_imap">Other mail</a>
        <a href="/show_email/user----key/INBOX/9?email_type=ms_imap">Your temporary ChatGPT verification code</a>
        """
        self.assertEqual(parse_mail_links(html), ["/show_email/user----key/INBOX/9?email_type=ms_imap"])

    def test_extract_verification_code_prefers_code_near_label(self):
        text = "Order 12345678. Your ChatGPT verification code is 482913."
        self.assertEqual(extract_verification_code(text), "482913")


if __name__ == "__main__":
    unittest.main()
