#!/usr/bin/env python3
# vim:fileencoding=utf-8
# License: GPL v3 Copyright: 2016, Kovid Goyal <kovid at kovidgoyal.net>

import atexit
import errno
import fcntl
import math
import os
import re
import string
import sys
from collections import namedtuple
from contextlib import suppress
from functools import lru_cache
from time import monotonic
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from .constants import (
    appname, is_macos, is_wayland, shell_path, supports_primary_selection
)
from .rgb import Color, to_color
if TYPE_CHECKING:
    from .cli import Namespace  # noqa

BASE = os.path.dirname(os.path.abspath(__file__))


def load_shaders(name):
    from .fast_data_types import GLSL_VERSION
    with open(os.path.join(BASE, '{}_vertex.glsl'.format(name))) as f:
        vert = f.read().replace('GLSL_VERSION', str(GLSL_VERSION), 1)
    with open(os.path.join(BASE, '{}_fragment.glsl'.format(name))) as f:
        frag = f.read().replace('GLSL_VERSION', str(GLSL_VERSION), 1)
    return vert, frag


def safe_print(*a, **k):
    with suppress(Exception):
        print(*a, **k)


def log_error(*a, **k):
    from .fast_data_types import log_error_string
    with suppress(Exception):
        msg = k.get('sep', ' ').join(map(str, a)) + k.get('end', '')
        log_error_string(msg.replace('\0', ''))


def ceil_int(x):
    return int(math.ceil(x))


def sanitize_title(x):
    return re.sub(r'\s+', ' ', re.sub(r'[\0-\x19\x80-\x9f]', '', x))


def color_as_int(val):
    return val[0] << 16 | val[1] << 8 | val[2]


def color_from_int(val):
    return Color((val >> 16) & 0xFF, (val >> 8) & 0xFF, val & 0xFF)


def parse_color_set(raw):
    parts = raw.split(';')
    lp = len(parts)
    if lp % 2 != 0:
        return
    for c, spec in [parts[i:i + 2] for i in range(0, len(parts), 2)]:
        try:
            c = int(c)
            if c < 0 or c > 255:
                continue
            if spec == '?':
                yield c, None
            else:
                r, g, b = to_color(spec)
                yield c, r << 16 | g << 8 | b
        except Exception:
            continue


ScreenSize = namedtuple('ScreenSize', 'rows cols width height cell_width cell_height')


class ScreenSizeGetter:
    changed = True
    Size = ScreenSize
    ans: Optional[ScreenSize] = None

    def __init__(self, fd: Optional[int]):
        if fd is None:
            fd = sys.stdout.fileno()
        self.fd = fd

    def __call__(self) -> ScreenSize:
        if self.changed:
            import array
            import fcntl
            import termios
            buf = array.array('H', [0, 0, 0, 0])
            fcntl.ioctl(self.fd, termios.TIOCGWINSZ, cast(bytearray, buf))
            rows, cols, width, height = tuple(buf)
            cell_width, cell_height = width // (cols or 1), height // (rows or 1)
            self.ans = ScreenSize(rows, cols, width, height, cell_width, cell_height)
            self.changed = False
        return cast(ScreenSize, self.ans)


@lru_cache(maxsize=64)
def screen_size_function(fd=None):
    return ScreenSizeGetter(fd)


def fit_image(width, height, pwidth, pheight):
    from math import floor
    if height > pheight:
        corrf = pheight / float(height)
        width, height = floor(corrf * width), pheight
    if width > pwidth:
        corrf = pwidth / float(width)
        width, height = pwidth, floor(corrf * height)
    if height > pheight:
        corrf = pheight / float(height)
        width, height = floor(corrf * width), pheight

    return int(width), int(height)


def set_primary_selection(text):
    if not supports_primary_selection:
        return  # There is no primary selection
    if isinstance(text, bytes):
        text = text.decode('utf-8')
    from kitty.fast_data_types import set_primary_selection
    set_primary_selection(text)


def get_primary_selection():
    if not supports_primary_selection:
        return ''  # There is no primary selection
    from kitty.fast_data_types import get_primary_selection
    return (get_primary_selection() or b'').decode('utf-8', 'replace')


def base64_encode(
    integer,
    chars=string.ascii_uppercase + string.ascii_lowercase + string.digits +
    '+/'
):
    ans = ''
    while True:
        integer, remainder = divmod(integer, 64)
        ans = chars[remainder] + ans
        if integer == 0:
            break
    return ans


def command_for_open(program='default'):
    if isinstance(program, str):
        from .conf.utils import to_cmdline
        program = to_cmdline(program)
    if program == ['default']:
        cmd = ['open'] if is_macos else ['xdg-open']
    else:
        cmd = program
    return cmd


def open_cmd(cmd, arg=None, cwd=None):
    import subprocess
    if arg is not None:
        cmd = list(cmd)
        if isinstance(arg, (list, tuple)):
            cmd.extend(arg)
        else:
            cmd.append(arg)
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=cwd or None)


def open_url(url, program='default', cwd=None):
    return open_cmd(command_for_open(program), url, cwd=cwd)


def detach(fork=True, setsid=True, redirect=True):
    if fork:
        # Detach from the controlling process.
        if os.fork() != 0:
            raise SystemExit(0)
    if setsid:
        os.setsid()
    if redirect:
        from .fast_data_types import redirect_std_streams
        redirect_std_streams(os.devnull)


def adjust_line_height(cell_height, val):
    if isinstance(val, int):
        return cell_height + val
    return int(cell_height * val)


def init_startup_notification_x11(window_handle, startup_id=None):
    # https://specifications.freedesktop.org/startup-notification-spec/startup-notification-latest.txt
    from kitty.fast_data_types import init_x11_startup_notification
    sid = startup_id or os.environ.pop('DESKTOP_STARTUP_ID', None)  # ensure child processes don't get this env var
    if not sid:
        return
    from .fast_data_types import x11_display
    display = x11_display()
    if not display:
        return
    return init_x11_startup_notification(display, window_handle, sid)


def end_startup_notification_x11(ctx):
    from kitty.fast_data_types import end_x11_startup_notification
    end_x11_startup_notification(ctx)


def init_startup_notification(window_handle, startup_id=None):
    if is_macos or is_wayland():
        return
    if window_handle is None:
        log_error('Could not perform startup notification as window handle not present')
        return
    try:
        return init_startup_notification_x11(window_handle, startup_id)
    except Exception:
        import traceback
        traceback.print_exc()


def end_startup_notification(ctx):
    if not ctx:
        return
    if is_macos or is_wayland():
        return
    try:
        end_startup_notification_x11(ctx)
    except Exception:
        import traceback
        traceback.print_exc()


class startup_notification_handler:

    def __init__(self, do_notify=True, startup_id=None, extra_callback=None):
        self.do_notify = do_notify
        self.startup_id = startup_id
        self.extra_callback = extra_callback
        self.ctx = None

    def __enter__(self):

        def pre_show_callback(window_handle):
            if self.extra_callback is not None:
                self.extra_callback(window_handle)
            if self.do_notify:
                self.ctx = init_startup_notification(window_handle, self.startup_id)

        return pre_show_callback

    def __exit__(self, *a):
        if self.ctx is not None:
            end_startup_notification(self.ctx)


def remove_socket_file(s, path=None):
    with suppress(OSError):
        s.close()
    if path:
        with suppress(OSError):
            os.unlink(path)


def unix_socket_paths(name, ext='.lock'):
    import tempfile
    home = os.path.expanduser('~')
    candidates = [tempfile.gettempdir(), home]
    if is_macos:
        from .fast_data_types import user_cache_dir
        candidates = [user_cache_dir(), '/Library/Caches']
    for loc in candidates:
        if os.access(loc, os.W_OK | os.R_OK | os.X_OK):
            filename = ('.' if loc == home else '') + name + ext
            yield os.path.join(loc, filename)


def single_instance_unix(name):
    import socket
    for path in unix_socket_paths(name):
        socket_path = path.rpartition('.')[0] + '.sock'
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC)
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as err:
            if err.errno in (errno.EAGAIN, errno.EACCES):
                # Client
                s = socket.socket(family=socket.AF_UNIX)
                s.connect(socket_path)
                single_instance.socket = s
                return False
            raise
        s = socket.socket(family=socket.AF_UNIX)
        try:
            s.bind(socket_path)
        except OSError as err:
            if err.errno in (errno.EADDRINUSE, errno.EEXIST):
                os.unlink(socket_path)
                s.bind(socket_path)
            else:
                raise
        single_instance.socket = s  # prevent garbage collection from closing the socket
        atexit.register(remove_socket_file, s, socket_path)
        s.listen()
        s.set_inheritable(False)
        return True


class SingleInstance:

    socket: Optional[Any] = None

    def __call__(self, group_id: Optional[str] = None):
        import socket
        name = '{}-ipc-{}'.format(appname, os.geteuid())
        if group_id:
            name += '-{}'.format(group_id)

        s = socket.socket(family=socket.AF_UNIX)
        # First try with abstract UDS
        addr = '\0' + name
        try:
            s.bind(addr)
        except OSError as err:
            if err.errno == errno.ENOENT:
                return single_instance_unix(name)
            if err.errno == errno.EADDRINUSE:
                s.connect(addr)
                single_instance.socket = s
                return False
            raise
        s.listen()
        single_instance.socket = s  # prevent garbage collection from closing the socket
        s.set_inheritable(False)
        atexit.register(remove_socket_file, s)
        return True


single_instance = SingleInstance()


def parse_address_spec(spec):
    import socket
    protocol, rest = spec.split(':', 1)
    socket_path = None
    if protocol == 'unix':
        family = socket.AF_UNIX
        address = rest
        if address.startswith('@') and len(address) > 1:
            address = '\0' + address[1:]
        else:
            socket_path = address
    elif protocol in ('tcp', 'tcp6'):
        family = socket.AF_INET if protocol == 'tcp' else socket.AF_INET6
        host, port = rest.rsplit(':', 1)
        address = host, int(port)
    else:
        raise ValueError('Unknown protocol in --listen-on value: {}'.format(spec))
    return family, address, socket_path


def write_all(fd, data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    while data:
        n = os.write(fd, data)
        if not n:
            break
        data = data[n:]


class TTYIO:

    def __enter__(self):
        from .fast_data_types import open_tty
        self.tty_fd, self.original_termios = open_tty(True)
        return self

    def __exit__(self, *a):
        from .fast_data_types import close_tty
        close_tty(self.tty_fd, self.original_termios)

    def send(self, data):
        if isinstance(data, (str, bytes)):
            write_all(self.tty_fd, data)
        else:
            for chunk in data:
                write_all(self.tty_fd, chunk)

    def recv(self, more_needed, timeout, sz=1):
        fd = self.tty_fd
        start_time = monotonic()
        while timeout > monotonic() - start_time:
            # will block for 0.1 secs waiting for data because we have set
            # VMIN=0 VTIME=1 in termios
            data = os.read(fd, sz)
            if data and not more_needed(data):
                break


def natsort_ints(iterable):

    def convert(text):
        return int(text) if text.isdigit() else text

    def alphanum_key(key):
        return tuple(map(convert, re.split(r'(\d+)', key)))

    return sorted(iterable, key=alphanum_key)


def exe_exists(exe):
    for loc in os.environ.get('PATH', '').split(os.pathsep):
        if loc and os.access(os.path.join(loc, exe), os.X_OK):
            return os.path.join(loc, exe)
    return False


@lru_cache(maxsize=2)
def get_editor() -> List[str]:
    import shlex
    for ans in (os.environ.get('VISUAL'), os.environ.get('EDITOR'), 'vim',
                'nvim', 'vi', 'emacs', 'kak', 'micro', 'nano', 'vis'):
        if ans and exe_exists(shlex.split(ans)[0]):
            break
    else:
        ans = 'vim'
    return shlex.split(ans)


def is_path_in_temp_dir(path):
    if not path:
        return False

    def abspath(x):
        if x:
            return os.path.abspath(os.path.realpath(x))

    import tempfile
    path = abspath(path)
    candidates = frozenset(map(abspath, ('/tmp', '/dev/shm', os.environ.get('TMPDIR', None), tempfile.gettempdir())))
    for q in candidates:
        if q and path.startswith(q):
            return True
    return False


def func_name(f):
    if hasattr(f, '__name__'):
        return f.__name__
    if hasattr(f, 'func') and hasattr(f.func, '__name__'):
        return f.func.__name__
    return str(f)


def resolved_shell(opts: Optional['Namespace'] = None) -> List[str]:
    ans = getattr(opts, 'shell', '.')
    if ans == '.':
        ans = [shell_path]
    else:
        import shlex
        ans = shlex.split(ans)
    return ans


def read_shell_environment(opts: Optional['Namespace'] = None) -> Dict[str, str]:
    ans = getattr(read_shell_environment, 'ans', None)
    if ans is None:
        from .child import openpty, remove_blocking
        ans = {}
        setattr(read_shell_environment, 'ans', ans)
        import subprocess
        shell = resolved_shell(opts)
        master, slave = openpty()
        remove_blocking(master)
        try:
            p = subprocess.Popen(shell + ['-l', '-c', 'env'], stdout=slave, stdin=slave, stderr=slave, start_new_session=True, close_fds=True)
        except FileNotFoundError:
            log_error('Could not find shell to read environment')
            return ans
        with os.fdopen(master, 'rb') as stdout, os.fdopen(slave, 'wb'):
            raw = b''
            from subprocess import TimeoutExpired
            from time import monotonic
            start_time = monotonic()
            while monotonic() - start_time < 1.5:
                try:
                    ret: Optional[int] = p.wait(0.01)
                except TimeoutExpired:
                    ret = None
                with suppress(Exception):
                    raw += stdout.read()
                if ret is not None:
                    break
            if p.returncode is None:
                log_error('Timed out waiting for shell to quit while reading shell environment')
                p.kill()
            elif p.returncode == 0:
                while True:
                    try:
                        x = stdout.read()
                    except Exception:
                        break
                    if not x:
                        break
                    raw += x
                draw = raw.decode('utf-8', 'replace')
                for line in draw.splitlines():
                    k, v = line.partition('=')[::2]
                    if k and v:
                        ans[k] = v
            else:
                log_error('Failed to run shell to read its environment')
    return ans
