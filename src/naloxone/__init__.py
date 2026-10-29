from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("naloxone")
except PackageNotFoundError:
    __version__ = "1.0.0"
