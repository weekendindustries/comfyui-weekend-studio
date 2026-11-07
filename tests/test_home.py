"""The pack's own folder (home.py, 0.3.0): outside ComfyUI's served user folder, one per install,
private, and the 0.2.x files moved out of user/default/weekend-studio/ on the first start."""
import json
import os
import stat
import sys
import tempfile
import unittest

from weekend_studio.credentials import Credentials
from weekend_studio.home import Home, config_base, home_dir, install_key, write_private_json
from weekend_studio.store import WorkflowStore
from tests.helpers import fx


class Paths(unittest.TestCase):
    def test_per_os_folders_are_outside_the_comfyui_tree(self):
        self.assertEqual(config_base({"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}, "win32", "C:\\Users\\x"),
                         os.path.join(r"C:\Users\x\AppData\Local", "WeekendStudio", "ComfyUI"))
        self.assertEqual(config_base({}, "darwin", "/Users/x"),
                         os.path.join("/Users/x", "Library", "Application Support", "Weekend Studio", "ComfyUI"))
        self.assertEqual(config_base({}, "linux", "/home/x"), os.path.join("/home/x", ".config", "weekend-studio", "comfyui"))
        self.assertEqual(config_base({"XDG_CONFIG_HOME": "/cfg"}, "linux", "/home/x"), os.path.join("/cfg", "weekend-studio", "comfyui"))
        d = home_dir("/opt/ComfyUI", env={}, platform="linux", home="/home/x")
        self.assertFalse(d.startswith("/opt/ComfyUI"), "never inside ComfyUI")
        self.assertTrue(d.endswith(install_key("/opt/ComfyUI")))

    def test_two_installs_never_share_a_folder(self):
        a = home_dir("/opt/ComfyUI", env={}, platform="linux", home="/home/x")
        b = home_dir("/opt/ComfyUI-2", env={}, platform="linux", home="/home/x")
        self.assertNotEqual(a, b)

    def test_no_folder_from_the_environment(self):
        # 0.3.2: the pack's own folder never comes from an environment variable (only the OS's folder names do).
        self.assertEqual(home_dir("/opt/ComfyUI", env={"WEEKEND_STUDIO_HOME": "/data/ws"}, platform="linux", home="/home/x"),
                         home_dir("/opt/ComfyUI", env={}, platform="linux", home="/home/x"))


class HomeFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Home(os.path.join(self.tmp.name, "home"), "/opt/ComfyUI")
        self.old = os.path.join(self.tmp.name, "ComfyUI", "user", "default", "weekend-studio")

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_id_is_random_and_kept(self):
        a = self.home.install_id()
        self.assertRegex(a, r"^[0-9a-f]{16}$")
        self.assertEqual(Home(self.home.root).install_id(), a)
        other = Home(os.path.join(self.tmp.name, "other")).install_id()
        self.assertNotEqual(a, other)

    def test_switches_start_off(self):
        self.assertEqual(self.home.settings(), {"sameComputer": False, "autoSync": False})
        self.assertEqual(self.home.update_settings({"sameComputer": True, "bogus": True, "autoSync": "yes"}),
                         {"sameComputer": True, "autoSync": False})
        self.assertEqual(Home(self.home.root).settings()["sameComputer"], True)

    def test_hand_edited_keys(self):
        # localPort and allowedHosts replace 0.3.1's environment variables (0.3.2): read from
        # settings.json, bad values ignored, and the tab's switches never wipe them.
        self.assertEqual((self.home.local_port(), self.home.allowed_hosts()), (None, []))
        write_private_json(self.home.settings_file, {"localPort": 9200, "allowedHosts": ["studio-pc.local", " ", 7]})
        self.assertEqual((self.home.local_port(), self.home.allowed_hosts()), (9200, ["studio-pc.local"]))
        self.home.update_settings({"sameComputer": True})
        self.assertEqual((self.home.local_port(), self.home.allowed_hosts()), (9200, ["studio-pc.local"]))
        for bad in (80, "9200", True, 70000):
            write_private_json(self.home.settings_file, {"localPort": bad, "allowedHosts": "a.local, b.local"})
            self.assertIsNone(self.home.local_port(), bad)
        self.assertEqual(self.home.allowed_hosts(), ["a.local", "b.local"])

    @unittest.skipIf(sys.platform.startswith("win"), "POSIX modes")
    def test_private(self):
        self.home.install_id()
        Credentials(self.home.machine_file).save({"machineToken": "wsm_x", "machineId": "m"})
        self.assertEqual(stat.S_IMODE(os.stat(self.home.root).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.home.machine_file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.home.install_file).st_mode), 0o600)

    def test_0_2_files_move_out_of_the_served_folder(self):
        os.makedirs(self.old)
        old_store = WorkflowStore(self.old)
        rec = old_store.publish("Portrait", fx("txt2img.api.json"))
        with open(os.path.join(self.old, ".machine.json"), "w") as f:
            json.dump({"machineToken": "wsm_" + "a" * 43, "machineId": "m1", "name": "PC"}, f)
        with open(os.path.join(self.old, ".tmp-abc.json"), "w") as f:
            f.write('{"machineToken": "half')
        moved = self.home.migrate_from(self.old)
        self.assertEqual(moved, {"token": True, "workflows": 1})
        self.assertFalse(os.path.exists(self.old), "the served folder is gone")
        self.assertEqual(Credentials(self.home.machine_file).load()["machineId"], "m1")
        self.assertEqual(WorkflowStore(self.home.workflows_dir).get(rec["id"])["name"], "Portrait")
        self.assertEqual(self.home.migrate_from(self.old), {"token": False, "workflows": 0}, "nothing left to move")

    def test_a_token_already_in_the_new_place_wins_and_the_old_one_still_goes(self):
        os.makedirs(self.old)
        Credentials(self.home.machine_file).save({"machineToken": "wsm_new", "machineId": "new"})
        with open(os.path.join(self.old, ".machine.json"), "w") as f:
            json.dump({"machineToken": "wsm_old", "machineId": "old"}, f)
        self.home.migrate_from(self.old)
        self.assertEqual(Credentials(self.home.machine_file).load()["machineId"], "new")
        self.assertFalse(os.path.exists(os.path.join(self.old, ".machine.json")))

    def test_other_files_in_the_old_folder_stay(self):
        os.makedirs(self.old)
        with open(os.path.join(self.old, "notes.txt"), "w") as f:
            f.write("mine")
        self.home.migrate_from(self.old)
        self.assertTrue(os.path.exists(os.path.join(self.old, "notes.txt")))


if __name__ == "__main__":
    unittest.main()
