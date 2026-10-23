/* Minimal stand-in for libc's <sys/socket.h>, enough for <linux/if.h>. */
#ifndef _STUB_SYS_SOCKET_H
#define _STUB_SYS_SOCKET_H
#include <linux/socket.h>
typedef unsigned short sa_family_t;
struct sockaddr {
	sa_family_t sa_family;
	char sa_data[14];
};
#define AF_INET6 10
#endif
