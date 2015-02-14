#!/usr/bin/env python3
import os
import sys
import re
import itertools as itools
from collections import defaultdict
from threading import Lock, Thread
from queue import Queue

try:
    from termcolor import colored
except ImportError:
    print("termcolor is not installed; output will be lacking colours" ,file=sys.stderr)
    def colored(*args, **kwargs):
        return args[0]

from elftools.elf.elffile import ELFFile
from elftools.elf.dynamic import DynamicSection, DynamicSegment
from elftools.common.exceptions import ELFError
from elftools.common.py3compat import bytes2str

# utilities
def warn(text):
    warning = colored("Warning: >>", 'red')
    text = colored(text, 'white')
    print(warning, text, file=sys.stderr)

def highlight(text):
    return colored(text, 'white', attrs=['bold', 'dark'])


def walk_multi_dir(dirs):
    yield from itools.chain(*(os.walk(d) for d in dirs))

class BrokenFinder():

    def __init__(self):
        self.found = set()  # 'shared libraries' (could also be symlinks) that we found so far
        self.lib2required_by = defaultdict(list)
        # get all directories in PATH;  if unset, use "/usr/bin" as a default
        self.bindirs = os.environ.get("PATH", "/usr/bin").split(":")
        self.libdirs = ["/usr"]
        self.job_queue = Queue()
        if os.path.exists("/opt"):
            self.libdirs.append("/opt")

    def enumerate_shared_libs(self):
        somatching = re.compile(r""".*\.so\Z # normal shared object
        |.*\.so(\.\d+)+ # versioned shared object""", re.VERBOSE)
        for dpath, dnames, fnames in walk_multi_dir(self.libdirs):
            for fullname, fname in ((os.path.join(dpath, fname),fname) for fname in fnames if re.match(somatching ,fname)):
                self.found.add(fname)
                if not os.path.islink(fullname):
                    yield fullname

    def enumerate_binaries(self):
        for dpath, dnames, fnames in walk_multi_dir(self.bindirs):
            for fname in fnames:
                fullname = os.path.join(dpath, fname)
                if not os.path.islink(fullname):
                    yield fullname

    def collect_needed(self, sofile):
        try:
            with open(sofile, 'rb') as f:
                try:
                    elffile = ELFFile(f)
                    for section in elffile.iter_sections():
                        if not isinstance(section, DynamicSection):
                            continue

                        for tag in section.iter_tags():
                            if tag.entry.d_tag == 'DT_NEEDED':
                                # no race, as there is only one worker
                                # and check waits for the worker to finish
                                # before accessing lib2required
                                self.lib2required_by[bytes2str(tag.needed)].append(sofile)

                except ELFError:
                    pass  # not an ELF file
        except PermissionError:
            warn("Could not open {}; please check permissions".format(sofile))

    def worker(self):
        while True:
            item = self.job_queue.get()
            self.collect_needed(item)
            self.job_queue.task_done()

    def check(self):
        t = Thread(target=self.worker)
        t.daemon = True
        t.start()
        for lib_or_bin in itools.chain(self.enumerate_shared_libs(), self.enumerate_binaries()):
            self.job_queue.put(lib_or_bin)
        self.job_queue.join()
        missing_libs = self.lib2required_by.keys()  - self.found
        if missing_libs:
            warn("The following libraries were not found")
        for missing_lib in (missing_libs):
            print("{} required by: {}".format(highlight(missing_lib), ', '.join(self.lib2required_by[missing_lib])), file=sys.stderr)

if __name__ == "__main__":
    b = BrokenFinder()
    b.check()
