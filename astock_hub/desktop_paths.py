import os
import sys
from pathlib import Path


def resource_root() -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


def user_data_dir() -> Path:
    override = os.environ.get('QIAODING_DATA_DIR')
    base = Path(override) if override else Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'QiaodingYigu'
    base.mkdir(parents=True, exist_ok=True)
    return base
