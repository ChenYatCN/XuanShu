DEFAULT_LOCATIONS = 'Kembaalung Village:Hollow Mountain|Dungeon|\nThe Commons:|Location|2859.009, 3176.191, 2.566:-2385.548, 2603.149, 258.397:-6609.876, -1789.233, 257.186:4018.403, -3374.743, 2.572:8274.751, -1065.662, 2.673:4197.647, 141.662, -67.268\nNightside:|Location|\nUnicorn Way:|Location|2624.409, -863.609, 30.012:2403.999, 1815.999, 28.000:-1216.323, 1816.535, 30.000:-1477.465, -903.326, 30.013\nShopping District:|Location|-2104.502, -1050.601, 0.034:-5347.831, -986.148, 0.034:-4389.247, -5275.093, 0.034:1005.579, -5326.548, 0.034:5666.464, -4549.839, 0.034:2472.551, -407.832, 0.034\nRavenwood:|Location|2747.231, -1217.251, -120.185:4761.226, 2572.452, 68.307:1071.232, 7958.425, 1.698:-1268.346, 4181.587, 84.866:-5046.718, 4795.736, 1.732:-2514.484, 1600.681, -28.785:-730.286, -583.817, -1.739\nOlde Town:|Location|1385.107, -615.919, 2.181:-2170.578, 3212.091, 32.200:-4251.745, 1839.398, -597.799:-623.398, -3276.836, -866.741:-5501.576, -1158.035, -1397.835:-11275.824, -2945.619, -2197.799:-6300.162, -4500.042, -1615.882\nTriton Avenue:|Location|-34.650, -1642.165, 63.335:6734.577, -6391.921, 57.234:5765.971, -10032.519, 33.815:-12129.188, -3750.990, -1394.233:-16589.451, -754.283, -301.103:-18875.710, -96.229, 0.270:-19604.597, -4062.864, 28.895:-30917.304, -1692.070, 54.048:-33172.269, 4813.919, 31.909:-28510.392, 4381.841, 31.909:-36916.664, 1981.664, 1.229:-34996.398, -8481.071, 35.273:-39170.871, -7553.402, 35.273:-36892.128, -1950.100, 64.840\nHaunted Cave:|Location|-259.899, -512.090, -0.002:13640.901, -4142.243, -70.002:10942.541, 3021.435, 30.005:11957.141, 9936.038, 9.568:7967.318, 12248.067, -133.619:\nDorm Room:|Location|2624.409, -863.609, 30.012:2403.999, 1815.999, 28.000:-1216.323, 1816.535, 30.000:-1477.465, -903.326, 30.013\nCastle Darkmoor:|Location|3919.805, -658.447, -168.110:7122.881, 2689.751, -167.051:6103.088, 4508.456, -156.019:-5853.828, 483.841, -139.605:'
# Exact display names from Locale_en-US-root.wad, Locale/en-US/WizardZone.lang
# checked 2026-09-14. Third field is the displayed translation, not the comment.
LOCATION_ALIASES = (
    ('Kembaalung Village', '肯巴隆村'),  # 00001111
    ('Hollow Mountain', '中空的山'),  # 00001118
    ('The Commons', '广场'),  # TheCommons
    ('Nightside', '黑夜之地'),  # Nightside
    ('Unicorn Way', '独角兽大道'),  # UnicornWay
    ('Shopping District', '购物街'),  # ShoppingDistrict
    ('Ravenwood', '拉文霍德'),  # Ravenwood
    ('Olde Town', '奥尔德镇'),  # OldeTown
    ('Triton Avenue', '海神大街'),  # TritonAvenue
    ('Haunted Cave', '闹鬼洞穴'),  # HauntedCave
    ('Dorm Room', '宿舍'),  # 00000510
    ('Castle Darkmoor', '暗夜沼泽城堡(Castle Darkmoor)'),  # 00001110
)


def expand_location_aliases(text):
    """Add verified aliases without changing coordinates or explicit overrides."""
    lines = text.splitlines()
    explicit = {name.strip() for line in lines
                for name in line.split('|')[0].split(':') if name.strip()}
    result = []
    for line in lines:
        parts = line.split('|')
        if len(parts) == 3:
            names = [name.strip() for name in parts[0].split(':') if name.strip()]
            for group in LOCATION_ALIASES:
                if any(name in group for name in names):
                    names.extend(name for name in group if name not in names and name not in explicit)
            parts[0] = ':'.join(names)
            line = '|'.join(parts)
        result.append(line)
    return '\n'.join(result)


DEFAULT_LOCATIONS = expand_location_aliases(DEFAULT_LOCATIONS)
