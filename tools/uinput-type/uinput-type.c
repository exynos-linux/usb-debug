// SPDX-License-Identifier: GPL-2.0-only
/*
 * uinput-type: type on the tablet through a virtual keyboard, for unattended
 * tests (logging in at the Phosh / GNOME login screen after a test reboot).
 * Static nolibc binary, pushed to /run with scripts/tab-put and started with
 * the console's "run" (scripts/tab-login does both).
 *
 *   uinput-type TOKEN...
 *     @file:PATH   type the bytes of PATH (a trailing newline is dropped)
 *     @enter @esc @space @tab @backspace @shift
 *                  press that key (@shift: wakes a blanked screen without
 *                  typing anything)
 *     @sleep:MS    wait MS milliseconds
 *     anything else: type it as text
 *
 * Text is typed with the US layout (the login screens' default), printable
 * ASCII only. The device appears as "gtactive3 test keyboard"; it is
 * destroyed when the program exits, after a short pause so the compositor
 * has read every event.
 *
 * Build (static, same flags as initramfs/init.c):
 *   aarch64-linux-gnu-gcc -Os -static -nostdlib -fno-stack-protector \
 *     -I$K/tools/include/nolibc -I$P/build/hdr/usr/include \
 *     -include $K/tools/include/nolibc/nolibc.h -o uinput-type uinput-type.c -lgcc
 */

#include <linux/input.h>
#include <linux/uinput.h>

static int ufd = -1;

static void emit(int type, int code, int value)
{
	struct input_event ev;

	memset(&ev, 0, sizeof(ev));
	ev.type = type;
	ev.code = code;
	ev.value = value;
	if (write(ufd, &ev, sizeof(ev)) != sizeof(ev))
		printf("uinput-type: write failed\n");
}

static void key(int code, int shift)
{
	if (shift) {
		emit(EV_KEY, KEY_LEFTSHIFT, 1);
		emit(EV_SYN, SYN_REPORT, 0);
	}
	emit(EV_KEY, code, 1);
	emit(EV_SYN, SYN_REPORT, 0);
	msleep(20);
	emit(EV_KEY, code, 0);
	emit(EV_SYN, SYN_REPORT, 0);
	if (shift) {
		emit(EV_KEY, KEY_LEFTSHIFT, 0);
		emit(EV_SYN, SYN_REPORT, 0);
	}
	msleep(40);
}

/* US layout: the key and whether it needs Shift */
static const char shift_digits[] = "!@#$%^&*()";
static const char letters[] = "qwertyuiopasdfghjklzxcvbnm";
static const unsigned char letter_keys[] = {
	KEY_Q, KEY_W, KEY_E, KEY_R, KEY_T, KEY_Y, KEY_U, KEY_I, KEY_O, KEY_P,
	KEY_A, KEY_S, KEY_D, KEY_F, KEY_G, KEY_H, KEY_J, KEY_K, KEY_L,
	KEY_Z, KEY_X, KEY_C, KEY_V, KEY_B, KEY_N, KEY_M,
};
static const struct { char plain, shifted; unsigned char code; } punct[] = {
	{ '-', '_', KEY_MINUS }, { '=', '+', KEY_EQUAL },
	{ '[', '{', KEY_LEFTBRACE }, { ']', '}', KEY_RIGHTBRACE },
	{ ';', ':', KEY_SEMICOLON }, { '\'', '"', KEY_APOSTROPHE },
	{ '`', '~', KEY_GRAVE }, { '\\', '|', KEY_BACKSLASH },
	{ ',', '<', KEY_COMMA }, { '.', '>', KEY_DOT }, { '/', '?', KEY_SLASH },
};

static int type_char(char c)
{
	const char *p;
	unsigned int i;

	if (c == ' ') {
		key(KEY_SPACE, 0);
		return 0;
	}
	if (c >= '0' && c <= '9') {
		key(c == '0' ? KEY_0 : KEY_1 + (c - '1'), 0);
		return 0;
	}
	for (p = shift_digits; *p; p++)
		if (*p == c) {
			key(KEY_1 + (p - shift_digits), 1);
			return 0;
		}
	for (p = letters; *p; p++) {
		if (*p == c) {
			key(letter_keys[p - letters], 0);
			return 0;
		}
		if (*p - 'a' + 'A' == c) {
			key(letter_keys[p - letters], 1);
			return 0;
		}
	}
	for (i = 0; i < sizeof(punct) / sizeof(punct[0]); i++) {
		if (punct[i].plain == c) {
			key(punct[i].code, 0);
			return 0;
		}
		if (punct[i].shifted == c) {
			key(punct[i].code, 1);
			return 0;
		}
	}
	return -1;
}

static int type_text(const char *s, long n)
{
	long i;

	for (i = 0; i < n; i++)
		if (type_char(s[i])) {
			printf("uinput-type: cannot type character %d\n", s[i]);
			return -1;
		}
	return 0;
}

static int type_file(const char *path)
{
	static char buf[256];
	long n;
	int fd = open(path, O_RDONLY);

	if (fd < 0) {
		printf("uinput-type: cannot open %s\n", path);
		return -1;
	}
	n = read(fd, buf, sizeof(buf));
	close(fd);
	if (n < 0)
		return -1;
	if (n > 0 && buf[n - 1] == '\n')
		n--;
	n = type_text(buf, n);
	memset(buf, 0, sizeof(buf));
	return n;
}

static int named_key(const char *name)
{
	static const struct { const char *name; int code; } keys[] = {
		{ "enter", KEY_ENTER }, { "esc", KEY_ESC }, { "space", KEY_SPACE },
		{ "tab", KEY_TAB }, { "backspace", KEY_BACKSPACE },
		{ "shift", KEY_LEFTSHIFT },
	};
	unsigned int i;

	for (i = 0; i < sizeof(keys) / sizeof(keys[0]); i++)
		if (!strcmp(name, keys[i].name))
			return keys[i].code;
	return -1;
}

int main(int argc, char **argv)
{
	struct uinput_setup us;
	int i, code;

	if (argc < 2) {
		printf("usage: uinput-type TOKEN... (@file:PATH @enter @esc @space @tab @backspace @shift @sleep:MS text)\n");
		return 1;
	}
	ufd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
	if (ufd < 0) {
		printf("uinput-type: cannot open /dev/uinput (modprobe uinput)\n");
		return 1;
	}
	ioctl(ufd, UI_SET_EVBIT, EV_KEY);
	ioctl(ufd, UI_SET_EVBIT, EV_SYN);
	for (code = KEY_ESC; code <= KEY_MICMUTE; code++)
		ioctl(ufd, UI_SET_KEYBIT, code);
	memset(&us, 0, sizeof(us));
	us.id.bustype = BUS_VIRTUAL;
	us.id.vendor = 0x1d6b;	/* Linux Foundation */
	us.id.product = 0x0104;
	strcpy(us.name, "gtactive3 test keyboard");
	if (ioctl(ufd, UI_DEV_SETUP, &us) || ioctl(ufd, UI_DEV_CREATE, 0)) {
		printf("uinput-type: cannot create the device\n");
		return 1;
	}
	/* let udev tag it and the compositor open it */
	msleep(1500);

	for (i = 1; i < argc; i++) {
		const char *t = argv[i];

		if (!strncmp(t, "@file:", 6)) {
			if (type_file(t + 6))
				break;
		} else if (!strncmp(t, "@sleep:", 7)) {
			msleep(atoi(t + 7));
		} else if (t[0] == '@' && (code = named_key(t + 1)) >= 0) {
			key(code, 0);
		} else if (type_text(t, strlen(t))) {
			break;
		}
	}

	msleep(500);
	ioctl(ufd, UI_DEV_DESTROY, 0);
	close(ufd);
	return i == argc ? 0 : 1;
}
