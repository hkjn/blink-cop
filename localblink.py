#!/usr/bin/python

"""Changes blink(1) color depending on build status, as reported by remote host.

Combined with a separate command (defaults to ./buildstatus) on the
remote host that produces the strings "green", "red", "black", etc on
stdout when called, this will keep your blink(1) in sync with your
build status.

Requires:
- ssh with public keys set up
- blink1-tool
- connected blink(1) device
"""

import httplib
import subprocess

STABLE_PUBLIC_HOST = 'http://www.google.com'
BLINK_TOOL = './blink1-tool'

# Host and command (run via SSH).
#HOST = 'henrik.mtv'
HOST = 'bork.i'
GET_BUILD_STATUS_COMMAND = './buildstatus'
POLLING_LATENCY_MS = 5000
# Tweak blinking frequency to approximate the polling latency we want.
TIMES_TO_BLINK = 10
BLINK_DELAY_MS = int(float(POLLING_LATENCY_MS) / TIMES_TO_BLINK)


class Error(BaseException):
    """A base exception."""

class CannotGetBuildStatusError(Error):
    """Not able to get build status, it seems."""

class ServerError(Error):
    """Something broke in a remote call."""

class BlinkError(Error):
    """Something went wrong with blink(1)."""

class InvalidStatusError(Error):
    """Invalid status."""


class Colors(object):
    RED = (255, 0, 0)
    BLUE = (0, 0, 255)
    ORANGE = (255, 190, 0)
    GREY = (127, 127, 127)
    GREEN = (0, 255, 0)
    TEAL = (0, 255, 255)
    YELLOW = (255, 200, 0)


def GetBlinkCmd(color, blink_delay_ms=BLINK_DELAY_MS):
    """Get blink1-tool command line as a list."""

    r, g, b = color
    color_fading_delay = blink_delay_ms  # Anything else looks jagged.
    flags = ('--rgb %s,%s,%s --blink %s --delay %s -m %s' %
             (r, g, b, TIMES_TO_BLINK, blink_delay_ms, color_fading_delay))
    return [BLINK_TOOL] + flags.split(' ')


def GetDiscoCmd():
    """Command to blink madly."""
    return [BLINK_TOOL, '-t', '50', '--random',  '200']


def RunCmdOnHost(cmd):
    """Run command remotely, via ssh."""

    commands = ['ssh', '-qo', 'PasswordAuthentication=no', HOST, cmd]
    child = subprocess.Popen(commands, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, close_fds=True)
    output = child.communicate()
    stdout = output[0].lower()
    stderr = output[1].lower()
    print '[ssh out] %s' % stdout
    print '[ssh err] %s' % stderr
    if 'could not resolve hostname' in stderr:
        # Failed to connect to remote host.
        raise CannotGetBuildStatusError(stderr)
    if 'not authenticated' in stdout:
        # Remote command failed for auth reasons.
        raise CannotGetBuildStatusError(stderr)

    if stderr:
        # Something unexpected (beyond just being offline).
        print 'Remote command failed: %s' % stderr
        raise ServerError(stderr)
    if not stdout:
        raise ServerError('Empty output from remote comamnd')
    return stdout


def GetBuildStatus():
    """Get the status of the continous build from a remote host.

    Assumes that the strings 'green', 'red', 'black', 'grey' are in
    the response as appropriate (and not otherwise).
    """

    result = RunCmdOnHost(GET_BUILD_STATUS_COMMAND)
    if 'green' in result:
        result = Status.BUILD_GREEN
    elif 'red' in result:
        result = Status.BUILD_RED
    elif 'black' in result:
        result = Status.BUILD_BLACK
    elif 'grey' in result:
        result = Status.BUILD_GREY
    else:
        # Catch-all for "managed to talk to server, but can't
        # understand what it's saying".
        raise ServerError('Unexpected response: %s' % result)
    print 'Build status: %s.' % StatusEnumToString(result)
    return result


def Draw(r, g, b):
    color_fading_delay = BLINK_DELAY_MS
    flags = ('--rgb %s,%s,%s --blink %s --delay %s -m %s' %
             (r, g, b, TIMES_TO_BLINK, BLINK_DELAY_MS, color_fading_delay))
    RunBlinkCmd([BLINK_TOOL] + flags.split(' '))



def RunBlinkCmd(commands):
    """Send command (as list) to blink1-tool."""

    try:
        child = subprocess.Popen(
            commands, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True)
    except OSError as e:
        raise BlinkError('Failed to run %s: %s' % (commands, e))
    out, err = child.communicate()
    if err:
        raise BlinkError('Blink command failed: %s' % err)


def StatusEnumToString(status):
    if status == Status.UNKNOWN:
        return 'unknown'
    elif status == Status.OFFLINE:
        return 'offline'
    elif status == Status.ONLINE:
        return 'online'
    elif status == Status.BUILD_GREY:
        return 'unknown'
    elif status == Status.BUILD_BLACK:
        return 'black build'
    elif status == Status.BUILD_RED:
        return 'red build'
    elif status == Status.BUILD_GREEN:
        return 'green build'
    raise InvalidStatusException(status)


class Status(object):
    UNKNOWN = 0
    OFFLINE = 1
    ONLINE = 2
    # All the BUILD statuses imply being online and able to talk to
    # remote server to get build status.
    BUILD_GREY = 3
    BUILD_BLACK = 4
    BUILD_RED = 5
    BUILD_GREEN = 6

    def __init__(self):
        self.status = self.UNKNOWN

    def GetBlinkCmd(self):
        """Get the blink(1) command for the current status."""

        if self.status in (self.UNKNOWN, self.OFFLINE):
            return GetBlinkCmd(Colors.ORANGE)
        elif self.status == self.ONLINE:
            return GetBlinkCmd(Colors.YELLOW)
        elif self.status == self.BUILD_GREY:
            return GetBlinkCmd(Colors.GREY)
        elif self.status == self.BUILD_RED:
            return GetBlinkCmd(Colors.RED, blink_delay_ms=500)
        elif self.status == self.BUILD_BLACK:
            # Aggressive red blinking.
            return GetBlinkCmd(Colors.RED, blink_delay_ms=50)
        elif self.status == self.BUILD_GREEN:
            return GetBlinkCmd(Colors.GREEN)
        raise InvalidStatusException(self.status)


    def __str__(self):
        return StatusEnumToString(self.status)

    def Update(self):
        """Update status."""

        old_status = self.status
        if self.status in (self.UNKNOWN, self.OFFLINE):
            # Let's see if we're online now.
            if HttpGet(STABLE_PUBLIC_HOST):
                self.status = self.ONLINE
        elif self.status >= self.ONLINE:
            # We're online, try to get build status.
            try:
                self.status = GetBuildStatus()
            except CannotGetBuildStatusError as e:
                # Go back to assuming we're ONLINE (which will drop us
                # OFFLINE eventually, if the stable public host can't
                # be reached).
                self.status = self.ONLINE
        if self.status != old_status:
            print('Status changed from %s to %s' %
                  (StatusEnumToString(old_status),
                   StatusEnumToString(self.status)))


def HttpGet(host):
    """GET HTTP/HTTPS resource and return True if successful."""

    protocol = 'http://'  # Assume unencrypted.
    if '://' in host:
        parts = host.split('://')
        protocol, host = parts
    if protocol == 'https':
        conn = httplib.HTTPSConnection(host, timeout=1)
    else:
        conn = httplib.HTTPConnection(host, timeout=1)
    try:
        conn.request('GET', '/')
        response = conn.getresponse()
    except Exception as e:  # TODO: Narrow.
        print 'Got exception: %s' % e
        return False
    finally:
        conn.close()
    if response.status == 200:
        return True
    return False


def Run():
    """Infinite loop showing status."""

    status = Status()
    while True:
        # Check status.
        try:
            status.Update()
        except Error as e:
            RunBlinkCmd(GetDiscoCmd())  # If nothing caught this, exit with a show.
            raise
        # Show status on blink(1). This blocks for some time.
        RunBlinkCmd(status.GetBlinkCmd())


if __name__ == '__main__':
    print 'Running..'
    Run()
