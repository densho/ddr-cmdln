from copy import deepcopy
import logging
logger = logging.getLogger(__name__)
import os
import shlex

import envoy
import psutil

from DDR import MEDIA_BASE

DEVICE_TYPES = ['hdd', 'usb']

DEVICE_STATES = {
    'hdd': {
        '--': [],
        'm-': ['link'],
        'ml': ['unlink'],
        '-l': ['unlink'],
    },
    'usb': {
        '--': ['mount'],
        'm-': ['unmount','link'],
        'ml': ['unmount','unlink'],
        '-l': ['unlink'],
    },
    'nfs': {
        '--': [],
        'm-': ['link'],
        'ml': ['unlink'],
        '-l': ['unlink'],
    },
    'unknown': {}
}


def device_actions(device):
    """Given device from devices(), return possible actions.
    
    @param device: dict
    @returns: list
    """
    devicetype = device['devicetype']
    state = []
    if device['mounted']: state.append('m')
    else:                 state.append('-')
    if device['linked']:  state.append('l')
    else:                 state.append('-')
    state = ''.join(state)
    return DEVICE_STATES[devicetype][state]

def local_devices(udisks_dump_stdout):
    """Parse the output of 'udisks --dump'
    
    NOTE: Separated from .devices() for easier testing.
    NOTE: This is probably unique to VirtualBox!
    
    @param udisks_dump_stdout: str Output of "udisks --dump".
    @returns: list of dicts containing device info.
    """
    chunks = udisks_dump_stdout.split('========================================================================\n')
    udisks_dump_stdout = None
    # get sdb* devices (sdb1, sdb2, etc)
    sdchunks = []
    NUMBRS = [c for c in '0123456789']
    for c in chunks:
        if ('sdb' in c) or ('sdc' in c) or ('sdd' in c):
            lines = c.split('\n')
            if lines[0][-1] in NUMBRS:
                sdchunks.append(c)
    chunks = c = None
    # grab the interesting data for each device
    # IMPORTANT: spaces are removed from these labels when they are assigned!!!
    devices = []
    INTERESTING = [
        'device-file', 'mount paths', 'label', 'is mounted', 'type',
        'native-path', 'by-id',
    ]
    for c in sdchunks:
        device = {
            'devicetype': 'unknown',
            'mounted': False,
            'linked': False,
            'actions': [],
        }
        for l in c.split('\n'):
            if ':' in l:
                k,v = l.split(':', 1)
                k = k.strip()
                v = v.strip()
                if (k in INTERESTING) and v and (not k in device.keys()):
                    device[k] = v
        devices.append(device)
    sdchunks = c = None
    # rm spaces and dashes
    RENAME_FIELDS = {
        'device-file': 'devicefile',
        'mount paths': 'mountpath',
        'is mounted': 'mounted',
        'type': 'fstype',
    }
    for device in devices:
        for keyfrom,keyto in RENAME_FIELDS.iteritems():
            if device.get(keyfrom,None):
                device[keyto] = device.pop(keyfrom)
    # I like ints
    INTEGERS = ['mounted', 'linked']
    for device in devices:
        for field in INTEGERS:
            if device.get(field, None):
                device[field] = int(device[field])
    # interpret device type
    for device in devices:
        # HDD
        if ('harddisk' in device['by-id'].lower()):
            device['devicetype'] = 'hdd'
            if device['mounted']:
                device['label'] = device['mountpath'].replace('/media/', '')
            else:
                device['label'] = device['devicefile']
        # USB
        elif 'usb' in device['native-path'].lower():
            device['devicetype'] = 'usb'
        device.pop('by-id')
        device.pop('native-path')
    # collections directory
    for device in devices:
        if device.get('mountpath', None):
            device['basepath'] = os.path.join(device['mountpath'], 'ddr')
    return devices

def nfs_devices(df_T_stdout):
    """List mounted NFS volumes.
    
    NFS shares mounted like this for testing:
        $ sudo mount -v -t nfs -o rw,nosuid qml:/srv/www /mnt/qml
    
    @param df_T_stdout: str Output of "df -T".
    @returns: list of dicts containing device info.
    """
    devices = []
    for line in df_T_stdout.strip().split('\n'):
        if 'nfs' in line:
            parts = shlex.split(line)
            device = {
                'devicetype': 'nfs',
                'devicefile': parts[0],
                'label': parts[0],
                'mountpath': parts[-1],
                'fstype': parts[1],
                'basepath': None,
                'linked': 0,
                'actions': [],
            }
            device['mounted'] = os.path.exists(device['mountpath'])
            devices.append(device)
    return devices

def find_dirs_with_file(base, marker, levels=2, excludes=['.git']):
    """Find dirs containing the specified file
    
    @param base: str Base directory.
    @param marker: str Look for dirs containing this file.
    @param levels: int Only descend this many levels (default 2).
    @param excludes: list Directories to exclude.
    """
    hits = []
    for root, dirs, files in os.walk(base):
        for x in excludes:
            if x in dirs:
                dirs.remove(x)
        if (marker in files):
            hits.append(root)
        depth = len(os.path.relpath(root, start=base).split(os.sep))
        if (depth >= levels) or (marker in files):
            # don't go any further down
            del dirs[:]
    sdirs = []
    for h in hits:
        d = os.path.dirname(h)
        if d not in sdirs:
            sdirs.append(d)
    return sdirs

def local_stores(devices, levels=3, symlink=None):
    """List Stores on local devices (HDD, USB).
    
    @param devices: list Output of local_devices().
    @param levels: int Limit how far down in the filesystem to look.
    @param symlink: str (optional) BASE_PATH symlink.
    """
    target = os.path.realpath(symlink)
    stores = []
    for device in devices:
        if not device.get('mounted'):
            # unmounted devices are added... so we can mount them
            device['actions'] = device_actions(device)
            stores.append(device)
        elif device.get('mountpath'):
            # find directories containing 'ddr' repositories.
            storedirs = find_dirs_with_file(
                device['mountpath'], 'repository.json',
                levels=2, excludes=['.git']
            )
            for sdir in storedirs:
                d = deepcopy(device)
                d['basepath'] = sdir
                d['label'] = d['basepath']
                # is device the target of symlink?
                if symlink and target:
                    if d.get('basepath') and (d['basepath'] == target):
                        d['linked'] = 1
                # what actions are possible from this state?
                d['actions'] = device_actions(d)
                stores.append(d)
    return stores

def nfs_stores(devices, levels=3, symlink=None):
    """List Stores under NFS basepath.
    
    @param devices: list Output of nfs_devices().
    @param levels: int Limit how far down in the filesystem to look.
    @param symlink: str (optional) BASE_PATH symlink.
    """
    target = os.path.realpath(symlink)
    stores = []
    for device in devices:
        # find directories containing 'ddr' repositories.
        sdirs = find_dirs_with_file(
            device['mountpath'], 'repository.json',
            levels=levels, excludes=['.git']
        )
        for sdir in sdirs:
            d = deepcopy(device)
            d['basepath'] = sdir
            d['label'] = d['basepath']
            # is device the target of symlink?
            if symlink and target:
                if d.get('basepath') and (d['basepath'] == target):
                    d['linked'] = 1
            # what actions are possible from this state?
            d['actions'] = device_actions(d)
            stores.append(d)
    return stores

def devices(symlink=None):
    """List removable drives whether or not they are attached.
    
    This is basically a wrapper around "udisks --dump" that looks for
    "/dev/sd*" devices and extracts certain info.
    Requires the udisks package (sudo apt-get install udisks).
    TODO Switch to udiskie? https://github.com/coldfix/udiskie
    
    >> devices()
    [
        {'devicetype': 'usb', fstype': 'ntfs', 'devicefile': '/dev/sdb1', 'label': 'USBDRIVE1', mountpath:'...', 'mounted':1, 'linked':True},
        {'device_type': 'hdd', fs_type': 'ext3', 'devicefile': '/dev/sdb2', 'label': 'USBDRIVE2', mountpath:'...', 'mounted':0, 'linked':True}
    ]
    
    @return: list of dicts containing attribs of devices
    """
    # HDD and USB devices
    localdevices = local_devices(envoy.run('udisks --dump', timeout=2).std_out)
    localstores = local_stores(localdevices, levels=3, symlink=symlink)
    # NFS shares
    nfsdevices = nfs_devices(envoy.run('df -T', timeout=2).std_out)
    nfsstores = nfs_stores(nfsdevices, levels=3, symlink=symlink)
    #
    devices = localstores + nfsstores
    return devices

def mounted_devices():
    """List mounted and accessible removable drives.
    
    Note: this is different from base_path!
    
    @return: List of dicts containing attribs of devices
    """
    return [
        {'devicefile':p.device, 'mountpath':p.mountpoint,}
        for p in psutil.disk_partitions()
        if '/media' in p.mountpoint
    ]

def mount( device_file, label ):
    """Mounts specified device at the label; returns mount_path.
    
    TODO FIX THIS HORRIBLY UNSAFE COMMAND!!!
    
    @param device_file: Device file (ex: '/dev/sdb1')
    @param label: Human-readable name of the drive (ex: 'mydrive')
    @return: Mount path (ex: '/media/mydrive')
    """
    mount_path = None
    cmd = 'pmount --read-write --umask 022 {} {}'.format(device_file, label)
    logger.debug(cmd)
    r = envoy.run(cmd, timeout=60)
    for d in mounted_devices():
        if label in d['mountpath']:
            mount_path = d['mountpath']
    logger.debug('mount_path: %s' % mount_path)
    return mount_path

def umount( device_file ):
    """Unmounts device at device_file.
    
    TODO FIX THIS HORRIBLY UNSAFE COMMAND!!!
    
    @param device_file: Device file (ex: '/dev/sdb1')
    @return: True/False
    """
    unmounted = 'error'
    cmd = 'pumount {}'.format(device_file)
    logger.debug(cmd)
    r = envoy.run(cmd, timeout=60)
    mounted = False
    for d in mounted_devices():
        if device_file in d['devicefile']:
            mounted = True
    if not mounted:
        unmounted = 'unmounted'
    logger.debug(unmounted)
    return unmounted

def remount( device_file, label ):
    unmounted = umount(device_file)
    mount_path = mount(device_file, label)
    return mount_path

def link(target):
    """Create symlink to Store from MEDIA_BASE.
    
    @param target: absolute path to link target
    """
    link = MEDIA_BASE
    link_parent = os.path.split(link)[0]
    logger.debug('link: %s -> %s' % (link, target))
    if target and link and link_parent:
        s = []
        if os.path.exists(target):          s.append('1')
        else:                               s.append('0')
        if os.path.exists(link_parent):     s.append('1')
        else:                               s.append('0')
        if os.access(link_parent, os.W_OK): s.append('1')
        else:                               s.append('0')
        s = ''.join(s)
        logger.debug('s: %s' % s)
        if s == '111':
            logger.debug('symlink target=%s, link=%s' % (target, link))
            os.symlink(target, link)

def unlink():
    """Remove symlink to Store from MEDIA_BASE.
    """
    if os.path.exists(MEDIA_BASE):
        target = os.path.realpath(MEDIA_BASE)
        logger.debug('rm %s (-> %s)' % (MEDIA_BASE, target))
        os.remove(MEDIA_BASE)
        logger.debug('%s exists: %s' % (MEDIA_BASE, os.path.exists(MEDIA_BASE)))
    else:
        logger.debug('%s not there!' % MEDIA_BASE)

def _make_drive_label( storagetype, mountpath ):
    """Make a drive label based on inputs.
    NOTE: Separated from .drive_label() for easier testing.
    """
    if storagetype in ['mounted', 'removable']:
        label = mountpath.replace('/media/', '')
        if label:
            return label
    return None

def drive_label( path ):
    """Returns drive label for path, if path points to a removable device.
    
    @param path: Absolute path
    @return: String drive_label or None
    """
    realpath = os.path.realpath(path)
    storagetype = storage_type(realpath)
    mountpath = mount_path(realpath)
    return _make_drive_label(storagetype, mountpath)

def is_writable(path):
    """Indicates whether user has write permissions; does not check presence.
    
    @param path: Absolute path
    @return: True/False
    """
    return os.access(path, os.W_OK)

def mount_path( path ):
    """Given an absolute path, finds the mount path.
    
    >>> mount_path('/media/USBDRIVE1/tmp/ddr-testing-201303211200/files/')
    '/media/USBDRIVE1'
    >>> mount_path('/home/gjost/ddr-local/ddrlocal')
    '/'
    
    @param path: Absolute file path
    @return: Path to mount point or '/'
    """
    if not path:
        return '/'
    p1 = os.sep.join( path.split(os.sep)[:-1] )
    if os.path.ismount(p1):
        return p1
    if p1 == '':
        return os.sep
    return mount_path(p1)

def status( path ):
    """Indicates status of storage path.
    
    If the VM gets paused/saved
    """
    try: exists = os.path.exists(path)
    except: exists = False
    try: listable = os.listdir(path)
    except: listable = False
    writable = os.access(path, os.W_OK)
    # conditions
    if exists and listable and writable:
        return 'ok'
    elif exists and not listable:
        return 'unmounted'
    return 'unknown'

def disk_space( mountpath ):
    """Returns disk usage in bytes for the mounted drive.
    
    @param mountpath
    @returns: OrderedDict total, used, free, percent
    """
    return psutil.disk_usage(mountpath)._asdict()
