import sys, unittest
from pathlib import Path
from loguru import logger
logger.remove()
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]))
raise SystemExit(not result.wasSuccessful())
