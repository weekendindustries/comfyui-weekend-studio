"""The no-account code (local_code.py): where this ComfyUI is, and a secret only its tab shows.
The browser reads the same codes (packages/comfy/localCode.js); packages/comfy/fixtures/local_codes.json holds both."""
import unittest

from weekend_studio import local_code as lc
from tests.helpers import fx


class Code(unittest.TestCase):
    def test_round_trip_every_kind_of_address(self):
        for addr, kind in (("192.168.0.2", 0), ("192.168.255.254", 0), ("10.1.2.3", 1), ("172.16.0.9", 2),
                           ("172.31.255.1", 2), ("100.64.0.1", 3), ("8.8.4.4", 3)):
            for port in (8189, 8193, 8196):
                for secret in (0, 1, 2**64 - 1, lc.new_secret()):
                    code = lc.encode(addr, port, secret)
                    self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
                    self.assertFalse(set(code) & set("01ILO"), code)
                    d = lc.decode(code)
                    self.assertEqual((d["address"], d["port"], d["kind"]), (addr, port, kind))
                    self.assertTrue(lc.matches(code, secret))

    def test_shared_with_the_browser(self):
        for c in fx("local_codes.json")["cases"]:
            self.assertEqual(lc.encode(c["address"], c["port"], int(c["secret"])), c["code"])
            d = lc.decode(c["code"])
            self.assertEqual((d["address"], d["port"], d["kind"], str(d["secret"])), (c["address"], c["port"], c["kind"], c["secretInCode"]))

    def test_another_secret_never_matches(self):
        code = lc.encode("192.168.1.20", 8189, 111)
        self.assertFalse(lc.matches(code, 112))
        self.assertFalse(lc.matches(None, 111))
        self.assertFalse(lc.matches("ABCD-EFGH", 111), "an account code is not this code")

    def test_typing_and_pasting(self):
        code = lc.encode("192.168.1.20", 8190, 99)
        self.assertEqual(lc.decode(code.lower().replace("-", " "))["port"], 8190)
        for bad in ("", "ABCD-EFGH", "ABCD-EFGH-JKMN-PQRS", 12, None):
            self.assertIsNone(lc.decode(bad), bad)

    def test_what_it_cannot_say(self):
        self.assertIsNone(lc.encode("::1", 8189, 1))
        self.assertIsNone(lc.encode("192.168.1.2", 8188, 1))
        self.assertIsNone(lc.encode("192.168.1.2", 8197, 1))
        self.assertIsNone(lc.encode("not an address", 8189, 1))

    def test_enough_secret_left(self):
        self.assertGreater(lc.secret_room(0), 2**38)
        self.assertGreater(lc.secret_room(3), 2**22)
        for kind in range(4):
            self.assertLessEqual(lc.secret_room(kind) * 4 * (1 << lc.KIND_BITS[kind]) * lc.PORT_SPAN, lc.SPACE)


if __name__ == "__main__":
    unittest.main()
