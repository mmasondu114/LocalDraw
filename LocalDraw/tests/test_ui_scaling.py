import unittest

from comic_workflow.qt_app.scaling import auto_scale, resolve_scale


class UIScalingTests(unittest.TestCase):
    def test_auto_scale_common_screens(self):
        # logical work area after Windows display scaling
        self.assertEqual(auto_scale(2752, 1104), 1.0)  # 3440x1440 @125%
        self.assertEqual(auto_scale(2048, 1104), 1.0)  # 2560x1440 @125%
        self.assertEqual(auto_scale(1920, 1032), 1.0)  # 1920x1080 @100%
        self.assertEqual(auto_scale(2560, 1392), 1.15)  # 2560x1440 @100%
        self.assertEqual(auto_scale(3440, 1392), 1.15)  # 3440x1440 @100%
        self.assertEqual(auto_scale(1536, 816), 0.9)  # 1920x1080 @125% laptop
        self.assertEqual(auto_scale(1366, 728), 0.9)

    def test_manual_setting_overrides_auto(self):
        self.assertEqual(resolve_scale("1.25", (2752, 1104)), 1.25)
        self.assertEqual(resolve_scale("auto", (1536, 816)), 0.9)
        self.assertEqual(resolve_scale("auto", None), 1.0)
        self.assertEqual(resolve_scale("bogus", (2560, 1392)), 1.15)


if __name__ == "__main__":
    unittest.main()
