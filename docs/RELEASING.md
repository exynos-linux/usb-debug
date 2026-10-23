# Releasing

1. Set the new version in `VERSION` and in the APKBUILD's `pkgver`
   (`pkgrel=0`). CI fails if the two differ. List protocol or behaviour
   changes in the release notes.
2. Merge to `main` once CI is green.
3. Tag and push: `git tag -s vX.Y.Z -m "usb-debug X.Y.Z" && git push origin vX.Y.Z`.

The `release` workflow then:

- checks that the tag matches `VERSION`,
- makes `usb-debug-X.Y.Z.tar.gz` (`git archive`) and `setup.sh` with the
  version filled in,
- fills the APKBUILD's `sha512sums` for that tarball and builds the
  packages from it on Alpine edge (x86_64 and aarch64),
- builds the aarch64 binaries `uinput-type` (nolibc) and
  `usb-debug-console` (static),
- publishes the GitHub release with `SHA256SUMS` over all of these.

Device repositories then bump `.usb-debug-version` and re-run
`scripts/usb-debug aport ...` (MIGRATION.md).
