#!/usr/bin/python

"""Changes blink(1) color depending on "status", as reported by remote host.

Combined with a separate command (defaults to ./status) on the remote
host that produces JSON strings on the format [[r, g, b], delay] on
stdout when called, this will keep your blink(1) in sync with whatever
status you want to track (e.g. builds, monitoring).

Requires:
- ssh with public keys set up
- blink1-tool
- connected blink(1) device

TODO: Detect if ssh won't work without password (kinit was not run).
TODO: Turn off when sleeping.
"""

import json
import httplib
import subprocess

STABLE_PUBLIC_HOST = 'http://www.google.com'
BLINK_TOOL = './blink1-tool'

# Host and command (run via SSH).
HOST = 'henrik.mtv'
#HOST = 'bork.i'
GET_STATUS_COMMAND = './status'
POLLING_LATENCY_MS = 5000
# Tweak blinking frequency to approximate the polling latency we want.
TIMES_TO_BLINK = 10
BLINK_DELAY_MS = int(float(POLLING_LATENCY_MS) / TIMES_TO_BLINK)
# Horrible hack: The cover of my blink(1) fell off, so I'll scale
# down the intensity by some factor to not blind myself.
SCALING_FACTOR = 0.4


class Error(BaseException):
    """A base exception."""

class CannotGetStatusError(Error):
    """Not able to get status on the remote host."""

class ServerError(Error):
    """Something broke in a remote call."""

class BlinkError(Error):
    """Something went wrong with blink(1)."""

class InvalidStatusError(Error):
    """Invalid status."""


def GetBlinkCmd(r, g, b, blink_delay_ms=BLINK_DELAY_MS):
    """Get blink1-tool command line as a list."""

    scaling = SCALING_FACTOR
    color_fading_delay = blink_delay_ms  # Anything else looks jagged.
    flags = ('--rgb %s,%s,%s --blink %s --delay %s -m %s' %
             (r * scaling, g * scaling, b * scaling, TIMES_TO_BLINK, blink_delay_ms, color_fading_delay))
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
        raise CannotGetStatusError(stderr)

    if stderr:
        # Something unexpected (beyond just being offline).
        print 'Remote command failed: %s' % stderr
        raise ServerError(stderr)
    if not stdout:
        print 'Empty output from remote comamnd'
        raise CannotGetStatusError('Empty output from remote command')
    return stdout


def GetStatus():
    """Get the status of something we're interested in from the remote host.

    Assumes that the remote command puts output like (255 0 0 x 25),
    for blinking pure red every 25 ms.
    """
    
    result = RunCmdOnHost(GET_STATUS_COMMAND)
    try:
        rgb, delay = json.loads(result)
        r, g, b = rgb
    except ValueError as ve:
        # Catch-all for "managed to talk to server, but can't
        # understand what it's saying".
        raise ServerError('Unexpected response: %s' % result)
    result = ServerStatus(r, g, b, delay=delay)
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


class Status(object):
    def __init__(self, r, g, b, delay=500):
        self.r = r
        self.g = g
        self.b = b
        self.delay = delay if delay is not None else 500

    def __str__(self):
        raise NotImplementedError()

    def Update(self):
        """Update status."""

        raise NotImplementedError()

    def Blink(self):
        """Get the blink(1) command for the current status."""
        # TODO: Rename.
        return RunBlinkCmd(GetBlinkCmd(self.r, self.g, self.b, blink_delay_ms=self.delay))
        

class UnknownStatus(Status):
    def __init__(self):
        # TODO: Abstract the numbers away.
        super(UnknownStatus, self).__init__(127, 127, 127)
    def Update(self):
        # Let's see if we're online now.
        if HttpGet(STABLE_PUBLIC_HOST):
            return OnlineStatus()
        return self

    def __str__(self):
        return 'unknown'

class OfflineStatus(UnknownStatus):
    def __init__(self):
        # TODO: Abstract the numbers away.
        super(OfflineStatus, self).__init__(255, 190, 0)

    def __str__(self):
        return 'offline'

class OnlineStatus(Status):
    def __init__(self, r=255, g=200, b=0, delay=None):
        # TODO: Abstract the numbers away.
        super(OnlineStatus, self).__init__(r, g, b, delay=delay)

    def Update(self):
        # Try to connect to the remote host to get status.
        try:
            return GetStatus()
        except CannotGetStatusError as e:
            # We were able to connect to the remote host but couldn't
            # get status, so go back to assuming we're online (which
            # will drop us offline eventually, if the stable public
            # host can't be reached).
            return self
    def __str__(self):
        return 'online'

class ServerStatus(OnlineStatus):
    def __str__(self):
        return 'server-specified status'


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

    status = UnknownStatus()
    while True:
        # Check status.
        try:
            status = status.Update()
            print 'Status: %s' % status
        except Error as e:
            RunBlinkCmd(GetDiscoCmd())  # If nothing caught this, exit with a show.
            raise
        # Show status on blink(1). This blocks for some time (possible
        # network call + sending command to blink(1).
        status.Blink()


if __name__ == '__main__':
    print 'Running..'
    Run()
