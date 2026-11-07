"""Who may use /weekend-studio/* (guard.py, 0.3.0, security review B1)."""
import unittest

from weekend_studio.guard import allowed_names, check, split_host

LOOP = allowed_names(None)


def h(**kw):
    base = {"X-Weekend-Studio": "1", "Host": "127.0.0.1:8188"}
    base.update({k.replace("_", "-"): v for k, v in kw.items() if v is not None})
    for k in [k for k, v in kw.items() if v is None]:
        base.pop(k.replace("_", "-"), None)
    return base


class Guard(unittest.TestCase):
    def test_the_tab_itself(self):
        self.assertIsNone(check(h(), LOOP))
        self.assertIsNone(check(h(Origin="http://127.0.0.1:8188", Sec_Fetch_Site="same-origin"), LOOP))
        self.assertIsNone(check(h(Host="localhost:8188", Origin="http://localhost:8188"), LOOP))
        self.assertIsNone(check(h(Host="[::1]:8188", Origin="http://[::1]:8188"), LOOP))

    def test_another_site_cannot(self):
        # No custom header: what a cross-site <form>, <img> or a simple fetch looks like.
        self.assertIsNotNone(check(h(X_Weekend_Studio=None, Origin="https://evil.example"), LOOP))
        self.assertIsNotNone(check(h(X_Weekend_Studio=None), LOOP))
        # With the header (only possible after a preflight ComfyUI doesn't allow), the Origin still fails.
        self.assertIsNotNone(check(h(Origin="https://evil.example"), LOOP))
        self.assertIsNotNone(check(h(Origin="null"), LOOP))
        self.assertIsNotNone(check(h(Origin="http://127.0.0.1:9999"), LOOP), "another port is another origin")
        self.assertIsNotNone(check(h(Sec_Fetch_Site="cross-site"), LOOP))

    def test_dns_rebinding_cannot(self):
        # The attacker's name resolves to 127.0.0.1: same-origin from the browser's view, so the
        # header and Origin pass — the Host doesn't.
        self.assertIsNotNone(check(h(Host="rebind.evil.example:8188", Origin="http://rebind.evil.example:8188"), LOOP))

    def test_lan_hosts_only_when_named(self):
        self.assertIsNotNone(check(h(Host="192.168.1.5:8188"), allowed_names("0.0.0.0,::")), "--listen alone is not a name")
        self.assertIsNone(check(h(Host="192.168.1.5:8188"), allowed_names("192.168.1.5")))
        named = allowed_names("0.0.0.0", ["studio-pc.local", " comfy.example.com:443"])
        self.assertIsNone(check(h(Host="studio-pc.local:8188", Origin="http://studio-pc.local:8188"), named))
        self.assertIsNone(check(h(Host="comfy.example.com", Origin="https://comfy.example.com"), named))

    def test_split_host(self):
        self.assertEqual(split_host("[::1]:8188"), ("::1", "8188"))
        self.assertEqual(split_host("LocalHost:1"), ("localhost", "1"))
        self.assertEqual(split_host("example.com"), ("example.com", ""))
        self.assertEqual(split_host(None), ("", ""))


if __name__ == "__main__":
    unittest.main()
