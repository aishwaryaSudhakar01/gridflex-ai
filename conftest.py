# Makes `from src import ...` work from any folder, and pins the working directory to the repo root.
import os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
