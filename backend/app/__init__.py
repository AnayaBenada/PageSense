# faiss-cpu and torch wheels each bundle their own libomp on macOS. Two OpenMP
# runtimes in one process abort or segfault unless duplicates are allowed, so
# allow them here (before either library loads) and keep faiss single-threaded.
import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

# Load torch's libomp first; if faiss's copy initializes first, torch segfaults.
import torch  # noqa: E402,F401
