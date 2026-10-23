# SPDX-License-Identifier: GPL-2.0-only
#
#   make                       the standalone console (console/, libc)
#   make install DESTDIR=...   the device layer (duranium-usb-debug), as the
#                              APKBUILD packages it
#   make install-host PREFIX=~/.local
#                              the host tool (install.sh does this from a release)
#   make check                 the tests (tests/)
#   make dist                  usb-debug-$(VERSION).tar.gz from git HEAD
#
# The bring-up init and uinput-type: console/Makefile (make -C console init ...).

VERSION := $(shell cat VERSION)
DESTDIR ?=
PREFIX ?= /usr/local
O ?= build

LIBEXEC := $(DESTDIR)/usr/libexec/duranium
UNITS := $(DESTDIR)/usr/lib/systemd/system

.PHONY: all console install install-device install-host check dist clean

all: console

console:
	$(MAKE) -C console console O=$(abspath $(O))

install: install-device

install-device: console
	install -Dm755 $(O)/usb-debug-console $(LIBEXEC)/usb-debug-console
	for f in device/libexec/*; do install -Dm755 "$$f" "$(LIBEXEC)/$${f##*/}" || exit 1; done
	install -Dm755 device/generator/duranium-usb-debug-generator \
		$(DESTDIR)/usr/lib/systemd/system-generators/duranium-usb-debug-generator
	install -Dm644 -t $(UNITS)/ device/systemd/*.service device/systemd/*.socket device/systemd/*.target
	install -Dm644 device/systemd/80-duranium-usb-debug.preset \
		$(DESTDIR)/usr/lib/systemd/system-preset/80-duranium-usb-debug.preset
	install -Dm644 device/systemd/80-duranium-usb-debug-adb.preset \
		$(DESTDIR)/usr/lib/systemd/system-preset/80-duranium-usb-debug-adb.preset
	install -Dm644 device/systemd/initramfs-duranium-usb-debug.preset \
		$(DESTDIR)/usr/lib/systemd/initrd-preset/80-duranium-usb-debug.preset
	install -Dm644 device/conf/sshd_config $(DESTDIR)/usr/share/duranium-usb-debug/sshd_config
	# firewall rule (only matters with postmarketos-config-nftables)
	install -Dm644 device/conf/60_duranium_usb_debug.nft \
		$(DESTDIR)/usr/share/nftables.avail/60_duranium_usb_debug.nft
	install -d $(DESTDIR)/etc/nftables.d
	ln -sf /usr/share/nftables.avail/60_duranium_usb_debug.nft \
		$(DESTDIR)/etc/nftables.d/60_duranium_usb_debug.nft
	install -Dm644 device/conf/duranium-usb-debug.tmpfiles \
		$(DESTDIR)/usr/lib/tmpfiles.d/duranium-usb-debug.conf
	install -Dm644 -t $(DESTDIR)/usr/lib/systemd/network/ device/conf/10-duranium-usb-debug.link
	install -Dm644 -t $(DESTDIR)/usr/lib/udev/rules.d/ device/conf/90-duranium-usb-debug-sockets.rules
	install -Dm644 -t $(DESTDIR)/usr/lib/NetworkManager/conf.d/ device/conf/50-duranium-usb-debug-nm.conf
	install -Dm644 device/conf/40-duranium-usb-debug-watchdog.conf \
		$(DESTDIR)/usr/lib/systemd/system.conf.d/40-duranium-usb-debug.conf
	install -d $(DESTDIR)/usr/lib/duranium-usb-debug/conf.d
	install -Dm644 -t $(DESTDIR)/usr/share/doc/duranium-usb-debug/ \
		device/README device/conf/usb-debug.conf.example

install-host:
	install -d $(DESTDIR)$(PREFIX)/share/usb-debug/$(VERSION) $(DESTDIR)$(PREFIX)/bin
	cp -R host console tools docs devices VERSION $(DESTDIR)$(PREFIX)/share/usb-debug/$(VERSION)/
	ln -sfn $(VERSION) $(DESTDIR)$(PREFIX)/share/usb-debug/current
	ln -sf ../share/usb-debug/current/host/usb-debug $(DESTDIR)$(PREFIX)/bin/usb-debug

check: console
	USBDBG_CONSOLE=$(abspath $(O))/usb-debug-console python3 -m unittest discover -s tests -t . -v

dist:
	@mkdir -p $(O)
	git archive --format=tar --prefix=usb-debug-$(VERSION)/ HEAD | gzip -n9 > $(O)/usb-debug-$(VERSION).tar.gz
	@echo $(O)/usb-debug-$(VERSION).tar.gz

clean:
	rm -rf $(O)
