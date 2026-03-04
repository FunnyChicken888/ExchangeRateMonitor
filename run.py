"""
ExchangeRateMonitor — 頂層入口點

用法：
    python run.py
"""

import os
import sys

# 確保專案根目錄在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.main import main

if __name__ == "__main__":
    main()
