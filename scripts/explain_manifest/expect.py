import os
import re
import sys
import time
import atexit
import difflib
import inspect
import datetime
import subprocess
from inspect import getframeinfo

from globmatch import glob_match

import suites


def glob_match_ignore_slash(path, globs):
    if path.startswith("/"):
        path = path[1:]
    globs = list(globs)
    for i, g in enumerate(globs):
        if g.startswith("/"):
            globs[i] = g[1:]

    return glob_match(path, globs)


def write_color(color):
    term_colors = {
        "red": 31,
        "green": 32,
        "yellow": 33,
        "blue": 34,
        "magenta": 35,
        "cyan": 36,
        "white": 37,
    }

    def decorator(fn):
        def wrapper(self, *args):
            if color not in term_colors:
                raise ValueError(f"unknown color {color}")
            sys.stdout.write('\033[%dm' % term_colors[color])
            r = fn(self, *args)
            sys.stdout.write('\033[0m')
            return r

        return wrapper

    return decorator


def write_block_desc(desc_verb):
    def decorator(fn):
        def wrapper(self, suite: ExpectSuite, *args):
            ExpectChain._log(f"[INFO] start to {desc_verb} of suite {suite.name}")
            start_time = time.time()
            r = fn(self, suite, *args)
            duration = time.time() - start_time
            ExpectChain._log("[INFO] finish to %s of suite %s in %.2fms" % (
                desc_verb, suite.name, duration*1000))
            return r

        return wrapper

    return decorator


class ExpectSuite():
    def __init__(self, name, manifest,
                 libc_max_version=None, libstdcpp_max_version=None, use_rpath=False, fips=False, extra_tests=[]):
        self.name = name
        self.manifest = manifest
        self.libc_max_version = libc_max_version
        self.libstdcpp_max_version = libstdcpp_max_version
        self.use_rpath = use_rpath
        self.fips = fips
        self.extra_tests = extra_tests


class ExpectChain():
    def __init__(self, infos):
        self._infos = infos
        self._all_failures = []
        self._reset()
        self.verbs = ("does_not", "equal", "match", "contain",
                      "contain_match", "less_than", "greater_than")
        atexit.register(self._print_all_fails)

    def _reset(self):
        # clear states
        self._logical_reverse = False
        self._files = []
        self._msg = ""
        self._title_shown = False
        self._checks_count = 0
        self._failures_count = 0
        self._last_attribute = None

    def _ctx_info(self):
        f = inspect.currentframe().f_back.f_back.f_back.f_back
        fn_rel = os.path.relpath(getframeinfo(f).filename, os.getcwd())

        return "%s:%d" % (fn_rel, f.f_lineno)

    @classmethod
    def _log(cls, *args):
        sys.stdout.write(f" {datetime.datetime.now().strftime('%b %d %X')} ")
        print(*args)

    @write_color("white")
    def _print_title(self):
        if self._title_shown:
            return
        self._log(f"[TEST] {self._ctx_info()}: {self._msg}")
        self._title_shown = True

    @write_color("red")
    def _print_fail(self, msg):
        self._log(f"[FAIL] {msg}")
        self._all_failures.append(f"{self._ctx_info()}: {msg}")
        self._failures_count += 1

    @write_color("green")
    def _print_ok(self, msg):
        self._log(f"[OK  ] {msg}")

    @write_color("yellow")
    def _print_error(self, msg):
        self._log(f"[FAIL] {msg}")

    def _print_result(self):
        if self._checks_count == 0:
            return
        if self._failures_count == 0:
            self._print_ok("%d check(s) passed for %d file(s)" %
                           (self._checks_count, len(self._files)))
        else:
            self._print_error("%d/%d check(s) failed for %d file(s)" % (
                self._failures_count, self._checks_count, len(self._files)))

    @write_color("red")
    def _print_all_fails(self):
        # flush pending result
        self._print_result()

        if self._all_failures:
            self._print_error(
                "Following failure(s) occured:\n" + "\n".join(self._all_failures))
            os._exit(1)

    def _compare(self, attr, fn):
        self._checks_count += 1
        for f in self._files:
            if not hasattr(f, attr):
                continue  # accept missing attribute for now
            v = getattr(f, attr)
            if self._key_name and isinstance(v, dict):
                # TODO: explict flag to accept missing key
                if self._key_name not in v:
                    return True
                v = v[self._key_name]
            (ok, err_template) = fn(v)
            if ok == self._logical_reverse:
                _not = "not"
                if self._logical_reverse:
                    _not = "actually"

                self._print_fail("file %s <%s>: %s" % (
                    f.relpath, attr, err_template.format(v, NOT=_not)
                ))
                return False
        return True

    def _exist(self):
        self._checks_count += 1
        matched_files_count = len(self._files)
        if (matched_files_count > 0) == self._logical_reverse:
            self._print_fail("found %d files matching %s" % (
                matched_files_count, self._path_glob))
        return self

    # following are verbs

    def _equal(self, attr, expect):
        return self._compare(attr, lambda a: (a == expect, "'{}' does {NOT} equal to '%s'" % expect))

    def _match(self, attr, expect):
        return self._compare(attr, lambda a: (re.match(expect, a), "'{}' does {NOT} match '%s'" % expect))

    def _less_than(self, attr, expect):
        def fn(a):
            ll = sorted(list(a))[-1] if isinstance(a, list) else a
            return ll < expect, "'{}' is {NOT} less than %s" % expect

        return self._compare(attr, fn)

    def _greater_than(self, attr, expect):
        def fn(a):
            ll = sorted(list(a))[0] if isinstance(a, list) else a
            return ll > expect, "'{}' is {NOT} greater than %s" % expect

        return self._compare(attr, fn)

    def _contain(self, attr, expect):
        def fn(a):
            if not isinstance(a, list):
                return False, f"{attr} is not a list"
            ok = expect in a
            msg = "'%s' is {NOT} found in the list" % expect
            if not ok:
                if len(a) == 0:
                    msg = f"'{attr}' is empty"
                else:
                    closest = difflib.get_close_matches(expect, a, 1)
                    if len(closest) > 0:
                        msg += f", did you mean '{closest[0]}'?"
            return ok, msg
                # should not reach here

        return self._compare(attr, fn)

    def _contain_match(self, attr, expect):
        def fn(a):
            if isinstance(a, list):
                msg = "'%s' is {NOT} found in the list" % expect
                for e in a:
                    if re.match(expect, e):
                        return True, msg
                return False, msg
            else:
                return False, f"'{attr}' is not a list"

        return self._compare(attr, fn)

    # following are public methods (test functions)
    def to(self):
        # does nothing, but helps to construct English
        return self

    def expect(self, path_glob, msg):
        # lazy print last test result
        self._print_result()
        # reset states
        self._reset()

        self._msg = msg
        self._print_title()

        self._path_glob = path_glob
        for f in self._infos:
            if glob_match_ignore_slash(f.relpath, [path_glob]):
                self._files.append(f)
        return self

    def do_not(self):
        self._logical_reverse = True
        return self

    def does_not(self):
        return self.do_not()

    def is_not(self):
        return self.do_not()

    # access the value of the dict of key "key"
    def key(self, key):
        self._key_name = key
        return self

    def exist(self):
        return self._exist()

    def exists(self):
        return self._exist()

    def __getattr__(self, name):
        dummy_call = lambda *x: self

        verb = re.findall("^(.*?)(?:s|es)?$", name)[0]
        if verb not in self.verbs:
            # XXX: hack to support rpath/runpath
            if self._current_suite.use_rpath and name == "runpath":
                name = "rpath"
            elif not self._current_suite.use_rpath and name == "rpath":
                name = "runpath"

            self._last_attribute = name
            # reset
            self._logical_reverse = False
            self._key_name = None
            return self

        if not self._last_attribute:
            self._print_error("attribute is not set before verb \"%s\"" % name)
            return dummy_call

        attr = self._last_attribute
        for f in self._files:
            if not hasattr(f, attr):
                self._print_error(
                    "\"%s\" expect \"%s\" attribute to be present, but it's not for %s" % (name, attr, f.relpath))
                return dummy_call

        def cls(expect):
            getattr(self, f"_{verb}")(attr, expect)
            return self

        return cls

    @write_block_desc("compare manifest")
    def compare_manifest(self, suite: ExpectSuite, manifest: str):
        self._current_suite = suite

        if not suite.manifest:
            self._print_error(f"manifest is not set for suite {suite.name}")
        else:
            diff_result = subprocess.run(
                ['diff', "-BbNaur", suite.manifest, '-'], input=manifest, stdout=subprocess.PIPE)
            if diff_result.returncode != 0:
                self._print_fail("manifest is not up-to-date:")
                if diff_result.stdout:
                    print(diff_result.stdout.decode())
                if diff_result.stderr:
                    print(diff_result.stderr.decode())

    @write_block_desc("run test suite")
    def run(self, suite: ExpectSuite):
        self._current_suite = suite

        suites.common_suites(self.expect, suite.fips)
        suites.libc_libcpp_suites(
            self.expect, suite.libc_max_version, suite.libstdcpp_max_version)

        if suite.extra_tests:
            for s in suite.extra_tests:
                s(self.expect)

        self._print_result()  # cleanup the lazy buffer
