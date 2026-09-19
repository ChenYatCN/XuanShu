import unittest
from src.drop_logger import filter_drops


class DropLoggerTests(unittest.TestCase):
    # Generic fixtures and confirmed in-game mount markup.
    def test_mount_and_existing_types(self):
        for kind in ('Mount', 'Pet', 'Reagent'):
            line = f'<img;Art_Chat_System.dds> <item;{kind}> 测试物品</item>'
            self.assertEqual(filter_drops([line]), ['测试物品'])

    def test_missing_type(self):
        self.assertEqual(filter_drops(['<img;Art_Chat_System.dds> <text> 通知</text>']), [])

    def test_confirmed_mount_message(self):
        line = ('<color;00FF00><image;Art/Art_Chat_System.dds;24;24;FFFFFFFF> '
                '<image;Mount> 骨龙(永久)</color>')
        self.assertEqual(filter_drops([line]), ['骨龙(永久)'])
