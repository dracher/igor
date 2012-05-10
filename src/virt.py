# -*- coding: utf-8 -*-

import logging
import os
import subprocess
from string import Template
from lxml import etree
import libvirt

from adt import *
import gvfs
from gvfs import run

logger = logging.getLogger(__name__)

GVFS_IS_STILL_BROKEN = True
LOOP_IS_STILL_BROKEN = True

def dict_to_args(d):
    return " ".join(["--%s" % k if v is None else \
                     "--%s=%s" % (k, v) \
                     for k, v in d.items()])


class VMImage(UpdateableObject):
    '''
    An image specififcation for a VMHost.
    This are actually params for truncate and parted.

    Parameters
    ----------
    size : int-like
        Size in GB
    label : string, optional
        label in ['gpt', 'mbr', 'loop']
    partitions : Array of Partitions
    '''
    filename = None
    size = 4
    label = "gpt"
    partitions = [{}]

    def __init__(self, size, partitions, label="gpt", filename=None):
        self.filename = filename
        self.size = size
        self.partitions = partitions
        self.label = label

    def create(self, session_dir="/tmp"):
        if self.filename is None:
            self.filename = run("mktemp --tmpdir='%s' 'vmimage-XXXX.img'" % \
                                session_dir)
        logger.debug("Creating VM image '%s'" % self.filename)
        self.truncate()
        self.partition()
        return self.filename

    def remove(self):
        logger.debug("Removing VM image '%s'" % self.filename)
        os.remove(self.filename)

    def truncate(self):
        run("truncate --size=%s '%s'" % (self.size, self.filename))

    def partition(self):
        for_parted = []

        # Create label
        if self.label not in ['gpt', 'mbr', 'loop']:
            raise Exception("No valid label given.")
        for_parted.append("mklabel %s" % self.label)

        # Create all partitions
        if self.partitions is None or len(self.partitions) is 0:
            logger.debug("No partitions given")
        else:
            for p in self.partitions:
                for_parted.append(p.for_parted())

        # Quit parted
        for_parted.append("quit")

        for parted_cmd in for_parted:
            run("parted '%s' '%s'" % (self.filename, parted_cmd))


class Partition(UpdateableObject):
    '''
    Params
    ------
    An array of dicts containing:
    - part_type (pri, sec, ext)
    - fs_type (ext[234], btrfs, ...), optional
    - start (see parted)
    - end (see parted)
    '''
    part_type = None
    start = None
    end = None
    fs_type = ""

    def __init__(self, pt, start, end, fst=""):
        self.part_type = pt
        self.start = start
        self.end = end
        self.fs_type = fst

    def for_parted(self):
        return "mkpart %s %s %s %s" % (self.part_type, self.fs_type, \
                                       self.start, self.end)

def virsh(cmd):
    run("virsh --connect='qemu:///system' %s" % cmd)

class VMHost(Host):
    '''A host which is actually a virtual guest.

    VMHosts are not much different from other hosts, besides that we can configure them.
    '''
    session = None
    image_specs = None

    disk_images = []

    vm_prefix = "igor-vm-"
    vm_defaults = {
        "vcpus": "4",
        "ram": "512",
        "os-type": "linux",
        "wait": "1"
    }

    connection_uri = "qemu:///system"
    libvirt_vm_definition = None

    def prepare_profile(self, p):
        logger.debug("Preparing VMHost")
        assert (self.session is not None)
        self.prepare_images()
        self.prepare_vm()
#        self.start_vm_and_install_os()

    def prepare_images(self):
        logger.debug("Preparing images")
        if self.image_specs is None or len(self.image_specs) is 0:
            logger.info("No image spec given.")
        else:
            for image_spec in self.image_specs:
                self.disk_images.append(image_spec.create(self.session.dirname))

    def prepare_vm(self):
        logger.debug("Preparing vm")

        self._vm_name = "%s%s" % (self.vm_prefix, self.session.cookie)

        # Sane defaults
        virtinstall_args = {
            "connect": "'%s'" % self.connection_uri,
            "name": "'%s'" % self._vm_name,
            "vcpus": "2",
            "cpu": "host",
            "ram": "1024",
            "boot": "network",
            "os-type": "'linux'",
            "noautoconsole": None,      # Prevents opening a window
            "import": None,
            "dry-run": None,
            "print-xml": None
        }

        virtinstall_args.update(self.vm_defaults)

        cmd = "virt-install "
        cmd += dict_to_args(virtinstall_args)

        for disk in self.disk_images:
            cmd += " --disk path='%s',device=disk,bus=virtio,format=raw" % disk

        self.libvirt_vm_definition = run(cmd)

#        logger.debug(self.libvirt_vm_definition)

        self.define()

    def get_first_mac_address(self):
        dom = etree.XML(self.libvirt_vm_definition)
        mac = dom.xpath("/domain/devices/interface[@type='network'][1]/mac")[0]
        return mac.attrib["address"]

    def start_vm_and_install_os(self):
        # Never reboot, even if requested by guest
        self.set_reboot_is_poweroff(True)

        self.boot()

    def remove_images(self):
        if self.image_specs is None or len(self.image_specs) is 0:
            logger.info("No image spec given.")
        else:
            for image_spec in self.image_specs:
                image_spec.remove()

    def remove_vm(self):
        self.shutdown()
        self.undefine()

    def remove(self):
        '''
        Remove all files which were created during the VM creation.
        '''
        self.remove_vm()
        self.remove_images()

    def submit_testsuite(self, session, testsuite):
        self.add_testsuite_cb_kernelarg()
        self.reboot()

    def boot(self):
        virsh("start %s" % self._vm_name)

    def reboot(self):
        virsh("reboot %s" % self._vm_name)

    def shutdown(self):
        virsh("shutdown %s" % self._vm_name)

    def define(self):
        tmpfile = run("mktemp --tmpdir")
        with open(tmpfile, "w") as f:
            logger.debug(tmpfile)
            f.write(self.libvirt_vm_definition)
            f.flush()
            virsh("define %s" % tmpfile)

    def undefine(self):
        virsh("undefine %s" % self._vm_name)


#pydoc cobbler.remote
class Cobbler(object):
    server = None
    credentials = None
    token = None

    def __init__(self, server_url, c=("cobbler", "cobbler")):
        import xmlrpclib
#        "http://cobbler-server.example.org/cobbler_api"
        self.credentials = c
        self.server = xmlrpclib.Server(server_url)

    def cobbler(self, cmd):
        if not run("cobbler %s && echo running" % cmd).endswith("running"):
            raise Exception("Cobbler is having a problem")

    def add_system(self, name, mac, profile):
        args = {
            "name": name,
            "mac": mac,
            "profile": profile,
            "status": "testing",
            "kernel_options": "BOOTIF=eth0 storage_init firstboot",
            "modify_interface": {
                "macaddress-eth0": mac
            }
        }

        with Cobbler.CobblerToken(self) as (token, obj):
            system_id = self.server.new_system(token)
            for k, v in args.items():
                logger.debug("Modifying system: %s %s" % (k, v))
                self.server.modify_system(system_id, k, v, token)
            self.server.save_system(system_id, token)

    def set_netboot_enable(self, name, pxe):
        args = {
            "netboot-enabled": 1 if pxe else 0
        }
        with Cobbler.CobblerToken(self) as (token, obj):
            system_handle = self.server.get_system_handle(name, token)
            for k, v in args.items():
                logger.debug("Modifying system: %s %s" % (k, v))
                self.server.modify_system(system_handle, k, v, token)
            self.server.save_system(system_handle, token)

    def remove_system(self, name):
        with Cobbler.CobblerToken(self) as (token, obj):
            self.server.remove_system(name, token)

    def find_system(self):
        with Cobbler.CobblerToken(self) as (token, obj):
            h = self.server.find_system(token)
            print h

    class CobblerToken:
        token = None
        cblr = None
        def __init__(self, cblr):
            self.cblr = cblr
            self.token = cblr.server.login(*(cblr.credentials))
        def __enter__(self):
            return (self.token, self)
        def __exit__(self, type, value, traceback):
            self.cblr.server.sync(self.token)
