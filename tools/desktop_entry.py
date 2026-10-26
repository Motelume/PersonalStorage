import multiprocessing
from personalstorage.desktop import entry

if __name__=='__main__':
    multiprocessing.freeze_support()
    raise SystemExit(entry())
